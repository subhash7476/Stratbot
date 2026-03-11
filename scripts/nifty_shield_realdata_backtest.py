#!/usr/bin/env python3
"""
NiftyShield Real-Data Backtest -- 25-27 Feb 2026
-------------------------------------------------
Downloads actual NIFTY02MAR26 option 1m candles from Upstox (in-memory,
not saved to disk), replays them through NiftyShieldStrategy, and compares
real-premium results against synthetic Black-76 pricing.

Key finding: Nifty 50 weekly expiry for the Feb 25 week is 02 MAR 2026 (Monday).
The OptionsContractSelector uses Tuesday (weekday 1), which would generate
03MAR2026. We correct for this by looking up prices by (strike, opt_type) only,
so real data is fetched for the actual market expiry regardless.

Run:
    python scripts/nifty_shield_realdata_backtest.py

Requirements:
    - Valid access_token in config/credentials.json  (must be recent / today's)
    - Local 1m Nifty data for 25-27 Feb in data/market_data/nse/candles/1m/
"""
import sys
import gzip
import json
import logging
import requests
import duckdb
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.auth.credentials import credentials
from core.api.upstox_client import UpstoxClient
from core.strategies.nifty_shield_strategy import NiftyShieldStrategy, NF_SYMBOL
from core.risk.greeks.black76_engine import Black76Engine

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
# Suppress expected NullDB errors from VIX query attempts (VIX is overridden manually)
logging.getLogger("core.database.queries").setLevel(logging.CRITICAL)

IST       = ZoneInfo("Asia/Kolkata")

# Backtest window
SESSIONS  = [date(2026, 2, 25), date(2026, 2, 26), date(2026, 2, 27)]
FROM_DATE = "2026-02-25"
TO_DATE   = "2026-02-27"

# Actual market expiry for Nifty weekly options in the Feb 25 week
ACTUAL_EXPIRY     = date(2026, 3, 2)   # Monday -- confirmed from Upstox master
ACTUAL_EXPIRY_UTC = datetime(2026, 3, 2, 18, 29, 59, tzinfo=timezone.utc)

# Strikes to download: ATM coverage for all 3 sessions
#   25-Feb ATM=25450, 26-Feb ATM=25450, 27-Feb ATM=25300
#   Download 25150-25650 to cover entries + adjustment range
TARGET_STRIKES = [25150, 25200, 25250, 25300, 25350, 25400,
                  25450, 25500, 25550, 25600, 25650]

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"

# VIX closes: use PREVIOUS session's daily close (morning gate)
#   24-Feb VIX=14.15, 25-Feb VIX=13.49, 26-Feb VIX=13.06
VIX_BY_DATE = {
    date(2026, 2, 25): 14.15,
    date(2026, 2, 26): 13.49,
    date(2026, 2, 27): 13.06,
}


# ---- Null DB stub (no persistence for this standalone backtest) -----------

class _Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def execute(self, *a, **kw): pass

class _NullDB:
    def trading_writer(self): return _Ctx()


# ---- Strategy subclasses --------------------------------------------------

class RealDataStrategy(NiftyShieldStrategy):
    """NiftyShield with live option prices injected from a pre-fetched lookup."""

    def __init__(self, price_lookup: Dict[Tuple, float]):
        super().__init__(_NullDB(), backtest_mode=True)
        # Keys: (date_str "YYYY-MM-DD", "HH:MM", strike_int, opt_type "CE"/"PE")
        self._price_lookup = price_lookup
        self._current_bar_ts: Optional[datetime] = None

    def on_session_start(self, session_date: date):
        super().on_session_start(session_date)
        self._vix_close = VIX_BY_DATE.get(session_date)

    def on_bar(self, bar: dict):
        self._current_bar_ts = bar["timestamp"]
        super().on_bar(bar)

    def _option_price(self, F: float, K: float, T: float, r: float,
                      iv: float, opt_type: str) -> float:
        ts = self._current_bar_ts
        if ts is not None:
            if ts.tzinfo is None:
                ts_ist = ts.replace(tzinfo=IST)
            else:
                ts_ist = ts.astimezone(IST)
            key = (str(ts_ist.date()), ts_ist.strftime("%H:%M"),
                   int(round(K)), opt_type)
            price = self._price_lookup.get(key)
            if price and price > 0:
                return price
        # Fallback to synthetic if key not found (e.g. low-liquidity bar)
        return Black76Engine.calculate_price(F, K, T, r, iv, opt_type)


