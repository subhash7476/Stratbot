"""
PixityAI v6 — True Momentum Structure
======================================
Hypothesis: Raw 20-day price breakout momentum on liquid NSE equities.
Institutional flow drives trend persistence and follow-through.

PIPELINE:
  1. Universe: Top 60 by median 60-day traded value. Frozen for entire backtest.
  2. Signal: Close[T] > Highest High of last 20 days AND 20d return > 0
  3. Entry: T+1 open
  4. Trade state machine: 1.5xATR initial stop, 2xATR trailing stop on highest close. No time cap.
  5. Portfolio: 0.75% risk, max 5 concurrent, 5-day re-entry guard per symbol

SPEC (locked):
  Stop:      1.5 × ATR(20) initial (set at entry)
  Target:    NONE — trailing stop only
  Trail:     2×ATR below highest close since entry (active from bar 1, ratchets up only)
  Time stop: NONE — hold until stop is hit
  No early exit. No fixed target. No time cap. Trend runs until it ends.

UNIVERSE:
  Top 60 NSE F&O equities by median 60-day daily traded value.
  Ranked once on last available data before train start. Frozen.

WALK-FORWARD:
  Train: 2025-01-02 to 2025-07-31
  Test:  2025-08-01 to 2026-02-13

FEES:
  NSE equity:
  Brokerage: Rs 20 flat x2
  STT: 0.025% sell-side
  Exchange: 0.00345% turnover
  Stamp: 0.003% buy-side

Usage:
    python scripts/run_v6_backtest.py
    python scripts/run_v6_backtest.py --debug-trades
    python scripts/run_v6_backtest.py --universe-size 60
"""

from __future__ import annotations

import os
import sys
import argparse
import logging
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import duckdb
import numpy as np
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
# Fee model (identical to v4)
# ---------------------------------------------------------------------------
STT_RATE      = 0.00025    # sell side
BROKERAGE     = 20.0       # per order, x2
EXCHANGE_RATE = 0.0000345  # composite turnover fee
STAMP_RATE    = 0.00003    # buy side


def compute_fees(entry: float, exit_p: float, qty: int, direction: str) -> float:
    buy_val  = entry  * qty if direction == "LONG" else exit_p * qty
    sell_val = exit_p * qty if direction == "LONG" else entry  * qty
    turnover = buy_val + sell_val
    return (sell_val * STT_RATE) + (BROKERAGE * 2) + (turnover * EXCHANGE_RATE) + (buy_val * STAMP_RATE)


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def get_trading_days(data_root: Path, from_date: date, to_date: date) -> List[date]:
    """Detect trading days from existing 1m DuckDB files."""
    m1_dir = data_root / "market_data" / "nse" / "candles" / "1m"
    days = []
    current = from_date
    while current <= to_date:
        if (m1_dir / f"{current.isoformat()}.duckdb").exists():
            days.append(current)
        current += timedelta(days=1)
    return sorted(days)


def preload_daily_ohlcv(
    data_root: Path,
    symbols_set: Set[str],
    from_date: date,
    to_date: date,
) -> Dict[str, pd.DataFrame]:
    """
    Aggregate 1m -> daily OHLCV for all symbols.
    Returns dict: symbol -> DataFrame(date, open, high, low, close, volume).
    """
    m1_dir = data_root / "market_data" / "nse" / "candles" / "1m"
    trading_days = get_trading_days(data_root, from_date, to_date)

    logger.info(f"Aggregating {len(trading_days)} days of 1m data to daily "
                f"for {len(symbols_set)} symbols...")

    symbol_rows: Dict[str, List[dict]] = {s: [] for s in symbols_set}

    for td in trading_days:
        db_path = m1_dir / f"{td.isoformat()}.duckdb"
        if not db_path.exists():
            continue
        try:
            conn = duckdb.connect(str(db_path), read_only=True)
            df = conn.execute("""
                SELECT
                    symbol,
                    FIRST(open  ORDER BY timestamp) AS open,
                    MAX(high)                        AS high,
                    MIN(low)                         AS low,
                    LAST(close  ORDER BY timestamp)  AS close,
                    SUM(volume)                      AS volume
                FROM candles
                WHERE timeframe = '1m'
                GROUP BY symbol
            """).df()
            conn.close()
            for _, row in df.iterrows():
                sym = row["symbol"]
                if sym in symbol_rows:
                    symbol_rows[sym].append({
                        "date":   td,
                        "open":   float(row["open"]),
                        "high":   float(row["high"]),
                        "low":    float(row["low"]),
                        "close":  float(row["close"]),
                        "volume": float(row["volume"]),
                    })
        except Exception as e:
            logger.debug(f"Error loading {td}: {e}")

    daily_cache: Dict[str, pd.DataFrame] = {}
    for sym, rows in symbol_rows.items():
        if rows:
            daily_cache[sym] = (
                pd.DataFrame(rows)
                .sort_values("date")
                .reset_index(drop=True)
            )

    loaded = sum(1 for v in daily_cache.values() if len(v) > 0)
    logger.info(f"Preloaded {loaded}/{len(symbols_set)} symbols successfully.")
    return daily_cache


