"""
PixityAI v8 — Intraday Volatility Expansion
=============================================
Hypothesis: Intraday compression -> expansion during high participation ->
continuation into session close. Microstructure / order-flow imbalance.

PIPELINE:
  1. Universe:    Top 60 by liquidity (frozen per walk-forward pair)
  2. Timeframe:   15-minute bars (aggregated from 1m)
  3. Session:     Entries 10:15–14:45. Force exit 15:20. No overnight.
  4. Setup:
       a. Opening range (9:15–10:15) is tight   <= 0.6 x 20d median first-hour range
       b. Intraday ATR(5 x 15m) <= 0.8 x 20d median intraday ATR
       c. Breakout bar: close > OR_high (long) or close < OR_low (short)
          AND 15m volume >= 1.5 x 20d median volume for that time slot
       d. Nifty 15m EMA20 > EMA50 for longs / EMA20 < EMA50 for shorts
  5. Entry:       Close of breakout bar (next bar open in simulation)
  6. Stop:        1.2 x 15m ATR(14) below entry (long) / above entry (short)
  7. Target:      +2R fixed
  8. Exit:        Stop / Target / 15:20 forced flat

PORTFOLIO:
  Risk per trade: 0.5% of equity
  Max 3 concurrent positions
  No re-entry same symbol same day

WALK-FORWARD:
  TRAIN-A: 2023-01-02 to 2023-12-29
  TEST-A:  2024-01-02 to 2024-12-31
  TRAIN-B: 2025-01-02 to 2025-07-31
  TEST-B:  2025-08-01 to 2026-02-13

FEES:
  Brokerage Rs 20 x2, STT 0.025% sell, Exchange 0.00345%, Stamp 0.003% buy.
  (Same NSE equity fee model — intraday = delivery rates for simplicity)

Usage:
    python scripts/run_v8_backtest.py
    python scripts/run_v8_backtest.py --debug-trades
    python scripts/run_v8_backtest.py --long-only
    python scripts/run_v8_backtest.py --short-only
"""

from __future__ import annotations

import os, sys, logging, argparse
import numpy as np
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import duckdb
import pandas as pd

os.environ["PYTHONIOENCODING"] = "utf-8"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — LOCKED, do not change
# ---------------------------------------------------------------------------
OR_MINUTES          = 60       # opening range = first 60 min (9:15–10:15)
OR_COMPRESSION      = 0.6      # OR high-low <= 0.6 x 20d median first-hour range
ATR_COMPRESSION     = 0.8      # intraday ATR(5) <= 0.8 x 20d median intraday ATR
VOL_MULT            = 1.5      # breakout bar volume >= 1.5 x 20d median time-slot volume
STOP_ATR_MULT       = 1.2      # stop = 1.2 x ATR(14) intraday
TARGET_R            = 2.0      # target = 2R
RISK_PCT            = 0.005    # 0.5% risk per trade
MAX_POSITIONS       = 3
ENTRY_CUTOFF        = time(14, 45)   # no new entries after 14:45
FORCE_EXIT          = time(15, 20)   # force flat at 15:20
ATR_PERIOD          = 14       # ATR period for stop calculation
LOOKBACK_DAYS       = 20       # historical lookback for medians

# Fee model
STT_RATE      = 0.00025
BROKERAGE     = 20.0
EXCHANGE_RATE = 0.0000345
STAMP_RATE    = 0.00003


def compute_fees(entry: float, exit_p: float, qty: int, direction: str) -> float:
    buy_val  = entry  * qty if direction == "LONG" else exit_p * qty
    sell_val = exit_p * qty if direction == "LONG" else entry  * qty
    turnover = buy_val + sell_val
    return (sell_val * STT_RATE) + (BROKERAGE * 2) + (turnover * EXCHANGE_RATE) + (buy_val * STAMP_RATE)


# ---------------------------------------------------------------------------
# Trading day detection
# ---------------------------------------------------------------------------

def get_trading_days(data_root: Path, from_date: date, to_date: date) -> List[date]:
    m1_dir = data_root / "market_data" / "nse" / "candles" / "1m"
    days, current = [], from_date
    while current <= to_date:
        if (m1_dir / f"{current.isoformat()}.duckdb").exists():
            days.append(current)
        current += timedelta(days=1)
    return sorted(days)


# ---------------------------------------------------------------------------
# 15m bar aggregation from 1m
# ---------------------------------------------------------------------------