class SyntheticStrategy(NiftyShieldStrategy):
    """NiftyShield with Black-76 synthetic pricing only (baseline)."""

    def __init__(self):
        super().__init__(_NullDB(), backtest_mode=True)

    def on_session_start(self, session_date: date):
        super().on_session_start(session_date)
        self._vix_close = VIX_BY_DATE.get(session_date)


# ---- Instrument master download -------------------------------------------

def build_instrument_key_map() -> Dict[Tuple[int, str], str]:
    """
    Download Upstox instrument master in-memory and return:
        {(strike_int, opt_type): numeric_instrument_key}
    for NIFTY options expiring on ACTUAL_EXPIRY (2026-03-02).
    """
    print("Downloading Upstox instrument master (in-memory, ~8 MB)...")
    resp = requests.get(INSTRUMENTS_URL, timeout=60)
    resp.raise_for_status()
    instruments = json.loads(gzip.decompress(resp.content))
    print(f"  Total instruments: {len(instruments):,}")

    # Target expiry window: +/- 12 hours of ACTUAL_EXPIRY_UTC
    target_ts_ms = ACTUAL_EXPIRY_UTC.timestamp() * 1000
    window_ms    = 43_200_000   # 12 hours

    result: Dict[Tuple[int, str], str] = {}
    for item in instruments:
        if (item.get("segment") or item.get("exchange", "")) != "NSE_FO":
            continue
        if item.get("name") != "NIFTY":
            continue
        exp_ms = item.get("expiry", 0)
        if abs(exp_ms - target_ts_ms) > window_ms:
            continue

        strike   = int(item.get("strike_price", 0))
        opt_type = str(item.get("instrument_type", "")).upper()
        ikey     = item.get("instrument_key", "")

        if strike and opt_type in ("CE", "PE") and ikey:
            result[(strike, opt_type)] = ikey

    print(f"  NIFTY {ACTUAL_EXPIRY} options found: {len(result)} contracts")
    return result


# ---- Fetch real 1m option candles ----------------------------------------

def fetch_option_candles(
    client: UpstoxClient,
    key_map: Dict[Tuple[int, str], str],
    strikes: List[int],
) -> Dict[Tuple, float]:
    """
    Fetch 1m close prices for each (strike, opt_type) combination.
    Returns price_lookup: {(date_str, "HH:MM", strike_int, opt_type): close}
    """
    price_lookup: Dict[Tuple, float] = {}
    pairs = [(s, t) for s in strikes for t in ("CE", "PE")]
    total = len(pairs)
    fetched = 0
    missing = []

    for strike, opt_type in pairs:
        ikey = key_map.get((strike, opt_type))
        if not ikey:
            missing.append(f"{strike}{opt_type}")
            continue

        try:
            candles = client.fetch_historical_candles_v3(
                instrument_key=ikey,
                unit="minutes",
                interval=1,
                to_date=TO_DATE,
                from_date=FROM_DATE,
            )
            for c in candles:
                ts = c["timestamp"]
                if ts.tzinfo is None:
                    ts_ist = ts.replace(tzinfo=IST)
                else:
                    ts_ist = ts.astimezone(IST)
                key = (str(ts_ist.date()), ts_ist.strftime("%H:%M"),
                       strike, opt_type)
                price_lookup[key] = float(c["close"])
            fetched += 1
            sym_name = f"NIFTY02MAR26{strike}{opt_type}"
            print(f"  [{fetched}/{total}] {sym_name}: {len(candles)} bars")
        except Exception as exc:
            print(f"  WARN: {strike}{opt_type} fetch failed: {exc}")

        time.sleep(0.12)   # stay under 8 req/s limit

    if missing:
        print(f"  WARN: {len(missing)} strikes not in master: {missing[:5]}")
    print(f"  Price lookup: {len(price_lookup):,} entries across "
          f"{FROM_DATE} to {TO_DATE}")
    return price_lookup


# ---- Load local Nifty 1m bars --------------------------------------------

def load_nifty_bars(session_date: date) -> List[dict]:
    db_path = (ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
               / f"{session_date}.duckdb")
    if not db_path.exists():
        print(f"  WARN: No 1m data for {session_date}")
        return []

    date_str = str(session_date)
    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(
        "SELECT timestamp, open, high, low, close, volume "
        "FROM candles WHERE symbol=? AND CAST(timestamp AS VARCHAR) LIKE ? "
        "ORDER BY timestamp",
        [NF_SYMBOL, f"{date_str}%"]
    ).fetchall()
    con.close()

    bars = []
    for r in rows:
        ts = r[0]
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        bars.append({
            "timestamp": ts,
            "open": float(r[1]), "high": float(r[2]),
            "low":  float(r[3]), "close": float(r[4]),
            "volume": float(r[5]),
        })
    return bars