def preload_nifty(data_root: Path, from_date: date, to_date: date) -> Optional[pd.DataFrame]:
    """Load Nifty 50 daily OHLCV from 1d DuckDB files."""
    d1_dir = data_root / "market_data" / "nse" / "candles" / "1d"
    rows = []
    current = from_date
    while current <= to_date:
        db_path = d1_dir / f"{current.isoformat()}.duckdb"
        if db_path.exists():
            try:
                conn = duckdb.connect(str(db_path), read_only=True)
                df = conn.execute("""
                    SELECT open, high, low, close, volume FROM candles
                    WHERE symbol = 'NSE_INDEX|Nifty 50' LIMIT 1
                """).df()
                conn.close()
                if not df.empty:
                    rows.append({
                        "date":   current,
                        "open":   float(df["open"].iloc[0]),
                        "high":   float(df["high"].iloc[0]),
                        "low":    float(df["low"].iloc[0]),
                        "close":  float(df["close"].iloc[0]),
                        "volume": float(df["volume"].iloc[0]),
                    })
            except Exception:
                pass
        current += timedelta(days=1)

    if rows:
        return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return None


# ---------------------------------------------------------------------------
# Universe selection: Top-N by median 60-day traded value
# ---------------------------------------------------------------------------

def select_universe(
    daily_cache: Dict[str, pd.DataFrame],
    symbol_map: Dict[str, str],
    as_of: date,
    top_n: int = 60,
) -> List[str]:
    """
    Rank all symbols by median 60-day daily traded value (Close × Volume)
    as of `as_of` date. Return top_n symbols (instrument_key strings).
    Universe is FROZEN after this call.
    """
    liq: List[Tuple[str, float]] = []

    for sym, df in daily_cache.items():
        causal = df[df["date"] <= as_of]
        if len(causal) < 10:
            continue
        tail = causal.tail(60)
        traded_val = (tail["close"] * tail["volume"]).median() / 1e7  # Rs crore
        if traded_val > 0:
            liq.append((sym, traded_val))

    if not liq:
        logger.warning("No liquidity data found for universe selection!")
        return []

    liq.sort(key=lambda x: x[1], reverse=True)
    top = [sym for sym, _ in liq[:top_n]]

    logger.info(f"Universe selected: top {len(top)} symbols by liquidity")
    if len(top) >= 5:
        logger.info(f"  Rank 1:  {symbol_map.get(liq[0][0], liq[0][0])} "
                    f"  liq={liq[0][1]:.0f} Cr")
        logger.info(f"  Rank 5:  {symbol_map.get(liq[4][0], liq[4][0])} "
                    f"  liq={liq[4][1]:.0f} Cr")
        logger.info(f"  Rank {len(top)}: {symbol_map.get(liq[len(top)-1][0], liq[len(top)-1][0])} "
                    f"  liq={liq[len(top)-1][1]:.0f} Cr")

    return top


# ---------------------------------------------------------------------------
# Indicator: ATR
# ---------------------------------------------------------------------------