def load_15m_day(
    db_path: Path,
    symbols: Set[str],
) -> Dict[str, pd.DataFrame]:
    """
    Load one day's 1m data and aggregate to 15m bars.
    Returns {symbol -> DataFrame(bar_time, open, high, low, close, volume)}
    bar_time = start of 15m bar (datetime), e.g. 09:15, 09:30, ...
    """
    if not db_path.exists():
        return {}
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        # Pull only the symbols we care about in one query
        sym_list = ", ".join(f"'{s}'" for s in symbols)
        df = conn.execute(f"""
            SELECT
                symbol,
                timestamp,
                open, high, low, close, volume
            FROM candles
            WHERE timeframe = '1m'
              AND symbol IN ({sym_list})
            ORDER BY symbol, timestamp
        """).df()
        conn.close()
    except Exception as e:
        logger.debug(f"Error loading {db_path}: {e}")
        return {}

    if df.empty:
        return {}

    # Align to 15m buckets: floor(minute / 15) * 15
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["bar_time"]  = df["timestamp"].dt.floor("15min")

    result: Dict[str, pd.DataFrame] = {}
    for sym, grp in df.groupby("symbol"):
        bars = (grp.groupby("bar_time")
                .agg(open   = ("open",   "first"),
                     high   = ("high",   "max"),
                     low    = ("low",    "min"),
                     close  = ("close",  "last"),
                     volume = ("volume", "sum"))
                .reset_index()
                .sort_values("bar_time")
                .reset_index(drop=True))
        result[sym] = bars
    return result


# ---------------------------------------------------------------------------
# Historical context: precompute per-symbol per-day stats needed for filters
# ---------------------------------------------------------------------------

def build_historical_context(
    data_root: Path,
    symbols:   Set[str],
    trading_days: List[date],
    lookback: int = LOOKBACK_DAYS,
) -> Dict[str, Dict[date, dict]]:
    """
    For each symbol, for each date D, compute the 20-day lookback medians:
      - median_first_hour_range:  median (first-hour high - first-hour low) over prior 20 days
      - median_intraday_atr:      median of per-day ATR(14) on 15m bars over prior 20 days
      - median_vol_by_slot:       {time_slot -> median volume} over prior 20 days
      - daily_close:              close of day D (for Nifty EMA, not needed per-stock)

    Returns: {symbol -> {date -> context_dict}}
    """
    logger.info(f"Building historical context for {len(symbols)} symbols "
                f"over {len(trading_days)} days...")

    m1_dir = data_root / "market_data" / "nse" / "candles" / "1m"

    # Cache 15m bar data per day per symbol to avoid re-loading
    # day_data[date] = {symbol -> 15m DataFrame}
    day_data: Dict[date, Dict[str, pd.DataFrame]] = {}

    for td in trading_days:
        db_path = m1_dir / f"{td.isoformat()}.duckdb"
        day_data[td] = load_15m_day(db_path, symbols)

    logger.info("15m data loaded. Computing context windows...")

    context: Dict[str, Dict[date, dict]] = {s: {} for s in symbols}

    for sym in symbols:
        for i, td in enumerate(trading_days):
            # Need lookback days of history BEFORE td
            prior_days = [trading_days[j] for j in range(max(0, i - lookback), i)]
            if len(prior_days) < 5:   # need at least 5 days for meaningful stats
                continue

            # Gather prior-day stats
            first_hour_ranges = []
            intraday_atrs     = []
            vol_by_slot: Dict[str, List[float]] = defaultdict(list)

            for pd_date in prior_days:
                bars = day_data.get(pd_date, {}).get(sym)
                if bars is None or bars.empty:
                    continue

                # First-hour range: bars from 09:15 to 10:00 (4 bars: 9:15,9:30,9:45,10:00)
                fh = bars[bars["bar_time"].dt.time < time(10, 15)]
                if len(fh) >= 3:
                    first_hour_ranges.append(float(fh["high"].max() - fh["low"].min()))

                # Intraday ATR(14) on 15m bars (Wilder)
                if len(bars) >= ATR_PERIOD + 1:
                    tr = pd.concat([
                        bars["high"] - bars["low"],
                        (bars["high"] - bars["close"].shift(1)).abs(),
                        (bars["low"]  - bars["close"].shift(1)).abs(),
                    ], axis=1).max(axis=1)
                    atr = float(tr.ewm(alpha=1/ATR_PERIOD, min_periods=ATR_PERIOD,
                                       adjust=False).mean().iloc[-1])
                    if not np.isnan(atr) and atr > 0:
                        intraday_atrs.append(atr)

                # Volume by time slot
                for _, row in bars.iterrows():
                    slot = row["bar_time"].strftime("%H:%M")
                    vol_by_slot[slot].append(float(row["volume"]))

            if not first_hour_ranges or not intraday_atrs:
                continue

            # Median volume per slot
            median_vol_slot = {slot: float(np.median(vols))
                               for slot, vols in vol_by_slot.items()
                               if vols}

            context[sym][td] = {
                "median_first_hour_range": float(np.median(first_hour_ranges)),
                "median_intraday_atr":     float(np.median(intraday_atrs)),
                "median_vol_by_slot":      median_vol_slot,
            }

    loaded = sum(len(v) for v in context.values())
    logger.info(f"Context built: {loaded} symbol-day entries")
    return context


# ---------------------------------------------------------------------------
# Nifty 15m loader (for intraday EMA filter)
# ---------------------------------------------------------------------------