# ---- Run one session through a strategy ----------------------------------

def run_session(strategy: NiftyShieldStrategy, session_date: date,
                bars: List[dict]) -> Optional[dict]:
    strategy.on_session_start(session_date)
    for bar in bars:
        strategy.on_bar(bar)
    result = strategy.get_session_result()
    # Enrich with raw entry/exit premiums for display
    if result is not None:
        result["_ce_entry"] = strategy._ce_entry
        result["_pe_entry"] = strategy._pe_entry
        result["_ce_exit"]  = strategy._ce_exit
        result["_pe_exit"]  = strategy._pe_exit
    return result


# ---- Coverage check -------------------------------------------------------

def check_coverage(price_lookup: Dict[Tuple, float]):
    print("\n  Coverage spot-check (13:05 IST):")
    for d in [str(s) for s in SESSIONS]:
        for strike in [25300, 25400, 25450]:
            for ot in ("CE", "PE"):
                val = price_lookup.get((d, "13:05", strike, ot))
                tag = f"{val:.1f}" if val else "MISSING"
                print(f"    {d}  {strike:>6}{ot}  @13:05 -> {tag}")


# ---- Pretty printer -------------------------------------------------------

def fmt(v, spec=".1f"):
    return f"{v:{spec}}" if v is not None else "N/A"


def print_report(real_results: List[Optional[dict]],
                 synth_results: List[Optional[dict]]):
    SEP  = "=" * 105
    DASH = "-" * 105
    print(f"\n{SEP}")
    print("  NIFTYSHIELD REAL-DATA vs SYNTHETIC BACKTEST  |  25-27 Feb 2026  |  Expiry: NIFTY02MAR26")
    print(f"{SEP}")
    hdr = (
        f"{'Date':<12} {'DayType':<12} {'VIX':>6} {'Lots':>5} {'ATM':>7}  "
        f"| {'Real CE+PE':>11} {'Exit':>15} {'Real PnL Rs':>12}  "
        f"| {'Syn CE+PE':>10} {'Syn PnL Rs':>11}"
    )
    print(hdr)
    print(DASH)

    total_real  = 0.0
    total_synth = 0.0
    trades_real  = 0
    trades_synth = 0

    for i, sd in enumerate(SESSIONS):
        rr = real_results[i]
        sr = synth_results[i]

        if rr is None and sr is None:
            print(f"{str(sd):<12} {'-- no trade --'}")
            continue

        src       = rr or sr
        day_type  = src.get("day_type", "?")
        vix       = src.get("vix_close")
        lots      = src.get("lots", 0)
        strike    = src.get("ce_strike")

        # Real
        r_ce   = rr.get("_ce_entry") if rr else None
        r_pe   = rr.get("_pe_entry") if rr else None
        r_prem = (r_ce + r_pe) if (r_ce and r_pe) else rr.get("total_premium") if rr else None
        r_exit = rr.get("exit_reason", "--") if rr else "--"
        r_pnl  = rr.get("pnl_net_rs")  if rr else None

        # Synthetic
        s_ce   = sr.get("_ce_entry") if sr else None
        s_pe   = sr.get("_pe_entry") if sr else None
        s_prem = (s_ce + s_pe) if (s_ce and s_pe) else sr.get("total_premium") if sr else None
        s_pnl  = sr.get("pnl_net_rs")  if sr else None

        if r_pnl is not None:
            total_real  += r_pnl
            trades_real += 1
        if s_pnl is not None:
            total_synth += s_pnl
            trades_synth += 1

        print(
            f"{str(sd):<12} {day_type:<12} "
            f"{fmt(vix, '.2f'):>6} {lots:>5} {fmt(strike, '.0f'):>7}  "
            f"| {fmt(r_prem):>11} {r_exit:>15} {fmt(r_pnl, '+,.0f'):>12}  "
            f"| {fmt(s_prem):>10} {fmt(s_pnl, '+,.0f'):>11}"
        )

    print(DASH)
    print(
        f"{'TOTAL':<12} {'':<12} {'':<6} {'':<5} {'':<7}  "
        f"| {'':<11} {f'{trades_real} trades':>15} {fmt(total_real, '+,.0f'):>12}  "
        f"| {'':<10} {fmt(total_synth, '+,.0f'):>11}"
    )

    delta = total_real - total_synth
    print(f"\n  Real vs Synthetic gap: {fmt(delta, '+,.0f')} Rs net "
          f"({'real higher' if delta > 0 else 'synthetic higher'})\n")

    # Per-day drill-down
    print(f"\n  {'='*60}")
    print("  ENTRY/EXIT PREMIUM DETAIL")
    print(f"  {'='*60}")
    for i, sd in enumerate(SESSIONS):
        rr = real_results[i]
        sr = synth_results[i]
        print(f"\n  {sd}:")
        if rr:
            print(
                f"    REAL   CE {fmt(rr.get('_ce_entry'))} -> {fmt(rr.get('_ce_exit'))} | "
                f"PE {fmt(rr.get('_pe_entry'))} -> {fmt(rr.get('_pe_exit'))} | "
                f"exit={rr.get('exit_reason')} | net Rs {fmt(rr.get('pnl_net_rs',0), '+,.0f')}"
            )
        else:
            print("    REAL   -- no trade --")
        if sr:
            print(
                f"    SYNTH  CE {fmt(sr.get('_ce_entry'))} -> {fmt(sr.get('_ce_exit'))} | "
                f"PE {fmt(sr.get('_pe_entry'))} -> {fmt(sr.get('_pe_exit'))} | "
                f"exit={sr.get('exit_reason')} | net Rs {fmt(sr.get('pnl_net_rs',0), '+,.0f')}"
            )
        else:
            print("    SYNTH  -- no trade --")
    print()