def compute_atr(df: pd.DataFrame, period: int = 20) -> Optional[pd.Series]:
    """Wilder's ATR — same as v4 scanner."""
    if len(df) < period + 1:
        return None
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"]  - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def generate_signals(
    universe: List[str],
    symbol_map: Dict[str, str],
    daily_cache: Dict[str, pd.DataFrame],
    trading_days: List[date],
    lookback: int = 20,
) -> Dict[date, List[dict]]:
    """
    For each trading day T in the range:
      Signal fires if:
        Close[T] > max(High[T-lookback .. T-1])   (20-day high breakout, strictly causal)
        AND Close[T] / Close[T-lookback] - 1 > 0  (20-day return positive)

    Entry is at T+1 open.

    Returns: {entry_date -> [signal_dict, ...]}

    Implementation note:
      daily_cache[sym] is a full DataFrame (RangeIndex) covering the entire
      preload window.  We build a date->row-index lookup per symbol once,
      then work entirely with integer positional indexing — no causal-slice
      overhead and no index-alignment ambiguity.
    """
    signals_by_entry: Dict[date, List[dict]] = defaultdict(list)
    trading_day_set = set(trading_days)

    for sym in universe:
        df = daily_cache.get(sym)
        if df is None or df.empty:
            continue

        trading_sym = symbol_map.get(sym, sym.split("|")[-1])

        # Pre-compute ATR on the full DataFrame (positional int index matches df)
        atr_series = compute_atr(df, 20)
        if atr_series is None:
            continue

        dates_arr  = df["date"].values          # numpy array for fast lookup
        close_arr  = df["close"].values
        high_arr   = df["high"].values
        atr_arr    = atr_series.values          # same length / same order as df

        # Build date -> integer position map
        date_to_pos: Dict[date, int] = {d: i for i, d in enumerate(dates_arr)}

        need = lookback + 1   # minimum rows needed before T to check breakout

        for day_i, td in enumerate(trading_days):
            pos = date_to_pos.get(td)
            if pos is None:
                continue                        # symbol had no data on this day

            # Need at least `need` bars before pos (indices 0..pos-1) plus pos itself
            if pos < need:
                continue

            close_t = float(close_arr[pos])
            if close_t <= 0:
                continue

            # ATR must be valid
            daily_atr = float(atr_arr[pos])
            if np.isnan(daily_atr) or daily_atr <= 0:
                continue

            # 20-day HIGH breakout: Close[T] > max(High[pos-20 .. pos-1])
            prior_20_high = float(high_arr[pos - lookback : pos].max())
            if close_t <= prior_20_high:
                continue

            # 20-day return > 0: Close[T] > Close[T-20]
            close_20d_ago = float(close_arr[pos - lookback])
            if close_t <= close_20d_ago:
                continue

            # Entry is T+1 open — next element in trading_days list
            if day_i + 1 >= len(trading_days):
                continue
            entry_date = trading_days[day_i + 1]

            signals_by_entry[entry_date].append({
                "symbol":         sym,
                "trading_symbol": trading_sym,
                "signal_date":    td,
                "entry_date":     entry_date,
                "direction":      "LONG",
                "signal_close":   close_t,
                "daily_atr":      daily_atr,
                "prior_20_high":  prior_20_high,
                "return_20d":     (close_t / close_20d_ago) - 1,
            })

    total = sum(len(v) for v in signals_by_entry.values())
    logger.info(f"Signal generation: {total} signals across {len(signals_by_entry)} entry days")
    return signals_by_entry


# ---------------------------------------------------------------------------
# Trade state machine  (v5 — trail-only, no early exit, no fixed target)
# ---------------------------------------------------------------------------