def load_nifty_15m_day(db_path: Path) -> Optional[pd.DataFrame]:
    """Load Nifty 50 15m bars for one day."""
    if not db_path.exists():
        return None
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        df = conn.execute("""
            SELECT timestamp, open, high, low, close, volume
            FROM candles
            WHERE symbol = 'NSE_INDEX|Nifty 50' AND timeframe = '1m'
            ORDER BY timestamp
        """).df()
        conn.close()
    except Exception:
        return None

    if df.empty:
        return None

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["bar_time"]  = df["timestamp"].dt.floor("15min")
    bars = (df.groupby("bar_time")
             .agg(open   = ("open",   "first"),
                  high   = ("high",   "max"),
                  low    = ("low",    "min"),
                  close  = ("close",  "last"),
                  volume = ("volume", "sum"))
             .reset_index()
             .sort_values("bar_time")
             .reset_index(drop=True))
    return bars


# ---------------------------------------------------------------------------
# Universe selection (reuse v7 pattern)
# ---------------------------------------------------------------------------

def select_universe(
    data_root:  Path,
    symbol_map: Dict[str, str],
    as_of_days: List[date],   # last 60 days up to rank date
    top_n:      int = 60,
) -> List[str]:
    """Rank by median daily traded value over as_of_days."""
    m1_dir = data_root / "market_data" / "nse" / "candles" / "1m"
    sym_vol: Dict[str, List[float]] = defaultdict(list)

    for td in as_of_days[-60:]:   # use at most last 60 days
        db_path = m1_dir / f"{td.isoformat()}.duckdb"
        if not db_path.exists():
            continue
        try:
            conn = duckdb.connect(str(db_path), read_only=True)
            df = conn.execute("""
                SELECT symbol, SUM(close * volume) as traded_val
                FROM candles
                WHERE timeframe = '1m'
                GROUP BY symbol
            """).df()
            conn.close()
            for _, row in df.iterrows():
                s = row["symbol"]
                if s in symbol_map:
                    sym_vol[s].append(float(row["traded_val"]))
        except Exception:
            pass

    liq = [(s, float(np.median(vals)) / 1e7)
           for s, vals in sym_vol.items() if vals]
    liq.sort(key=lambda x: x[1], reverse=True)
    top = [s for s, _ in liq[:top_n]]

    if liq:
        logger.info(f"Universe: top {len(top)} | "
                    f"#{1} {symbol_map.get(liq[0][0], liq[0][0])} {liq[0][1]:.0f}Cr | "
                    f"#{top_n} {symbol_map.get(liq[len(top)-1][0], liq[len(top)-1][0])} "
                    f"{liq[len(top)-1][1]:.0f}Cr")
    return top


# ---------------------------------------------------------------------------
# ATR helper for 15m bars
# ---------------------------------------------------------------------------

def atr14_on_bars(bars: pd.DataFrame) -> float:
    """Wilder ATR(14) on a 15m bar DataFrame. Returns latest value."""
    if len(bars) < ATR_PERIOD + 1:
        return float("nan")
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - bars["close"].shift(1)).abs(),
        (bars["low"]  - bars["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    val = float(tr.ewm(alpha=1/ATR_PERIOD, min_periods=ATR_PERIOD,
                       adjust=False).mean().iloc[-1])
    return val if not np.isnan(val) else float("nan")


# ---------------------------------------------------------------------------
# Trade state machine — intraday
# ---------------------------------------------------------------------------