# ---- Main -----------------------------------------------------------------

def main():
    # 1. Auth
    token = credentials.get("access_token")
    if not token:
        print("ERROR: No access_token in config/credentials.json")
        sys.exit(1)
    client = UpstoxClient(token)

    # 2. Build instrument key map from master (in-memory)
    key_map = build_instrument_key_map()
    if not key_map:
        print("ERROR: No NIFTY options found for", ACTUAL_EXPIRY)
        sys.exit(1)
    print(f"  ATM 25450 CE -> {key_map.get((25450, 'CE'), 'NOT FOUND')}")
    print(f"  ATM 25300 CE -> {key_map.get((25300, 'CE'), 'NOT FOUND')}")

    # 3. Fetch 1m option candles (real market data)
    print(f"\nFetching 1m option candles for {len(TARGET_STRIKES)} strikes x 2 ...")
    price_lookup = fetch_option_candles(client, key_map, TARGET_STRIKES)

    # 4. Coverage check
    check_coverage(price_lookup)

    # 5. Load Nifty index bars from local DuckDB
    print("\nLoading local Nifty 1m bars...")
    bars_by_date: Dict[date, List[dict]] = {}
    for sd in SESSIONS:
        bars = load_nifty_bars(sd)
        bars_by_date[sd] = bars
        if bars:
            nifty_13h05 = [b for b in bars if b["timestamp"].hour == 13
                           and b["timestamp"].minute == 5]
            atm_price = nifty_13h05[0]["close"] if nifty_13h05 else "?"
        else:
            atm_price = "NO DATA"
        print(f"  {sd}: {len(bars)} bars  |  13:05 close = {atm_price}")

    # 6. Run both strategies day by day
    print("\nRunning backtests...")
    real_results:  List[Optional[dict]] = []
    synth_results: List[Optional[dict]] = []

    for sd in SESSIONS:
        bars = bars_by_date.get(sd, [])
        if not bars:
            real_results.append(None)
            synth_results.append(None)
            continue

        real_strat  = RealDataStrategy(price_lookup)
        synth_strat = SyntheticStrategy()

        rr = run_session(real_strat,  sd, bars)
        sr = run_session(synth_strat, sd, bars)

        r_pnl_str = fmt(rr.get("pnl_net_rs"), "+,.0f") if rr else "no trade"
        s_pnl_str = fmt(sr.get("pnl_net_rs"), "+,.0f") if sr else "no trade"
        print(f"  {sd}  real={r_pnl_str} Rs  synth={s_pnl_str} Rs")

        real_results.append(rr)
        synth_results.append(sr)

    # 7. Print comparison report
    print_report(real_results, synth_results)


if __name__ == "__main__":
    main()