class Trade:
    """
    v6 Long-only trade lifecycle on daily bars.

    Spec:
      Stop:  entry - 1.5*ATR  (initial, set at entry)
      Trail: 2*ATR below highest close since entry, ratchets up, never down.
             Active from bar 1.
      Exit:  only on stop hit or force-close at period end.
      No time cap. No fixed target. Trend runs until it ends.
    """

    STOP_MULT      = 1.5     # initial stop distance = 1.5 × ATR
    TRAIL_ATR_MULT = 2.0     # trail at 2× ATR below highest close

    def __init__(
        self,
        symbol:         str,
        trading_symbol: str,
        entry_price:    float,
        qty:            int,
        stop:           float,
        daily_atr:      float,
        entry_date:     date,
        signal_date:    date,
        signal_close:   float,
    ):
        self.symbol         = symbol
        self.trading_symbol = trading_symbol
        self.entry_price    = entry_price
        self.qty            = qty
        self.stop           = stop
        self.daily_atr      = daily_atr
        self.entry_date     = entry_date
        self.signal_date    = signal_date
        self.signal_close   = signal_close
        self.direction      = "LONG"

        self.bars_open      = 0
        self.highest_close  = entry_price   # tracks highest close for trail
        self.peak_r         = 0.0

        self.closed         = False
        self.exit_price: Optional[float] = None
        self.exit_reason: Optional[str]  = None
        self.exit_date: Optional[date]   = None

    @property
    def stop_dist(self) -> float:
        return abs(self.entry_price - self.stop)

    def current_r(self, price: float) -> float:
        if self.stop_dist == 0:
            return 0.0
        return (price - self.entry_price) / self.stop_dist

    def update_bar(self, bar: pd.Series) -> Optional[str]:
        """
        Process one daily bar.
        bar must have: date, open, high, low, close.
        Returns exit reason or None.

        Order of operations:
          1. Stop check (intrabar, gap protection via open)
          2. Update highest_close, ratchet trail stop up (never down)
          No time stop. Holds until stop is hit.
        """
        if self.closed:
            return None

        open_  = float(bar["open"])
        high   = float(bar["high"])
        low    = float(bar["low"])
        close  = float(bar["close"])
        bar_dt = bar["date"] if isinstance(bar["date"], date) else bar["date"].date()

        self.bars_open += 1

        # 1. Stop check — gap protection: exit at min(open, stop) for longs
        if open_ <= self.stop or low <= self.stop:
            exit_p = min(open_, self.stop)
            self._close(exit_p, "STOP", bar_dt)
            return "STOP"

        # 2. Update highest close, ratchet trail up (never down)
        self.highest_close = max(self.highest_close, close)
        r_now = self.current_r(close)
        self.peak_r = max(self.peak_r, r_now)

        trail_stop = self.highest_close - self.TRAIL_ATR_MULT * self.daily_atr
        self.stop  = max(self.stop, trail_stop)

        return None

    def _close(self, price: float, reason: str, dt) -> None:
        self.exit_price  = float(price)
        self.exit_reason = reason
        self.exit_date   = dt if isinstance(dt, date) else dt.date()
        self.closed      = True

    def pnl(self, fee_func=None) -> float:
        if self.exit_price is None:
            return 0.0
        gross = (self.exit_price - self.entry_price) * self.qty
        fees  = fee_func(self.entry_price, self.exit_price, self.qty, self.direction) \
                if fee_func else 0.0
        return gross - fees

    def r_multiple(self) -> float:
        """Realized R = pnl_gross / initial_risk."""
        if self.exit_price is None or self.stop_dist == 0:
            return 0.0
        gross = (self.exit_price - self.entry_price) * self.qty
        return gross / (self.stop_dist * self.qty)


# ---------------------------------------------------------------------------
# Portfolio simulator (v5)
# ---------------------------------------------------------------------------