class Trade:
    """
    Intraday v8 trade. Long or Short.
    Stop:   1.2 x ATR below entry (long) / above entry (short)
    Target: entry + 2 x stop_dist (long) / entry - 2 x stop_dist (short)
    Force:  exit at 15:20
    """

    def __init__(
        self,
        symbol:         str,
        trading_symbol: str,
        direction:      str,        # "LONG" or "SHORT"
        entry_price:    float,
        qty:            int,
        stop:           float,
        target:         float,
        entry_bar_time: datetime,
        signal_meta:    dict,
    ):
        self.symbol         = symbol
        self.trading_symbol = trading_symbol
        self.direction      = direction
        self.entry_price    = entry_price
        self.qty            = qty
        self.stop           = stop
        self.target         = target
        self.entry_bar_time = entry_bar_time
        self.signal_meta    = signal_meta
        self.entry_date     = entry_bar_time.date()

        self.bars_open   = 0
        self.closed      = False
        self.exit_price: Optional[float] = None
        self.exit_reason: Optional[str]  = None
        self.exit_time:   Optional[datetime] = None

    @property
    def stop_dist(self) -> float:
        return abs(self.entry_price - self.stop)

    def current_r(self, price: float) -> float:
        if self.stop_dist == 0:
            return 0.0
        if self.direction == "LONG":
            return (price - self.entry_price) / self.stop_dist
        return (self.entry_price - price) / self.stop_dist

    def update_bar(self, bar: pd.Series, force_exit: bool = False) -> Optional[str]:
        """
        Process one 15m bar.
        bar: {bar_time, open, high, low, close, volume}
        """
        if self.closed:
            return None

        open_    = float(bar["open"])
        high     = float(bar["high"])
        low      = float(bar["low"])
        close    = float(bar["close"])
        bar_time = bar["bar_time"]
        if not isinstance(bar_time, datetime):
            bar_time = bar_time.to_pydatetime()

        self.bars_open += 1

        # Force exit at 15:20
        if force_exit or bar_time.time() >= FORCE_EXIT:
            self._close(close, "FORCE_EXIT", bar_time)
            return "FORCE_EXIT"

        if self.direction == "LONG":
            # Stop (gap protection)
            if open_ <= self.stop or low <= self.stop:
                self._close(min(open_, self.stop), "STOP", bar_time)
                return "STOP"
            # Target
            if high >= self.target:
                self._close(self.target, "TARGET", bar_time)
                return "TARGET"
        else:  # SHORT
            if open_ >= self.stop or high >= self.stop:
                self._close(max(open_, self.stop), "STOP", bar_time)
                return "STOP"
            if low <= self.target:
                self._close(self.target, "TARGET", bar_time)
                return "TARGET"

        return None

    def _close(self, price: float, reason: str, bar_time: datetime) -> None:
        self.exit_price  = float(price)
        self.exit_reason = reason
        self.exit_time   = bar_time
        self.closed      = True

    def pnl(self, fee_func=None) -> float:
        if self.exit_price is None:
            return 0.0
        gross = ((self.exit_price - self.entry_price) * self.qty
                 if self.direction == "LONG"
                 else (self.entry_price - self.exit_price) * self.qty)
        return gross - (fee_func(self.entry_price, self.exit_price,
                                 self.qty, self.direction) if fee_func else 0.0)

    def r_multiple(self) -> float:
        if self.exit_price is None or self.stop_dist == 0:
            return 0.0
        if self.direction == "LONG":
            return (self.exit_price - self.entry_price) / self.stop_dist
        return (self.entry_price - self.exit_price) / self.stop_dist


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

class Portfolio:
    def __init__(self, capital: float = 500_000.0):
        self.equity    = capital
        self.initial   = capital
        self.peak      = capital
        self.open_trades: List[Trade]   = []
        self.closed_trades: List[Trade] = []
        self.equity_curve: List[Tuple[date, float]] = []
        self.max_dd    = 0.0
        self._traded_today: Dict[date, Set[str]] = defaultdict(set)

    def _update_dd(self) -> None:
        self.peak   = max(self.peak, self.equity)
        self.max_dd = max(self.max_dd, (self.peak - self.equity) / self.peak)

    def can_enter(self, symbol: str, today: date) -> bool:
        if len(self.open_trades) >= MAX_POSITIONS:
            return False
        if symbol in {t.symbol for t in self.open_trades}:
            return False
        if symbol in self._traded_today[today]:
            return False
        return True

    def enter(
        self,
        symbol: str, trading_symbol: str, direction: str,
        entry_price: float, stop: float, target: float,
        entry_bar_time: datetime, signal_meta: dict,
    ) -> Optional[Trade]:
        today = entry_bar_time.date()
        if not self.can_enter(symbol, today):
            return None
        stop_dist = abs(entry_price - stop)
        if stop_dist <= 0 or entry_price <= 0:
            return None
        risk_amt = self.equity * RISK_PCT
        qty      = max(1, int(risk_amt / stop_dist))
        trade = Trade(symbol, trading_symbol, direction, entry_price, qty,
                      stop, target, entry_bar_time, signal_meta)
        self.open_trades.append(trade)
        self._traded_today[today].add(symbol)
        return trade

    def process_bar(self, bar_by_symbol: Dict[str, pd.Series],
                    bar_time: datetime) -> None:
        force = bar_time.time() >= FORCE_EXIT
        to_close = []
        for trade in self.open_trades:
            bar = bar_by_symbol.get(trade.symbol)
            if bar is None:
                continue
            reason = trade.update_bar(bar, force_exit=force)
            if reason:
                self.equity += trade.pnl(compute_fees)
                self._update_dd()
                to_close.append(trade)
                self.closed_trades.append(trade)
        for t in to_close:
            self.open_trades.remove(t)

    def end_of_day(self, today: date) -> None:
        # Any still-open trades get force-closed (shouldn't happen normally)
        for trade in list(self.open_trades):
            trade._close(trade.entry_price, "EOD_FLAT", datetime.combine(today, FORCE_EXIT))
            self.equity += trade.pnl(compute_fees)
            self._update_dd()
            self.closed_trades.append(trade)
        self.open_trades.clear()
        self.equity_curve.append((today, self.equity))


# ---------------------------------------------------------------------------
# Single-day signal scan + simulation
# ---------------------------------------------------------------------------

