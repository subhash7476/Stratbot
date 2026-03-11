#!/usr/bin/env python3
"""
V9 PM Scalper -- Paper Trading Runner
=====================================
Standalone paper trading script for the V9 BullTrend PM strategy.
Does NOT require the full TradingRunner stack.

Strategy params (confirmed from Section 13 + 14 of STRATEGY_RESEARCH_LOG.md):
  Signal:    13pm day-type model (logistic_13pm_prod v2, train_thru=2025)
  Filter:    BullTrend only, confidence >= 0.75
  Entry:     13:02 IST open price (Nifty 50 Futures proxy via index 1m bar)
  Stop:      0.30% below entry -- hard stop, checked every 1m bar
  Target:    None -- hold to time exit
  Time exit: 14:45 IST (105 minutes after 13:00)
  Cost:      0.04% round-trip (Nifty futures, all-in)

Modes:
  --live              Poll today's DuckDB for new bars in real time (default)
  --replay YYYY-MM-DD Replay a past session from the stored DuckDB
  --recap             Print trade history from the log CSV and exit

Usage:
  python scripts/run_v9_paper.py                       # live today
  python scripts/run_v9_paper.py --replay 2026-02-21   # replay past session
  python scripts/run_v9_paper.py --recap               # show trade history
  python scripts/run_v9_paper.py --replay 2026-02-21 --verbose

Output:
  Terminal: real-time dashboard (refreshes each bar)
  CSV log:  logs/v9_paper_trades.csv  (one row per completed trade)
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.state.daytype_engine import DayTypeEngine, DayTypeState

IST      = ZoneInfo("Asia/Kolkata")
SYMBOL   = "NSE_INDEX|Nifty 50"
CANDLE_DIR     = ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
LIVE_BUFFER_DB = ROOT / "data" / "live_buffer" / "candles_today.duckdb"
LOG_DIR        = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
PAPER_CSV  = LOG_DIR / "v9_paper_trades.csv"

# ── Strategy constants ────────────────────────────────────────────────────────
MIN_CONF     = 0.75   # BullTrend confidence threshold
STOP_PCT     = 0.30   # Hard stop % below entry
ROUND_TRIP   = 0.04   # Futures round-trip cost %
ENTRY_HOUR   = 13
ENTRY_MINUTE = 2      # Enter at 13:02
EXIT_HOUR    = 14
EXIT_MINUTE  = 45     # Exit at 14:45

POLL_INTERVAL_S = 30  # Seconds between DuckDB polls in live mode

# ── CSV header ────────────────────────────────────────────────────────────────
CSV_HEADER = [
    "session_date", "entry_time", "entry_price", "stop_level",
    "exit_time", "exit_price", "exit_reason",
    "confidence", "predicted_state",
    "pnl_gross_pct", "pnl_net_pct",
    "model_version",
]


# ── State machine ─────────────────────────────────────────────────────────────

@dataclass
class SessionState:
    """Tracks everything for a single trading day."""
    session_date:  date
    sm:            str   = "IDLE"        # IDLE / AWAITING_ENTRY / IN_POSITION / DONE
    day_type:      str   = "Unknown"
    confidence:    float = 0.0
    model_version: str   = ""
    entry_time:    Optional[datetime] = None
    entry_price:   Optional[float]    = None
    stop_level:    Optional[float]    = None
    exit_time:     Optional[datetime] = None
    exit_price:    Optional[float]    = None
    exit_reason:   str   = ""
    pnl_gross:     Optional[float]    = None
    pnl_net:       Optional[float]    = None
    bars_seen:     int   = 0
    last_close:    Optional[float]    = None
    last_ts:       Optional[datetime] = None


# ── DuckDB helpers ────────────────────────────────────────────────────────────

def _db_path(d: date) -> Path:
    """
    Return the DuckDB path for date d.
    For today (live mode) the market ingestor writes to the live buffer;
    historical dates live in the per-date candles archive.
    """
    today = date.today()
    if d == today:
        return LIVE_BUFFER_DB          # live_buffer/candles_today.duckdb
    return CANDLE_DIR / f"{d}.duckdb"  # historical archive


def load_bars_up_to(d: date, after_ts: Optional[datetime] = None) -> pd.DataFrame:
    """Load 1m Nifty bars from DuckDB for date d, optionally only after after_ts."""
    db = _db_path(d)
    if not db.exists():
        return pd.DataFrame()
    today = date.today()
    # Retry for up to 25s — tick aggregator holds write lock for ~10-20s per cycle
    for attempt in range(50):
        try:
            con = duckdb.connect(str(db), read_only=True)
            # Live buffer stores all timeframes; filter to 1m candles only
            tf_filter = "AND timeframe = '1m'" if d == today else ""
            if after_ts:
                ts_str = after_ts.strftime("%Y-%m-%d %H:%M:%S")
                q = (f"SELECT timestamp, open, high, low, close, volume "
                     f"FROM candles WHERE symbol = '{SYMBOL}' "
                     f"{tf_filter} "
                     f"AND timestamp > '{ts_str}' ORDER BY timestamp")
            else:
                q = (f"SELECT timestamp, open, high, low, close, volume "
                     f"FROM candles WHERE symbol = '{SYMBOL}' "
                     f"{tf_filter} ORDER BY timestamp")
            df = con.execute(q).df()
            con.close()
            if df.empty:
                return df
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            return df.sort_values("timestamp").reset_index(drop=True)
        except Exception as e:
            if attempt < 49:
                time.sleep(0.5)
            else:
                print(f"  [WARN] DuckDB locked after 25s: {e}")
    return pd.DataFrame()


# ── CSV helpers ───────────────────────────────────────────────────────────────

def ensure_csv_header() -> None:
    if not PAPER_CSV.exists():
        with open(PAPER_CSV, "w", newline="") as f:
            csv.writer(f).writerow(CSV_HEADER)


def append_trade(s: SessionState) -> None:
    ensure_csv_header()
    row = [
        str(s.session_date),
        str(s.entry_time.time())  if s.entry_time  else "",
        round(s.entry_price, 4)   if s.entry_price  is not None else "",
        round(s.stop_level, 4)    if s.stop_level   is not None else "",
        str(s.exit_time.time())   if s.exit_time   else "",
        round(s.exit_price, 4)    if s.exit_price   is not None else "",
        s.exit_reason,
        round(s.confidence, 4),
        s.day_type,
        round(s.pnl_gross, 4)     if s.pnl_gross    is not None else "",
        round(s.pnl_net, 4)       if s.pnl_net      is not None else "",
        s.model_version,
    ]
    with open(PAPER_CSV, "a", newline="") as f:
        csv.writer(f).writerow(row)


# ── Terminal display ──────────────────────────────────────────────────────────

def _clr():
    os.system("cls" if os.name == "nt" else "clear")


def _bar_str(color_code: str, text: str) -> str:
    return f"\033[{color_code}m{text}\033[0m"


def _grn(t): return _bar_str("92", t)
def _red(t): return _bar_str("91", t)
def _yel(t): return _bar_str("93", t)
def _cyn(t): return _bar_str("96", t)
def _bld(t): return _bar_str("1",  t)
def _dim(t): return _bar_str("2",  t)


def _pnl_str(pct: Optional[float]) -> str:
    if pct is None:
        return _dim("--")
    return (_grn(f"{pct:+.3f}%") if pct >= 0 else _red(f"{pct:+.3f}%"))


def _conf_str(conf: float, tier: str = "") -> str:
    s = f"{conf:.0%}"
    if conf >= 0.80:   return _grn(s)
    if conf >= 0.75:   return _cyn(s)
    return _dim(s)


def _dt_str(dt_state: str, conf: float) -> str:
    if dt_state == "BullTrend": return _grn("BullTrend")
    if dt_state == "BearTrend": return _red("BearTrend")
    if dt_state == "Choppy":    return _yel("Choppy")
    return _dim(dt_state)


def _mins_to_exit(now: datetime) -> str:
    exit_today = now.replace(hour=EXIT_HOUR, minute=EXIT_MINUTE, second=0, microsecond=0)
    delta = (exit_today - now).total_seconds()
    if delta < 0:
        return _dim("past")
    m, s = divmod(int(delta), 60)
    return f"{m}m {s:02d}s"


def print_dashboard(s: SessionState, mode: str, verbose: bool = False) -> None:
    now_ist = datetime.now(IST)
    W = 62

    print(_bld("-" * W))
    title = f"  V9 PM SCALPER  [{mode.upper()}]  {s.session_date}"
    ts_str = now_ist.strftime("%H:%M:%S IST")
    print(f"{_bld(title):<55} {_dim(ts_str)}")
    print("-" * W)

    # Day-type status
    dt_label = _dt_str(s.day_type, s.confidence)
    conf_label = _conf_str(s.confidence)
    bar_label  = f"bar {s.bars_seen}"
    print(f"  Day-type : {dt_label}  conf={conf_label}  {_dim(bar_label)}")

    # State-specific display
    if s.sm == "IDLE":
        print(f"  Status   : {_dim('Accumulating bars -- waiting for 13:00 checkpoint')}")

    elif s.sm == "AWAITING_ENTRY":
        print(f"  Status   : {_yel('Signal confirmed -- entering at 13:02 open')}")

    elif s.sm == "IN_POSITION":
        cur = s.last_close or s.entry_price
        gross = (cur - s.entry_price) / s.entry_price * 100 if s.entry_price else 0.0
        net   = gross - ROUND_TRIP
        dist  = (cur - s.stop_level) / s.stop_level * 100 if s.stop_level else 0.0

        print(f"  Status   : {_grn('IN POSITION')} (long Nifty futures)")
        print(f"  Entry    : {s.entry_price:.2f}  at {s.entry_time.strftime('%H:%M') if s.entry_time else '--'} IST")
        print(f"  Stop     : {s.stop_level:.2f}  ({dist:+.2f}% from current)")
        print(f"  Current  : {cur:.2f}  unrealised={_pnl_str(net)}")
        print(f"  Time exit: 14:45 IST  ({_mins_to_exit(now_ist)} remaining)")

    elif s.sm == "DONE":
        if s.entry_price is not None:
            reason_str = {"stop_hit": _red("STOP HIT"), "time_exit": _cyn("TIME EXIT")}.get(
                s.exit_reason, _dim(s.exit_reason))
            print(f"  Status   : {_bld('CLOSED')}  reason={reason_str}")
            print(f"  Entry    : {s.entry_price:.2f}  at {s.entry_time.strftime('%H:%M') if s.entry_time else '--'} IST")
            print(f"  Exit     : {s.exit_price:.2f}  at {s.exit_time.strftime('%H:%M') if s.exit_time else '--'} IST")
            print(f"  PnL net  : {_pnl_str(s.pnl_net)}")
        else:
            print(f"  Status   : {_dim('No trade today')}  ({s.day_type}, conf={s.confidence:.0%})")

    print("-" * W)

    if verbose and s.last_ts:
        print(_dim(f"  Last bar : {s.last_ts.strftime('%H:%M:%S')} IST  close={s.last_close:.2f}"))

    print(_dim(f"  Log      : {PAPER_CSV}"))
    print(_dim(f"  Model    : logistic_13pm_prod {s.model_version}"))
    print("-" * W)


# ── Core processing loop ──────────────────────────────────────────────────────

def process_bar(
    bar: dict,
    s: SessionState,
    engine: DayTypeEngine,
    verbose: bool,
) -> None:
    """Feed one bar through the engine and state machine. Mutates SessionState s."""
    ts  = pd.Timestamp(bar["timestamp"])
    ts_ist = ts.tz_localize(IST) if ts.tzinfo is None else ts.astimezone(IST)

    s.bars_seen += 1
    s.last_ts    = ts_ist
    s.last_close = float(bar["close"])

    # Feed bar to engine
    bar_dict = {
        "timestamp": ts_ist,
        "open":      float(bar["open"]),
        "high":      float(bar["high"]),
        "low":       float(bar["low"]),
        "close":     float(bar["close"]),
        "volume":    float(bar.get("volume", 0)),
    }
    new_state: Optional[DayTypeState] = engine.on_bar(bar_dict)

    # ── Update SessionState from engine output ────────────────────────────────
    if new_state is not None and new_state.checkpoint == "13pm":
        s.day_type      = new_state.predicted_state
        s.confidence    = new_state.confidence
        s.model_version = new_state.model_version

        if s.sm == "IDLE":
            if new_state.predicted_state == "BullTrend" and new_state.confidence >= MIN_CONF:
                s.sm = "AWAITING_ENTRY"
                if verbose:
                    print(f"\n  => SIGNAL: BullTrend conf={new_state.confidence:.0%} -- will enter at 13:02")
            else:
                s.sm = "DONE"
                if verbose:
                    print(f"\n  => SKIP: {new_state.predicted_state} conf={new_state.confidence:.0%}")

    # ── AWAITING_ENTRY: enter at 13:02 ───────────────────────────────────────
    if s.sm == "AWAITING_ENTRY":
        bar_h, bar_m = ts_ist.hour, ts_ist.minute
        if (bar_h, bar_m) >= (ENTRY_HOUR, ENTRY_MINUTE):
            s.entry_price = float(bar["open"])
            s.stop_level  = round(s.entry_price * (1 - STOP_PCT / 100), 4)
            s.entry_time  = ts_ist
            s.sm          = "IN_POSITION"
            if verbose:
                print(f"\n  => ENTER LONG @ {s.entry_price:.2f}  stop={s.stop_level:.2f}")

    # ── IN_POSITION: check stop and time exit ─────────────────────────────────
    if s.sm == "IN_POSITION":
        bar_h, bar_m = ts_ist.hour, ts_ist.minute
        lo = float(bar["low"])

        # 1. Hard stop (priority)
        if lo <= s.stop_level:
            s.exit_price  = s.stop_level
            s.exit_time   = ts_ist
            s.exit_reason = "stop_hit"
            _close_position(s)
            if verbose:
                print(f"\n  => STOP HIT @ {s.exit_price:.2f}  pnl_net={s.pnl_net:+.3f}%")
            return

        # 2. Time exit at 14:45
        if (bar_h, bar_m) >= (EXIT_HOUR, EXIT_MINUTE):
            s.exit_price  = float(bar["open"])
            s.exit_time   = ts_ist
            s.exit_reason = "time_exit"
            _close_position(s)
            if verbose:
                print(f"\n  => TIME EXIT @ {s.exit_price:.2f}  pnl_net={s.pnl_net:+.3f}%")
            return


def _close_position(s: SessionState) -> None:
    """Compute PnL, log to CSV, mark session done."""
    s.pnl_gross = (s.exit_price - s.entry_price) / s.entry_price * 100
    s.pnl_net   = s.pnl_gross - ROUND_TRIP
    s.sm        = "DONE"
    append_trade(s)


# ── Recap mode ────────────────────────────────────────────────────────────────

def show_recap() -> None:
    """Print trade history from the paper CSV."""
    if not PAPER_CSV.exists():
        print("No paper trades logged yet.")
        return

    df = pd.read_csv(PAPER_CSV)
    if df.empty:
        print("No trades found in log.")
        return

    total   = len(df)
    wins    = (df["pnl_net_pct"] > 0).sum()
    wr      = wins / total * 100 if total > 0 else 0
    avg_pnl = df["pnl_net_pct"].mean()
    cum_pnl = df["pnl_net_pct"].sum()
    max_dd  = _max_drawdown(df["pnl_net_pct"].values)

    W = 62
    print(_bld("-" * W))
    print(_bld("  V9 PM SCALPER -- PAPER TRADE RECAP"))
    print("-" * W)
    print(f"  Trades    : {total}")
    print(f"  Win rate  : {_pnl_str(wr - 50 + 0.001)}  ({wins}/{total}  {wr:.1f}%)")
    print(f"  Avg PnL   : {_pnl_str(avg_pnl)}/trade (net of {ROUND_TRIP}% cost)")
    print(f"  Cum PnL   : {_pnl_str(cum_pnl)}")
    print(f"  Max DD    : {_red(f'{max_dd:.2f}%')}")
    print("-" * W)

    print(f"\n  {'Date':<12}  {'Entry':>9}  {'Exit':>9}  {'Reason':<12}  {'Conf':>6}  {'PnL net':>9}")
    print(f"  {'-'*12}  {'-'*9}  {'-'*9}  {'-'*12}  {'-'*6}  {'-'*9}")
    for _, row in df.iterrows():
        reason_str = {"stop_hit": _red("stop_hit"), "time_exit": _cyn("time_exit")}.get(
            str(row.get("exit_reason", "")), str(row.get("exit_reason", ""))
        )
        pnl_s = _pnl_str(row.get("pnl_net_pct"))
        conf_s = f"{row.get('confidence', 0):.0%}"
        print(f"  {str(row.get('session_date','')):<12}  "
              f"{str(row.get('entry_price',''))[:9]:>9}  "
              f"{str(row.get('exit_price', ''))[:9]:>9}  "
              f"{reason_str:<22}  "
              f"{conf_s:>6}  "
              f"{pnl_s}")

    print("-" * W)
    print(_dim(f"  Source: {PAPER_CSV}"))


def _max_drawdown(pnl_series) -> float:
    """Max drawdown from cumulative PnL series."""
    if len(pnl_series) == 0:
        return 0.0
    cum  = [0.0]
    for p in pnl_series:
        cum.append(cum[-1] + p)
    peak = cum[0]
    max_dd = 0.0
    for v in cum:
        peak = max(peak, v)
        dd   = peak - v
        max_dd = max(max_dd, dd)
    return max_dd


# ── Live mode ─────────────────────────────────────────────────────────────────

def run_live(verbose: bool) -> None:
    """Real-time paper trading -- polls DuckDB every 30s for new bars."""
    today = date.today()
    ensure_csv_header()

    print(_bld(f"\n  V9 PM SCALPER -- LIVE MODE  {today}"))
    print(_dim(f"  Polling {LIVE_BUFFER_DB} every {POLL_INTERVAL_S}s"))
    print(_dim(f"  Press Ctrl+C to quit\n"))

    # lock_threshold=1.01: disables automatic state locking so all 3 checkpoints
    # (10am, 11am, 13pm) always fire.  With the default threshold (0.70) the engine
    # locks at 10am on high-confidence days, preventing the 13pm prediction from
    # ever being emitted, which is the only signal V9 trades on.
    engine = DayTypeEngine(lock_threshold=1.01)
    engine.reset(today)
    s = SessionState(session_date=today)

    last_ts: Optional[datetime] = None

    try:
        while True:
            # Load bars since last processed
            df = load_bars_up_to(today, after_ts=last_ts)

            if not df.empty:
                for _, row in df.iterrows():
                    process_bar(row.to_dict(), s, engine, verbose)
                    last_ts = pd.Timestamp(row["timestamp"])

            # Refresh display
            _clr()
            print_dashboard(s, mode="LIVE", verbose=verbose)

            # Stop polling after position is closed or market is done
            if s.sm == "DONE" and s.bars_seen > 300:
                if not verbose:
                    print(_dim("\n  Session complete. Press Ctrl+C to exit."))

            time.sleep(POLL_INTERVAL_S)

    except KeyboardInterrupt:
        print(f"\n\n  Stopped. Trades logged to {PAPER_CSV}")
        if s.pnl_net is not None:
            print(f"  Today's result: {_pnl_str(s.pnl_net)}")


# ── Replay mode ───────────────────────────────────────────────────────────────

def run_replay(replay_date: date, verbose: bool) -> None:
    """Replay a past session from stored DuckDB bars."""
    ensure_csv_header()

    print(_bld(f"\n  V9 PM SCALPER -- REPLAY MODE  {replay_date}"))

    df = load_bars_up_to(replay_date)
    if df.empty:
        print(_red(f"\n  ERROR: No bars found for {replay_date}"))
        print(_dim(f"  Expected: {_db_path(replay_date)}"))
        sys.exit(1)

    print(_dim(f"  Loaded {len(df)} bars from DuckDB"))
    print(_dim(f"  Press Ctrl+C to abort\n"))
    time.sleep(1)

    # lock_threshold=1.01: disables locking so all 3 checkpoints always fire
    # (see run_live() comment for full explanation)
    engine = DayTypeEngine(lock_threshold=1.01)
    engine.reset(replay_date)
    s = SessionState(session_date=replay_date)

    try:
        for _, row in df.iterrows():
            process_bar(row.to_dict(), s, engine, verbose)
            ts = pd.Timestamp(row["timestamp"])
            ts_ist = ts.tz_localize(IST) if ts.tzinfo is None else ts.astimezone(IST)
            h, m = ts_ist.hour, ts_ist.minute

            # Only refresh display in key windows (saves terminal spam)
            if m == 0 or (h == 13 and m <= 10) or s.sm in ("AWAITING_ENTRY", "IN_POSITION", "DONE"):
                _clr()
                print_dashboard(s, mode=f"REPLAY {replay_date}", verbose=verbose)
                time.sleep(0.05)   # brief pause for readability

            # Stop early if session closed and past 15:30
            if s.sm == "DONE" and h >= 15 and m >= 30:
                break

    except KeyboardInterrupt:
        print("\n  Replay aborted.")
        return

    # Final display
    _clr()
    print_dashboard(s, mode=f"REPLAY {replay_date}", verbose=verbose)

    print()
    if s.entry_price is not None:
        result_str = _pnl_str(s.pnl_net)
        reason_str = s.exit_reason.replace("_", " ").upper()
        print(f"  RESULT: {reason_str}  pnl_net={result_str}")
        print(f"  Logged to {PAPER_CSV}")
    else:
        print(f"  No trade -- {s.day_type} conf={s.confidence:.0%}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="V9 PM Scalper -- paper trading runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--live",   action="store_true", default=True,
        help="Live mode: poll today's DuckDB every 30s (default)",
    )
    mode_group.add_argument(
        "--replay", metavar="YYYY-MM-DD",
        help="Replay a past session date",
    )
    mode_group.add_argument(
        "--recap",  action="store_true",
        help="Print trade history from log CSV and exit",
    )
    parser.add_argument("--verbose", action="store_true", help="Show all bar events")
    args = parser.parse_args()

    if args.recap:
        show_recap()
        return

    if args.replay:
        try:
            replay_date = date.fromisoformat(args.replay)
        except ValueError:
            print(f"  ERROR: Invalid date '{args.replay}'. Use YYYY-MM-DD format.")
            sys.exit(1)
        run_replay(replay_date, verbose=args.verbose)
        return

    run_live(verbose=args.verbose)


if __name__ == "__main__":
    main()