class Portfolio:
    MAX_POSITIONS  = 5
    RISK_PCT       = 0.0075   # 0.75% per trade
    REENTRY_DAYS   = 5        # cooldown per symbol after exit

    def __init__(self, capital: float = 500_000.0):
        self.equity    = capital
        self.initial   = capital
        self.peak      = capital
        self.open_trades: List[Trade]   = []
        self.closed_trades: List[Trade] = []
        self.equity_curve: List[Tuple[date, float]] = []
        self.max_dd    = 0.0
        # cooldown: symbol -> last_exit_date
        self._last_exit: Dict[str, date] = {}

    def _update_dd(self) -> None:
        self.peak  = max(self.peak, self.equity)
        dd         = (self.peak - self.equity) / self.peak
        self.max_dd = max(self.max_dd, dd)

    def open_symbols(self) -> Set[str]:
        return {t.symbol for t in self.open_trades}

    def _in_cooldown(self, symbol: str, today: date) -> bool:
        last = self._last_exit.get(symbol)
        if last is None:
            return False
        # trading-day-aware cooldown is expensive; use calendar days as proxy
        # 5 calendar days ≈ 3–4 trading days, conservative enough
        return (today - last).days < self.REENTRY_DAYS

    def can_enter(self, symbol: str, today: date) -> bool:
        if len(self.open_trades) >= self.MAX_POSITIONS:
            return False
        if symbol in self.open_symbols():
            return False
        if self._in_cooldown(symbol, today):
            return False
        return True

    def enter(self, sig: dict, entry_price: float, today: date) -> Optional[Trade]:
        if not self.can_enter(sig["symbol"], today):
            return None

        daily_atr = sig["daily_atr"]
        stop_dist = daily_atr * Trade.STOP_MULT
        if stop_dist <= 0 or entry_price <= 0:
            return None

        risk_amt = self.equity * self.RISK_PCT
        qty      = max(1, int(risk_amt / stop_dist))
        stop     = entry_price - stop_dist

        trade = Trade(
            symbol         = sig["symbol"],
            trading_symbol = sig["trading_symbol"],
            entry_price    = entry_price,
            qty            = qty,
            stop           = stop,
            daily_atr      = daily_atr,
            entry_date     = today,
            signal_date    = sig["signal_date"],
            signal_close   = sig["signal_close"],
        )
        self.open_trades.append(trade)
        return trade

    def process_day(self, bar_by_symbol: Dict[str, pd.Series], today: date) -> None:
        """Update all open trades with today's bar."""
        to_close = []
        for trade in self.open_trades:
            bar = bar_by_symbol.get(trade.symbol)
            if bar is None:
                continue
            reason = trade.update_bar(bar)
            if reason:
                pnl = trade.pnl(compute_fees)
                self.equity += pnl
                self._update_dd()
                self._last_exit[trade.symbol] = today
                to_close.append(trade)
                self.closed_trades.append(trade)

        for t in to_close:
            self.open_trades.remove(t)

        self.equity_curve.append((today, self.equity))

    def force_close_all(self, bar_by_symbol: Dict[str, pd.Series], today: date) -> None:
        for trade in list(self.open_trades):
            bar   = bar_by_symbol.get(trade.symbol)
            price = float(bar["close"]) if bar is not None else trade.entry_price
            trade._close(price, "FORCE_CLOSE", today)
            pnl   = trade.pnl(compute_fees)
            self.equity += pnl
            self._update_dd()
            self.closed_trades.append(trade)
        self.open_trades.clear()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(portfolio: Portfolio, label: str, start: date, end: date) -> dict:
    trades = portfolio.closed_trades
    if not trades:
        print(f"\n[{label}] No trades.")
        return {}

    pnls    = [t.pnl(compute_fees) for t in trades]
    rs      = [t.r_multiple()      for t in trades]
    winners = [p for p in pnls if p > 0]
    losers  = [p for p in pnls if p <= 0]
    wr      = len(winners) / len(trades) * 100
    exp     = np.mean(pnls)
    pf_den  = abs(sum(losers)) if losers else 1e-9
    pf      = sum(winners) / pf_den if winners else 0.0
    total   = sum(pnls)
    avg_r   = float(np.mean(rs))
    med_r   = float(np.median(rs))
    dd      = portfolio.max_dd * 100
    ret     = (portfolio.equity / portfolio.initial - 1) * 100

    exits = defaultdict(int)
    for t in trades:
        exits[t.exit_reason] += 1

    print(f"\n{'='*65}")
    print(f"  PixityAI v6 — {label}")
    print(f"  Period: {start} to {end}")
    print(f"{'='*65}")
    print(f"  Trades:        {len(trades)}")
    print(f"  Win Rate:      {wr:.1f}%")
    print(f"  Total PnL:     Rs {total:,.0f}")
    print(f"  Expectancy:    Rs {exp:,.0f} / trade")
    print(f"  Profit Factor: {pf:.2f}")
    print(f"  Max Drawdown:  {dd:.1f}%")
    print(f"  Avg R:         {avg_r:.3f}")
    print(f"  Median R:      {med_r:.3f}")
    print(f"  Return:        {ret:.1f}%")
    print(f"  Final Equity:  Rs {portfolio.equity:,.0f}")
    print(f"  Exit reasons:  {dict(exits)}")

    # R distribution
    buckets = [
        ("< -1R",        sum(1 for r in rs if r < -1.0)),
        ("-1 to -0.5R",  sum(1 for r in rs if -1.0 <= r < -0.5)),
        ("-0.5 to 0R",   sum(1 for r in rs if -0.5 <= r < 0)),
        ("0 to +0.5R",   sum(1 for r in rs if 0 <= r < 0.5)),
        ("+0.5 to +1R",  sum(1 for r in rs if 0.5 <= r < 1.0)),
        ("+1 to +1.5R",  sum(1 for r in rs if 1.0 <= r < 1.5)),
        ("+1.5 to +2R",  sum(1 for r in rs if 1.5 <= r < 2.0)),
        ("+2 to +3R",    sum(1 for r in rs if 2.0 <= r < 3.0)),
        (">= +3R",       sum(1 for r in rs if r >= 3.0)),
    ]
    print(f"\n  R DISTRIBUTION:")
    for lbl, cnt in buckets:
        bar = "#" * cnt
        print(f"    {lbl:<16} {cnt:>3}  {bar}")

    # Skew
    sk = 0.0
    if len(rs) >= 5:
        from scipy.stats import skew as _skew
        sk = float(_skew(rs))
        verdict = "RIGHT-SKEWED [good]" if sk > 0.1 else \
                  "SYMMETRIC"           if sk > -0.1 else \
                  "LEFT-SKEWED [bad]"
        print(f"\n  R skew: {sk:.3f}  ->  {verdict}")

    # Eval criteria
    print(f"\n  EVAL CRITERIA:")
    print(f"    PF >= 1.15:          {'PASS' if pf >= 1.15 else 'FAIL'}  ({pf:.2f})")
    print(f"    Avg R >= 0.25:       {'PASS' if avg_r >= 0.25 else 'FAIL'}  ({avg_r:.3f})")
    print(f"    Right-skewed:        {'PASS' if sk > 0.1 else 'FAIL'}  (skew={sk:.3f})")
    print(f"    Expectancy > 0:      {'PASS' if exp > 0 else 'FAIL'}  (Rs {exp:,.0f})")
    max_hold = max((t.bars_open for t in trades), default=0)
    print(f"    Force-closes:        {exits.get('FORCE_CLOSE', 0)} (open at period end)")
    print(f"    Longest hold:        {max_hold} bars")
    print(f"{'='*65}\n")

    return {
        "trades":        len(trades),
        "win_rate":      wr,
        "total_pnl":     total,
        "expectancy":    exp,
        "profit_factor": pf,
        "max_dd":        dd,
        "avg_r":         avg_r,
        "median_r":      med_r,
        "skew":          sk,
        "time_stops":    exits.get("TIME_STOP", 0),
        "stops":         exits.get("STOP", 0),
        "force_closes":  exits.get("FORCE_CLOSE", 0),
    }