def run_day(
    today:          date,
    universe:       List[str],
    symbol_map:     Dict[str, str],
    bars_15m:       Dict[str, pd.DataFrame],    # symbol -> today's 15m bars
    nifty_15m:      Optional[pd.DataFrame],     # today's Nifty 15m bars
    context:        Dict[str, Dict[date, dict]], # historical context
    portfolio:      Portfolio,
    long_only:      bool = False,
    short_only:     bool = False,
    debug:          bool = False,
) -> None:
    """
    Run one trading day:
      1. Check opening range compression for each symbol.
      2. Scan each 15m bar from 10:15 onward for breakout.
      3. Enter on next bar open if all conditions met.
      4. Process open trades bar by bar.
      5. Force flat at 15:20.
    """

    # Nifty intraday trend: EMA20 vs EMA50 on 15m
    nifty_trend: Optional[str] = None
    if nifty_15m is not None and len(nifty_15m) >= 50:
        nifty_close = nifty_15m["close"]
        ema20 = float(nifty_close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(nifty_close.ewm(span=50, adjust=False).mean().iloc[-1])
        nifty_trend = "UP" if ema20 > ema50 else "DOWN"

    # Track which symbols have already fired today
    fired_today: Set[str] = set()

    # Get all 15m bar times for today sorted
    all_bar_times: Set[datetime] = set()
    for sym in universe:
        bars = bars_15m.get(sym)
        if bars is not None:
            for bt in bars["bar_time"]:
                all_bar_times.add(bt.to_pydatetime() if not isinstance(bt, datetime) else bt)

    if not all_bar_times:
        return

    sorted_bar_times = sorted(all_bar_times)

    # Pre-compute opening range and conditions per symbol
    # (done once per symbol per day, not per bar)
    sym_conditions: Dict[str, dict] = {}

    for sym in universe:
        bars = bars_15m.get(sym)
        ctx  = context.get(sym, {}).get(today)
        if bars is None or bars.empty or ctx is None:
            continue

        # --- Opening range (9:15–10:15) ---
        or_bars = bars[bars["bar_time"].dt.time < time(10, 15)]
        if len(or_bars) < 3:
            continue
        or_high = float(or_bars["high"].max())
        or_low  = float(or_bars["low"].min())
        or_range = or_high - or_low

        # Filter 1: OR compression
        med_fh_range = ctx["median_first_hour_range"]
        if med_fh_range <= 0 or or_range > OR_COMPRESSION * med_fh_range:
            continue

        sym_conditions[sym] = {
            "or_high":          or_high,
            "or_low":           or_low,
            "or_range":         or_range,
            "med_fh_range":     med_fh_range,
            "med_intraday_atr": ctx["median_intraday_atr"],
            "med_vol_by_slot":  ctx["median_vol_by_slot"],
        }

    if debug and sym_conditions:
        logger.debug(f"  {today}: {len(sym_conditions)} symbols passed OR compression")

    # --- Bar-by-bar simulation ---
    # Pending entries: (bar_time_to_enter, symbol, direction, stop, target, meta)
    pending_entries: List[tuple] = []

    for bt in sorted_bar_times:
        bt_time = bt.time() if isinstance(bt, datetime) else bt

        # Build bar lookup for this time slot
        bar_now: Dict[str, pd.Series] = {}
        for sym in universe:
            bars = bars_15m.get(sym)
            if bars is None:
                continue
            row = bars[bars["bar_time"] == bt]
            if not row.empty:
                bar_now[sym] = row.iloc[0]

        # Process pending entries from previous bar
        new_pending = []
        for (entry_bt, sym, direction, stop, target, meta) in pending_entries:
            if bt == entry_bt:
                bar = bar_now.get(sym)
                if bar is not None:
                    entry_price = float(bar["open"])
                    if entry_price > 0:
                        # Recalculate stop/target from actual entry price
                        atr_stop = meta["atr_stop_dist"]
                        if direction == "LONG":
                            actual_stop   = entry_price - atr_stop
                            actual_target = entry_price + atr_stop * TARGET_R
                        else:
                            actual_stop   = entry_price + atr_stop
                            actual_target = entry_price - atr_stop * TARGET_R

                        trade = portfolio.enter(
                            symbol         = sym,
                            trading_symbol = symbol_map.get(sym, sym.split("|")[-1]),
                            direction      = direction,
                            entry_price    = entry_price,
                            stop           = actual_stop,
                            target         = actual_target,
                            entry_bar_time = bt,
                            signal_meta    = meta,
                        )
                        if trade and debug:
                            logger.debug(
                                f"    ENTRY {sym} {direction} @ {entry_price:.2f} "
                                f"stop={actual_stop:.2f} tgt={actual_target:.2f} [{bt}]"
                            )
            else:
                new_pending.append((entry_bt, sym, direction, stop, target, meta))
        pending_entries = new_pending

        # Process open positions
        portfolio.process_bar(bar_now, bt)

        # Scan for new setups (only between 10:15 and ENTRY_CUTOFF, on breakout bars)
        if bt_time < time(10, 15) or bt_time > ENTRY_CUTOFF:
            continue
        if bt_time >= FORCE_EXIT:
            continue

        for sym, cond in sym_conditions.items():
            if sym in fired_today:
                continue

            bar = bar_now.get(sym)
            if bar is None:
                continue

            # Filter 2: Intraday ATR compression
            bars = bars_15m.get(sym)
            bars_so_far = bars[bars["bar_time"] <= bt]
            if len(bars_so_far) < ATR_PERIOD + 1:
                continue
            current_atr = atr14_on_bars(bars_so_far)
            if np.isnan(current_atr) or current_atr <= 0:
                continue
            if current_atr > ATR_COMPRESSION * cond["med_intraday_atr"]:
                continue

            bar_close  = float(bar["close"])
            bar_volume = float(bar["volume"])
            bar_slot   = bt.strftime("%H:%M")
            med_vol    = cond["med_vol_by_slot"].get(bar_slot, 0)

            # Filter 3: Volume confirmation
            if med_vol <= 0 or bar_volume < VOL_MULT * med_vol:
                continue

            # Filter 4: Directional breakout + Nifty trend filter
            direction = None

            if not short_only and bar_close > cond["or_high"]:
                if nifty_trend is None or nifty_trend == "UP":
                    direction = "LONG"

            if not long_only and bar_close < cond["or_low"]:
                if nifty_trend is None or nifty_trend == "DOWN":
                    direction = "SHORT"

            if direction is None:
                continue

            # Schedule entry on next bar's open
            next_idx = sorted_bar_times.index(bt) + 1
            if next_idx >= len(sorted_bar_times):
                continue
            next_bt = sorted_bar_times[next_idx]
            if next_bt.time() >= FORCE_EXIT:
                continue

            # Compute stop distance from ATR (will be re-applied at actual entry price)
            atr_stop_dist = STOP_ATR_MULT * current_atr

            meta = {
                "signal_bar_time": bt,
                "or_high":         cond["or_high"],
                "or_low":          cond["or_low"],
                "or_range":        cond["or_range"],
                "current_atr":     current_atr,
                "bar_volume":      bar_volume,
                "med_vol":         med_vol,
                "vol_ratio":       bar_volume / med_vol if med_vol > 0 else 0,
                "nifty_trend":     nifty_trend,
                "atr_stop_dist":   atr_stop_dist,
            }
            pending_entries.append((next_bt, sym, direction, None, None, meta))
            fired_today.add(sym)

            if debug:
                logger.debug(
                    f"    SIGNAL {sym} {direction} OR={'%.2f'%cond['or_high']}"
                    f"/{'%.2f'%cond['or_low']} ATR={'%.2f'%current_atr} "
                    f"vol_ratio={'%.1f'%meta['vol_ratio']} [{bt}]"
                )

    # End of day: force flat anything remaining
    portfolio.end_of_day(today)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(portfolio: Portfolio, label: str,
                 start: date, end: date) -> dict:
    trades = portfolio.closed_trades
    if not trades:
        print(f"\n[{label}] No trades.")
        return {}

    pnls    = [t.pnl(compute_fees) for t in trades]
    rs      = [t.r_multiple()      for t in trades]
    winners = [p for p in pnls if p > 0]
    losers  = [p for p in pnls if p <= 0]
    wr      = len(winners) / len(trades) * 100
    pf_den  = abs(sum(losers)) if losers else 1e-9
    pf      = sum(winners) / pf_den if winners else 0.0
    total   = sum(pnls)
    avg_r   = float(np.mean(rs))
    med_r   = float(np.median(rs))
    exp     = float(np.mean(pnls))
    dd      = portfolio.max_dd * 100
    ret     = (portfolio.equity / portfolio.initial - 1) * 100

    exits = defaultdict(int)
    for t in trades:
        exits[t.exit_reason] += 1

    longs  = [t for t in trades if t.direction == "LONG"]
    shorts = [t for t in trades if t.direction == "SHORT"]

    print(f"\n{'='*65}")
    print(f"  PixityAI v8 — {label}")
    print(f"  Period: {start} to {end}")
    print(f"{'='*65}")
    print(f"  Trades:        {len(trades)}  "
          f"(L:{len(longs)} S:{len(shorts)})")
    print(f"  Win Rate:      {wr:.1f}%")
    print(f"  Total PnL:     Rs {total:,.0f}")
    print(f"  Expectancy:    Rs {exp:,.0f} / trade")
    print(f"  Profit Factor: {pf:.2f}")
    print(f"  Max Drawdown:  {dd:.1f}%")
    print(f"  Avg R:         {avg_r:.3f}")
    print(f"  Median R:      {med_r:.3f}")
    print(f"  Return:        {ret:.1f}%")
    print(f"  Final Equity:  Rs {portfolio.equity:,.0f}")
    print(f"  Exits:         {dict(exits)}")

    buckets = [
        ("< -1R",         sum(1 for r in rs if r < -1.0)),
        ("-1 to -0.5R",   sum(1 for r in rs if -1.0 <= r < -0.5)),
        ("-0.5 to 0R",    sum(1 for r in rs if -0.5 <= r < 0)),
        ("0 to +0.5R",    sum(1 for r in rs if 0 <= r < 0.5)),
        ("+0.5 to +1R",   sum(1 for r in rs if 0.5 <= r < 1.0)),
        ("+1 to +1.8R",   sum(1 for r in rs if 1.0 <= r < 1.8)),
        ("+1.8 to +2.2R", sum(1 for r in rs if 1.8 <= r < 2.2)),  # target cluster
        (">= +2.2R",      sum(1 for r in rs if r >= 2.2)),
    ]
    print(f"\n  R DISTRIBUTION:")
    for lbl, cnt in buckets:
        print(f"    {lbl:<17} {cnt:>3}  {'#'*cnt}")

    sk = 0.0
    if len(rs) >= 5:
        from scipy.stats import skew as _skew
        sk = float(_skew(rs))
        verdict = ("RIGHT-SKEWED [good]" if sk > 0.1 else
                   "SYMMETRIC"           if sk > -0.1 else
                   "LEFT-SKEWED [bad]")
        print(f"\n  R skew: {sk:.3f}  ->  {verdict}")

    tgt_n  = exits.get("TARGET",     0)
    stp_n  = exits.get("STOP",       0)
    fex_n  = exits.get("FORCE_EXIT", 0)

    print(f"\n  EVAL CRITERIA:")
    print(f"    PF >= 1.3:           {'PASS' if pf >= 1.3 else 'FAIL'}  ({pf:.2f})")
    print(f"    Avg R >= 0.25:       {'PASS' if avg_r >= 0.25 else 'FAIL'}  ({avg_r:.3f})")
    print(f"    Expectancy > 0:      {'PASS' if exp > 0 else 'FAIL'}  (Rs {exp:,.0f})")
    print(f"    Target hit rate:     {tgt_n}/{len(trades)} = {tgt_n/len(trades)*100:.0f}%")
    print(f"    Stop hit rate:       {stp_n}/{len(trades)} = {stp_n/len(trades)*100:.0f}%")
    print(f"    Force-exit rate:     {fex_n}/{len(trades)} = {fex_n/len(trades)*100:.0f}%")
    print(f"{'='*65}\n")

    return {
        "trades": len(trades), "win_rate": wr, "total_pnl": total,
        "expectancy": exp, "profit_factor": pf, "max_dd": dd,
        "avg_r": avg_r, "median_r": med_r, "skew": sk,
        "targets": tgt_n, "stops": stp_n, "force_exits": fex_n,
    }


def dump_trades(portfolio: Portfolio, label: str, n: int = 5) -> None:
    trades = sorted(portfolio.closed_trades, key=lambda t: t.r_multiple())
    if not trades:
        return

    def _tbl(subset: List[Trade], header: str) -> None:
        print(f"\n  {header}")
        print(f"  {'#':>3}  {'Symbol':<12}  {'Dir':<5}  {'Entry':>8}  {'Exit':>8}  "
              f"{'Stop':>8}  {'Tgt':>8}  {'Bars':>4}  {'R':>6}  "
              f"{'PnL':>8}  {'VolRatio':>8}  Reason  Time")
        print(f"  {'-'*110}")
        for i, t in enumerate(subset, 1):
            pnl  = t.pnl(compute_fees)
            r    = t.r_multiple()
            vr   = t.signal_meta.get("vol_ratio", 0)
            nt   = t.signal_meta.get("nifty_trend", "?")
            print(f"  {i:>3}  {t.trading_symbol[:12]:<12}  {t.direction:<5}  "
                  f"{t.entry_price:>8.2f}  {t.exit_price:>8.2f}  "
                  f"{t.stop:>8.2f}  {t.target:>8.2f}  "
                  f"{t.bars_open:>4}  {r:>6.2f}  {pnl:>8,.0f}  "
                  f"{vr:>7.1f}x  {t.exit_reason}  "
                  f"{t.entry_bar_time.strftime('%Y-%m-%d %H:%M')}  nifty={nt}")

    print(f"\n{'='*65}")
    print(f"  {label} — TRADE SAMPLES")
    print(f"{'='*65}")
    _tbl(trades[-n:][::-1], f"TOP {n} BEST")
    _tbl(trades[:n],        f"TOP {n} WORST")


# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------

def run_backtest(
    start:      date,
    end:        date,
    capital:    float,
    universe:   List[str],
    symbol_map: Dict[str, str],
    context:    Dict[str, Dict[date, dict]],
    label:      str   = "BACKTEST",
    long_only:  bool  = False,
    short_only: bool  = False,
    debug:      bool  = False,
) -> Portfolio:
    logger.info(f"[{label}] {start} to {end}")
    trading_days = get_trading_days(ROOT / "data", start, end)
    logger.info(f"Trading days: {len(trading_days)}")

    m1_dir = ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
    portfolio = Portfolio(capital=capital)
    universe_set = set(universe)

    for td in trading_days:
        db_path = m1_dir / f"{td.isoformat()}.duckdb"
        bars_15m = load_15m_day(db_path, universe_set)
        nifty_15m = load_nifty_15m_day(db_path)

        run_day(
            today      = td,
            universe   = universe,
            symbol_map = symbol_map,
            bars_15m   = bars_15m,
            nifty_15m  = nifty_15m,
            context    = context,
            portfolio  = portfolio,
            long_only  = long_only,
            short_only = short_only,
            debug      = debug,
        )

    logger.info(f"[{label}] done: {len(portfolio.closed_trades)} trades, "
                f"equity Rs {portfolio.equity:,.0f}")
    return portfolio


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PixityAI v8 Intraday Expansion")
    parser.add_argument("--capital",      default=500_000, type=int)
    parser.add_argument("--universe-size",default=60,      type=int)
    parser.add_argument("--long-only",    action="store_true")
    parser.add_argument("--short-only",   action="store_true")
    parser.add_argument("--debug-trades", action="store_true")
    parser.add_argument("--debug-bars",   action="store_true",
                        help="Log bar-level debug (verbose)")
    args = parser.parse_args()

    db = DatabaseManager(ROOT / "data")
    with db.config_reader() as conn:
        rows = conn.execute(
            "SELECT instrument_key, trading_symbol FROM fo_stocks WHERE is_active = 1"
        ).fetchall()
    symbol_map = {r[0]: r[1] for r in rows}
    logger.info(f"F&O universe: {len(symbol_map)} symbols")

    if args.long_only:
        logger.info("Mode: LONG only")
    elif args.short_only:
        logger.info("Mode: SHORT only")
    else:
        logger.info("Mode: LONG + SHORT")

    # Four walk-forward periods
    periods = [
        (date(2023, 1, 2), date(2023, 12, 29), "TRAIN-A (2023)"),
        (date(2024, 1, 2), date(2024, 12, 31), "TEST-A  (2024)"),
        (date(2025, 1, 2), date(2025,  7, 31), "TRAIN-B (2025-H1)"),
        (date(2025, 8, 1), date(2026,  2, 13), "TEST-B  (2025-H2)"),
    ]

    all_start = date(2023, 1, 2)
    all_end   = date(2026, 2, 13)
    all_trading_days = get_trading_days(ROOT / "data", all_start, all_end)

    # Universe A: ranked using first 60 days of TRAIN-A
    td_a   = get_trading_days(ROOT / "data", date(2023, 1, 2), date(2023, 12, 29))
    rank_days_a = td_a[:60]
    logger.info(f"Selecting universe A (ranked over {rank_days_a[0]} to {rank_days_a[-1]})...")
    univ_a = select_universe(ROOT / "data", symbol_map, rank_days_a, args.universe_size)

    # Universe B: ranked using first 60 days of TRAIN-B
    td_b   = get_trading_days(ROOT / "data", date(2025, 1, 2), date(2025, 7, 31))
    rank_days_b = td_b[:60]
    logger.info(f"Selecting universe B (ranked over {rank_days_b[0]} to {rank_days_b[-1]})...")
    univ_b = select_universe(ROOT / "data", symbol_map, rank_days_b, args.universe_size)

    # Build historical context for both universes over the full date range
    # Context is causal: on day D, only prior days are used for medians
    all_universe = list(set(univ_a) | set(univ_b))
    logger.info(f"Building historical context for {len(all_universe)} unique symbols "
                f"over {len(all_trading_days)} days...")
    context = build_historical_context(
        ROOT / "data", set(all_universe), all_trading_days, LOOKBACK_DAYS
    )

    # Run all four periods
    all_stats = {}
    for i, (p_start, p_end, label) in enumerate(periods):
        universe = univ_a if i < 2 else univ_b
        port = run_backtest(
            start      = p_start,
            end        = p_end,
            capital    = args.capital,
            universe   = universe,
            symbol_map = symbol_map,
            context    = context,
            label      = label,
            long_only  = args.long_only,
            short_only = args.short_only,
            debug      = args.debug_bars,
        )
        stats = print_report(port, label, p_start, p_end)
        all_stats[label] = stats
        if args.debug_trades:
            dump_trades(port, label)

    # Walk-forward summary
    print("\n" + "=" * 75)
    print("  WALK-FORWARD SUMMARY — v8 Intraday Expansion")
    print("=" * 75)
    lbls = list(all_stats.keys())
    print(f"  {'Metric':<22}  " + "  ".join(f"{l[:10]:>12}" for l in lbls))
    print(f"  {'-'*70}")
    for k in ["trades", "win_rate", "profit_factor", "avg_r", "median_r",
              "skew", "targets", "stops", "force_exits", "max_dd", "total_pnl"]:
        vals = [all_stats.get(lb, {}).get(k, 0) for lb in lbls]
        if vals and isinstance(vals[0], float):
            row = "  ".join(f"{v:>12.3f}" for v in vals)
        else:
            row = "  ".join(f"{v:>12}" for v in vals)
        print(f"  {k:<22}  {row}")


if __name__ == "__main__":
    main()