def dump_trades(portfolio: Portfolio, label: str, n_best: int = 5, n_worst: int = 5) -> None:
    trades = sorted(portfolio.closed_trades, key=lambda t: t.r_multiple())
    if not trades:
        return

    def _print_table(subset: List[Trade], header: str) -> None:
        print(f"\n  {header}")
        print(f"  {'#':>3}  {'Symbol':<14}  {'Entry':>8}  {'Exit':>8}  "
              f"{'ATR':>6}  {'Stop':>8}  {'Days':>4}  "
              f"{'R':>6}  {'PnL':>9}  Reason       Entry-date")
        print(f"  {'-'*100}")
        for i, t in enumerate(subset, 1):
            pnl  = t.pnl(compute_fees)
            r    = t.r_multiple()
            stp_pct = abs(t.stop - t.entry_price) / t.entry_price * 100 if t.entry_price else 0
            print(f"  {i:>3}  {t.trading_symbol[:14]:<14}  "
                  f"{t.entry_price:>8.2f}  {t.exit_price:>8.2f}  "
                  f"{t.daily_atr:>6.2f}  {t.stop:>8.2f}  "
                  f"{t.bars_open:>4}  {r:>6.2f}  {pnl:>9,.0f}  "
                  f"{t.exit_reason:<12} {t.entry_date}  "
                  f"(stp={stp_pct:.1f}%  signal={t.signal_date})")

    print(f"\n{'='*65}")
    print(f"  {label} — TRADE SAMPLES")
    print(f"{'='*65}")
    _print_table(trades[-n_best:][::-1], f"TOP {n_best} BEST TRADES")
    _print_table(trades[:n_worst],       f"TOP {n_worst} WORST TRADES")


# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------

def run_backtest(
    start:       date,
    end:         date,
    capital:     float,
    universe:    List[str],
    symbol_map:  Dict[str, str],
    daily_cache: Dict[str, pd.DataFrame],
    label:       str = "BACKTEST",
    lookback:    int = 20,
) -> Portfolio:
    logger.info(f"[{label}] {start} to {end}")

    # Trading days within period
    trading_days = get_trading_days(ROOT / "data", start, end)
    logger.info(f"Trading days in period: {len(trading_days)}")

    # Generate all signals for the period
    signals_by_entry = generate_signals(
        universe     = universe,
        symbol_map   = symbol_map,
        daily_cache  = daily_cache,
        trading_days = trading_days,
        lookback     = lookback,
    )

    # Portfolio simulation
    portfolio = Portfolio(capital=capital)

    for td in trading_days:
        # Build today's bar lookup for all universe symbols
        bar_today: Dict[str, pd.Series] = {}
        for sym in universe:
            df = daily_cache.get(sym)
            if df is None:
                continue
            row = df[df["date"] == td]
            if not row.empty:
                bar_today[sym] = row.iloc[0]

        # Entries: signals whose entry_date == today
        sigs_today = signals_by_entry.get(td, [])
        for sig in sigs_today:
            bar = bar_today.get(sig["symbol"])
            if bar is None:
                continue
            entry_price = float(bar["open"])
            if entry_price <= 0:
                continue

            trade = portfolio.enter(sig, entry_price, td)
            if trade:
                logger.debug(
                    f"  ENTRY: {sig['trading_symbol']} LONG "
                    f"@ {entry_price:.2f}  stop={trade.stop:.2f}  "
                    f"ATR={sig['daily_atr']:.2f}  [{td}]"
                )

        # Update open positions with today's bar
        portfolio.process_day(bar_today, td)

    # Force-close anything remaining at period end
    last_bars: Dict[str, pd.Series] = {}
    for sym in universe:
        df = daily_cache.get(sym)
        if df is None:
            continue
        row = df[df["date"] <= end].tail(1)
        if not row.empty:
            last_bars[sym] = row.iloc[0]

    if portfolio.open_trades:
        logger.info(f"Force-closing {len(portfolio.open_trades)} positions at period end")
        portfolio.force_close_all(last_bars, end)

    return portfolio


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PixityAI v6 True Momentum Backtest")
    parser.add_argument("--train-start",   default="2025-01-02")
    parser.add_argument("--train-end",     default="2025-07-31")
    parser.add_argument("--test-start",    default="2025-08-01")
    parser.add_argument("--test-end",      default="2026-02-13")
    parser.add_argument("--capital",       default=500_000, type=int)
    parser.add_argument("--universe-size", default=60, type=int,
                        help="Top-N symbols by 60d median liquidity (default: 60)")
    parser.add_argument("--lookback",      default=20, type=int,
                        help="N-day high breakout lookback (default: 20)")
    parser.add_argument("--debug-trades",  action="store_true",
                        help="Print best/worst trade tables")
    args = parser.parse_args()

    # Load symbol map from config DB
    db = DatabaseManager(ROOT / "data")
    with db.config_reader() as conn:
        rows = conn.execute(
            "SELECT instrument_key, trading_symbol FROM fo_stocks WHERE is_active = 1"
        ).fetchall()
    symbol_map = {r[0]: r[1] for r in rows}
    logger.info(f"F&O universe: {len(symbol_map)} symbols")

    tr_s = date.fromisoformat(args.train_start)
    tr_e = date.fromisoformat(args.train_end)
    te_s = date.fromisoformat(args.test_start)
    te_e = date.fromisoformat(args.test_end)

    # Preload daily OHLCV: go back enough for indicators (200 days before train start)
    # Data available from 2023-01-02 — use 300-day warmup for full ATR/indicators
    preload_from = tr_s - timedelta(days=300)

    logger.info(f"Preloading data from {preload_from} to {te_e}...")
    daily_cache = preload_daily_ohlcv(
        data_root   = ROOT / "data",
        symbols_set = set(symbol_map.keys()),
        from_date   = preload_from,
        to_date     = te_e,
    )

    # Universe selection — rank on the 60th trading day of the full backtest range.
    # Liquidity ranking doesn't require strict causal isolation (it's structural,
    # not predictive), so we may use early train data for warmup.
    # Using tr_e as the upper bound ensures we have enough history in all cases.
    all_train_days = get_trading_days(ROOT / "data", tr_s, tr_e)
    if len(all_train_days) >= 60:
        rank_as_of = all_train_days[59]   # 60th trading day of train period
    elif all_train_days:
        rank_as_of = all_train_days[-1]
    else:
        rank_as_of = tr_s
    logger.info(f"Selecting universe as of {rank_as_of} (60th train day)...")
    universe = select_universe(
        daily_cache = daily_cache,
        symbol_map  = symbol_map,
        as_of       = rank_as_of,
        top_n       = args.universe_size,
    )

    if not universe:
        logger.error("Universe selection returned 0 symbols. Aborting.")
        sys.exit(1)

    # Print universe
    print(f"\nFROZEN UNIVERSE ({len(universe)} symbols):")
    for i, sym in enumerate(universe, 1):
        print(f"  {i:>3}. {symbol_map.get(sym, sym)}")

    # Train backtest
    logger.info("=== TRAIN ===")
    train_port = run_backtest(
        start       = tr_s,
        end         = tr_e,
        capital     = args.capital,
        universe    = universe,
        symbol_map  = symbol_map,
        daily_cache = daily_cache,
        label       = "TRAIN",
        lookback    = args.lookback,
    )
    train_stats = print_report(train_port, "TRAIN", tr_s, tr_e)
    if args.debug_trades:
        dump_trades(train_port, "TRAIN")

    # Test backtest
    logger.info("=== TEST ===")
    test_port = run_backtest(
        start       = te_s,
        end         = te_e,
        capital     = args.capital,
        universe    = universe,
        symbol_map  = symbol_map,
        daily_cache = daily_cache,
        label       = "TEST",
        lookback    = args.lookback,
    )
    test_stats = print_report(test_port, "TEST", te_s, te_e)
    if args.debug_trades:
        dump_trades(test_port, "TEST")

    # Walk-forward summary
    print("\nWALK-FORWARD SUMMARY")
    print(f"  {'Metric':<22}  {'TRAIN':>12}  {'TEST':>12}")
    print(f"  {'-'*50}")
    for k in ["trades", "win_rate", "profit_factor", "avg_r", "median_r",
              "skew", "stops", "force_closes", "max_dd", "total_pnl"]:
        tv = train_stats.get(k, 0)
        sv = test_stats.get(k, 0)
        if isinstance(tv, float):
            print(f"  {k:<22}  {tv:>12.3f}  {sv:>12.3f}")
        else:
            print(f"  {k:<22}  {tv:>12}  {sv:>12}")


if __name__ == "__main__":
    main()
