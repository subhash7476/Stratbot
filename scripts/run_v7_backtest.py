"""
PixityAI v7 — Mean Reversion Baseline
=======================================
Hypothesis: Liquid NSE large-caps revert to fair value after sharp
sentiment-driven dislocations. Institutional support creates a floor.

PIPELINE:
  1. Universe: Top 60 by median 60-day traded value. Frozen for backtest.
  2. Signal:   5-day return <= -3%
               AND RSI(14) < 35
               AND Nifty Close > Nifty 200DMA  (structural uptrend gate)
  3. Entry:    T+1 open
  4. Trade:    Stop 1.5xATR below entry | Target +1.5R (fixed) | Time stop 10 days
  5. Portfolio: 0.75% risk, max 5 concurrent, 5-day re-entry guard per symbol

SPEC (locked):
  Stop:      entry - 1.5 * ATR(20)  (initial, fixed)
  Target:    entry + 1.5 * stop_dist  (= +1.5R, fixed mean-reversion target)
  Time stop: 10 trading days (reversion that hasn't worked in 10d has failed)
  No trail.  No early exit.

UNIVERSE:
  Top 60 NSE F&O equities by median 60-day daily traded value.
  Ranked at the 60th trading day of train period. Frozen.

WALK-FORWARD (full 3-year):
  Period A Train: 2023-01-02 to 2023-12-29
  Period A Test:  2024-01-02 to 2024-12-31
  Period B Train: 2025-01-02 to 2025-07-31
  Period B Test:  2025-08-01 to 2026-02-13

FEES:
  NSE equity: Brokerage Rs 20 x2, STT 0.025% sell, Exchange 0.00345%,
              Stamp 0.003% buy.

Usage:
    python scripts/run_v7_backtest.py
    python scripts/run_v7_backtest.py --debug-trades
    python scripts/run_v7_backtest.py --rsi-threshold 40
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
# Fee model
# ---------------------------------------------------------------------------
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
# Data loading
# ---------------------------------------------------------------------------

def get_trading_days(data_root: Path, from_date: date, to_date: date) -> List[date]:
    m1_dir = data_root / "market_data" / "nse" / "candles" / "1m"
    days, current = [], from_date
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
                SELECT symbol,
                    FIRST(open  ORDER BY timestamp) AS open,
                    MAX(high)                        AS high,
                    MIN(low)                         AS low,
                    LAST(close  ORDER BY timestamp)  AS close,
                    SUM(volume)                      AS volume
                FROM candles WHERE timeframe = '1m' GROUP BY symbol
            """).df()
            conn.close()
            for _, row in df.iterrows():
                sym = row["symbol"]
                if sym in symbol_rows:
                    symbol_rows[sym].append({
                        "date": td, "open": float(row["open"]),
                        "high": float(row["high"]), "low": float(row["low"]),
                        "close": float(row["close"]), "volume": float(row["volume"]),
                    })
        except Exception as e:
            logger.debug(f"Error loading {td}: {e}")
    daily_cache: Dict[str, pd.DataFrame] = {}
    for sym, rows in symbol_rows.items():
        if rows:
            daily_cache[sym] = (pd.DataFrame(rows).sort_values("date")
                                .reset_index(drop=True))
    loaded = sum(1 for v in daily_cache.values() if len(v) > 0)
    logger.info(f"Preloaded {loaded}/{len(symbols_set)} symbols.")
    return daily_cache


def preload_nifty(data_root: Path, from_date: date, to_date: date) -> Optional[pd.DataFrame]:
    d1_dir = data_root / "market_data" / "nse" / "candles" / "1d"
    rows, current = [], from_date
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
                    rows.append({"date": current, "open": float(df["open"].iloc[0]),
                                 "high": float(df["high"].iloc[0]), "low": float(df["low"].iloc[0]),
                                 "close": float(df["close"].iloc[0]), "volume": float(df["volume"].iloc[0])})
            except Exception:
                pass
        current += timedelta(days=1)
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True) if rows else None


# ---------------------------------------------------------------------------
# Universe selection
# ---------------------------------------------------------------------------

def select_universe(
    daily_cache: Dict[str, pd.DataFrame],
    symbol_map: Dict[str, str],
    as_of: date,
    top_n: int = 60,
) -> List[str]:
    liq: List[Tuple[str, float]] = []
    for sym, df in daily_cache.items():
        causal = df[df["date"] <= as_of]
        if len(causal) < 10:
            continue
        traded_val = (causal.tail(60)["close"] * causal.tail(60)["volume"]).median() / 1e7
        if traded_val > 0:
            liq.append((sym, traded_val))
    if not liq:
        logger.warning("No liquidity data for universe selection!")
        return []
    liq.sort(key=lambda x: x[1], reverse=True)
    top = [s for s, _ in liq[:top_n]]
    logger.info(f"Universe: top {len(top)} by liquidity | "
                f"#{1} {symbol_map.get(liq[0][0], liq[0][0])} {liq[0][1]:.0f}Cr | "
                f"#{top_n} {symbol_map.get(liq[len(top)-1][0], liq[len(top)-1][0])} {liq[len(top)-1][1]:.0f}Cr")
    return top


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def compute_atr(df: pd.DataFrame, period: int = 20) -> Optional[pd.Series]:
    if len(df) < period + 1:
        return None
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"]  - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def compute_rsi_arr(close_arr: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder RSI. Returns array same length as close_arr, NaN for warmup."""
    n = len(close_arr)
    rsi = np.full(n, np.nan)
    if n < period + 1:
        return rsi
    delta = np.diff(close_arr.astype(float))
    gain  = np.where(delta > 0, delta, 0.0)
    loss  = np.where(delta < 0, -delta, 0.0)
    # Wilder smoothing: seed with simple average, then exponential
    avg_g = gain[:period].mean()
    avg_l = loss[:period].mean()
    for i in range(period, n - 1):
        avg_g = (avg_g * (period - 1) + gain[i]) / period
        avg_l = (avg_l * (period - 1) + loss[i]) / period
        rs = avg_g / avg_l if avg_l > 0 else 100.0
        rsi[i + 1] = 100.0 - 100.0 / (1.0 + rs)
    return rsi


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def generate_signals(
    universe:    List[str],
    symbol_map:  Dict[str, str],
    daily_cache: Dict[str, pd.DataFrame],
    nifty_df:    Optional[pd.DataFrame],
    trading_days: List[date],
    ret_lookback: int  = 5,
    ret_threshold: float = -0.03,   # 5d return <= -3%
    rsi_period:   int  = 14,
    rsi_threshold: float = 35.0,    # RSI < 35
) -> Dict[date, List[dict]]:
    """
    Signal: Close[T] down >= 3% from 5 days ago
            AND RSI(14)[T] < 35
            AND Nifty[T] >= Nifty 200DMA[T]
    Entry:  T+1 open.
    """
    signals_by_entry: Dict[date, List[dict]] = defaultdict(list)

    # Pre-compute Nifty 200DMA gate (date -> bool)
    nifty_gate: Dict[date, bool] = {}
    if nifty_df is not None and not nifty_df.empty:
        nifty_c   = nifty_df.set_index("date")["close"]
        nifty_200 = nifty_c.rolling(200, min_periods=100).mean()
        for td in trading_days:
            if td in nifty_c.index and td in nifty_200.index:
                c = float(nifty_c.loc[td])
                m = float(nifty_200.loc[td])
                nifty_gate[td] = (not np.isnan(m)) and (c >= m)
            else:
                nifty_gate[td] = True
        n_active = sum(nifty_gate.values())
        logger.info(f"Nifty 200DMA gate: {n_active}/{len(trading_days)} days open")
    else:
        nifty_gate = {td: True for td in trading_days}
        logger.warning("No Nifty data — 200DMA gate disabled")

    need = max(rsi_period + 2, ret_lookback + 1, 25)  # min bars needed

    for sym in universe:
        df = daily_cache.get(sym)
        if df is None or df.empty:
            continue

        trading_sym = symbol_map.get(sym, sym.split("|")[-1])
        atr_series  = compute_atr(df, 20)
        if atr_series is None:
            continue

        close_arr = df["close"].values
        atr_arr   = atr_series.values
        rsi_arr   = compute_rsi_arr(close_arr, rsi_period)
        date_to_pos: Dict[date, int] = {d: i for i, d in enumerate(df["date"].values)}

        for day_i, td in enumerate(trading_days):
            # Regime gate first (cheapest)
            if not nifty_gate.get(td, True):
                continue

            pos = date_to_pos.get(td)
            if pos is None or pos < need:
                continue

            # RSI filter
            rsi_val = rsi_arr[pos]
            if np.isnan(rsi_val) or rsi_val >= rsi_threshold:
                continue

            # 5-day return filter
            close_t     = float(close_arr[pos])
            close_5d_ago = float(close_arr[pos - ret_lookback])
            if close_5d_ago <= 0:
                continue
            ret5 = (close_t - close_5d_ago) / close_5d_ago
            if ret5 > ret_threshold:          # ret_threshold is -0.03 (negative)
                continue

            # ATR
            daily_atr = float(atr_arr[pos])
            if np.isnan(daily_atr) or daily_atr <= 0:
                continue

            # Entry is T+1
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
                "rsi":            rsi_val,
                "ret5d":          ret5,
            })

    total = sum(len(v) for v in signals_by_entry.values())
    logger.info(f"Signals generated: {total} across {len(signals_by_entry)} entry days")
    return signals_by_entry


# ---------------------------------------------------------------------------
# Trade state machine — mean reversion (fixed target, time stop, no trail)
# ---------------------------------------------------------------------------

class Trade:
    """
    v7 Mean-reversion trade. Long only.

    Stop:   entry - 1.5 * ATR  (fixed)
    Target: entry + 1.5 * stop_dist  (= +1.5R, fixed)
    Time:   10 trading days max
    No trail, no early exit.
    """

    STOP_MULT   = 1.5   # initial stop = 1.5 x ATR
    TARGET_R    = 1.5   # target = 1.5R
    MAX_BARS    = 10

    def __init__(
        self,
        symbol:         str,
        trading_symbol: str,
        entry_price:    float,
        qty:            int,
        stop:           float,
        target:         float,
        daily_atr:      float,
        entry_date:     date,
        signal_date:    date,
        rsi_at_signal:  float,
        ret5d:          float,
    ):
        self.symbol         = symbol
        self.trading_symbol = trading_symbol
        self.entry_price    = entry_price
        self.qty            = qty
        self.stop           = stop
        self.target         = target
        self.daily_atr      = daily_atr
        self.entry_date     = entry_date
        self.signal_date    = signal_date
        self.rsi_at_signal  = rsi_at_signal
        self.ret5d          = ret5d
        self.direction      = "LONG"

        self.bars_open   = 0
        self.closed      = False
        self.exit_price: Optional[float] = None
        self.exit_reason: Optional[str]  = None
        self.exit_date:   Optional[date] = None

    @property
    def stop_dist(self) -> float:
        return abs(self.entry_price - self.stop)

    def current_r(self, price: float) -> float:
        return (price - self.entry_price) / self.stop_dist if self.stop_dist else 0.0

    def update_bar(self, bar: pd.Series) -> Optional[str]:
        """
        Order of operations each bar:
          1. Stop check (gap protection via open)
          2. Target check (use high — could touch intraday)
          3. Time stop at end of bar 10
        """
        if self.closed:
            return None

        open_  = float(bar["open"])
        high   = float(bar["high"])
        low    = float(bar["low"])
        close  = float(bar["close"])
        bar_dt = bar["date"] if isinstance(bar["date"], date) else bar["date"].date()

        self.bars_open += 1

        # 1. Stop — gap protection
        if open_ <= self.stop or low <= self.stop:
            exit_p = min(open_, self.stop)
            self._close(exit_p, "STOP", bar_dt)
            return "STOP"

        # 2. Target — use high for intraday touch
        if high >= self.target:
            self._close(self.target, "TARGET", bar_dt)
            return "TARGET"

        # 3. Time stop
        if self.bars_open >= self.MAX_BARS:
            self._close(close, "TIME_STOP", bar_dt)
            return "TIME_STOP"

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
        return gross - (fee_func(self.entry_price, self.exit_price, self.qty,
                                 self.direction) if fee_func else 0.0)

    def r_multiple(self) -> float:
        if self.exit_price is None or self.stop_dist == 0:
            return 0.0
        return (self.exit_price - self.entry_price) * self.qty / (self.stop_dist * self.qty)


# ---------------------------------------------------------------------------
# Portfolio simulator
# ---------------------------------------------------------------------------

class Portfolio:
    MAX_POSITIONS = 5
    RISK_PCT      = 0.0075
    REENTRY_DAYS  = 5

    def __init__(self, capital: float = 500_000.0):
        self.equity    = capital
        self.initial   = capital
        self.peak      = capital
        self.open_trades: List[Trade]   = []
        self.closed_trades: List[Trade] = []
        self.equity_curve: List[Tuple[date, float]] = []
        self.max_dd    = 0.0
        self._last_exit: Dict[str, date] = {}

    def _update_dd(self) -> None:
        self.peak   = max(self.peak, self.equity)
        dd          = (self.peak - self.equity) / self.peak
        self.max_dd = max(self.max_dd, dd)

    def open_symbols(self) -> Set[str]:
        return {t.symbol for t in self.open_trades}

    def _in_cooldown(self, symbol: str, today: date) -> bool:
        last = self._last_exit.get(symbol)
        return last is not None and (today - last).days < self.REENTRY_DAYS

    def can_enter(self, symbol: str, today: date) -> bool:
        return (len(self.open_trades) < self.MAX_POSITIONS
                and symbol not in self.open_symbols()
                and not self._in_cooldown(symbol, today))

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
        target   = entry_price + stop_dist * Trade.TARGET_R
        trade = Trade(
            symbol         = sig["symbol"],
            trading_symbol = sig["trading_symbol"],
            entry_price    = entry_price,
            qty            = qty,
            stop           = stop,
            target         = target,
            daily_atr      = daily_atr,
            entry_date     = today,
            signal_date    = sig["signal_date"],
            rsi_at_signal  = sig["rsi"],
            ret5d          = sig["ret5d"],
        )
        self.open_trades.append(trade)
        return trade

    def process_day(self, bar_by_symbol: Dict[str, pd.Series], today: date) -> None:
        to_close = []
        for trade in self.open_trades:
            bar = bar_by_symbol.get(trade.symbol)
            if bar is None:
                continue
            reason = trade.update_bar(bar)
            if reason:
                self.equity += trade.pnl(compute_fees)
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
            self.equity += trade.pnl(compute_fees)
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

    print(f"\n{'='*65}")
    print(f"  PixityAI v7 — {label}")
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

    buckets = [
        ("< -1R",        sum(1 for r in rs if r < -1.0)),
        ("-1 to -0.5R",  sum(1 for r in rs if -1.0 <= r < -0.5)),
        ("-0.5 to 0R",   sum(1 for r in rs if -0.5 <= r < 0)),
        ("0 to +0.5R",   sum(1 for r in rs if 0 <= r < 0.5)),
        ("+0.5 to +1R",  sum(1 for r in rs if 0.5 <= r < 1.0)),
        ("+1 to +1.4R",  sum(1 for r in rs if 1.0 <= r < 1.4)),
        ("+1.4 to +1.6R",sum(1 for r in rs if 1.4 <= r < 1.6)),   # target cluster
        (">= +1.6R",     sum(1 for r in rs if r >= 1.6)),
    ]
    print(f"\n  R DISTRIBUTION:")
    for lbl, cnt in buckets:
        print(f"    {lbl:<17} {cnt:>3}  {'#' * cnt}")

    sk = 0.0
    if len(rs) >= 5:
        from scipy.stats import skew as _skew
        sk = float(_skew(rs))
        verdict = ("RIGHT-SKEWED [good]" if sk > 0.1 else
                   "SYMMETRIC"           if sk > -0.1 else
                   "LEFT-SKEWED [bad]")
        print(f"\n  R skew: {sk:.3f}  ->  {verdict}")

    avg_rsi = float(np.mean([t.rsi_at_signal for t in trades]))
    avg_ret = float(np.mean([t.ret5d * 100   for t in trades]))
    print(f"\n  Signal quality: avg RSI @ entry={avg_rsi:.1f}  avg 5d-ret={avg_ret:.1f}%")

    tgt_n   = exits.get("TARGET", 0)
    stop_n  = exits.get("STOP", 0)
    ttime_n = exits.get("TIME_STOP", 0)
    fc_n    = exits.get("FORCE_CLOSE", 0)

    print(f"\n  EVAL CRITERIA:")
    print(f"    PF >= 1.15:          {'PASS' if pf >= 1.15 else 'FAIL'}  ({pf:.2f})")
    print(f"    Avg R >= 0.25:       {'PASS' if avg_r >= 0.25 else 'FAIL'}  ({avg_r:.3f})")
    print(f"    Expectancy > 0:      {'PASS' if exp > 0 else 'FAIL'}  (Rs {exp:,.0f})")
    print(f"    Target hit rate:     {tgt_n}/{len(trades)} = {tgt_n/len(trades)*100:.0f}%")
    print(f"    Stop hit rate:       {stop_n}/{len(trades)} = {stop_n/len(trades)*100:.0f}%")
    print(f"    Time-stop rate:      {ttime_n}/{len(trades)} = {ttime_n/len(trades)*100:.0f}%")
    print(f"{'='*65}\n")

    return {
        "trades": len(trades), "win_rate": wr, "total_pnl": total,
        "expectancy": exp, "profit_factor": pf, "max_dd": dd,
        "avg_r": avg_r, "median_r": med_r, "skew": sk,
        "targets": tgt_n, "stops": stop_n, "time_stops": ttime_n, "force_closes": fc_n,
    }


def dump_trades(portfolio: Portfolio, label: str, n: int = 5) -> None:
    trades = sorted(portfolio.closed_trades, key=lambda t: t.r_multiple())
    if not trades:
        return

    def _tbl(subset: List[Trade], header: str) -> None:
        print(f"\n  {header}")
        print(f"  {'#':>3}  {'Symbol':<14}  {'Entry':>8}  {'Exit':>8}  "
              f"{'ATR':>6}  {'Stop':>8}  {'Target':>8}  {'Days':>4}  "
              f"{'R':>6}  {'PnL':>9}  {'RSI':>5}  {'5dRet':>6}  Reason")
        print(f"  {'-'*110}")
        for i, t in enumerate(subset, 1):
            pnl = t.pnl(compute_fees)
            r   = t.r_multiple()
            print(f"  {i:>3}  {t.trading_symbol[:14]:<14}  "
                  f"{t.entry_price:>8.2f}  {t.exit_price:>8.2f}  "
                  f"{t.daily_atr:>6.2f}  {t.stop:>8.2f}  {t.target:>8.2f}  "
                  f"{t.bars_open:>4}  {r:>6.2f}  {pnl:>9,.0f}  "
                  f"{t.rsi_at_signal:>5.1f}  {t.ret5d*100:>5.1f}%  "
                  f"{t.exit_reason}  [{t.entry_date}]")

    print(f"\n{'='*65}")
    print(f"  {label} — TRADE SAMPLES")
    print(f"{'='*65}")
    _tbl(trades[-n:][::-1], f"TOP {n} BEST")
    _tbl(trades[:n],        f"TOP {n} WORST")


# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------

def run_backtest(
    start:        date,
    end:          date,
    capital:      float,
    universe:     List[str],
    symbol_map:   Dict[str, str],
    daily_cache:  Dict[str, pd.DataFrame],
    nifty_df:     Optional[pd.DataFrame],
    label:        str   = "BACKTEST",
    rsi_threshold: float = 35.0,
    ret_threshold: float = -0.03,
) -> Portfolio:
    logger.info(f"[{label}] {start} to {end}")
    trading_days = get_trading_days(ROOT / "data", start, end)
    logger.info(f"Trading days: {len(trading_days)}")

    signals_by_entry = generate_signals(
        universe      = universe,
        symbol_map    = symbol_map,
        daily_cache   = daily_cache,
        nifty_df      = nifty_df,
        trading_days  = trading_days,
        rsi_threshold = rsi_threshold,
        ret_threshold = ret_threshold,
    )

    portfolio = Portfolio(capital=capital)

    for td in trading_days:
        bar_today: Dict[str, pd.Series] = {}
        for sym in universe:
            df = daily_cache.get(sym)
            if df is None:
                continue
            row = df[df["date"] == td]
            if not row.empty:
                bar_today[sym] = row.iloc[0]

        for sig in signals_by_entry.get(td, []):
            bar = bar_today.get(sig["symbol"])
            if bar is None:
                continue
            entry_price = float(bar["open"])
            if entry_price <= 0:
                continue
            trade = portfolio.enter(sig, entry_price, td)
            if trade:
                logger.debug(
                    f"  ENTRY {sig['trading_symbol']} @ {entry_price:.2f}  "
                    f"stop={trade.stop:.2f}  tgt={trade.target:.2f}  "
                    f"RSI={sig['rsi']:.1f}  ret5={sig['ret5d']*100:.1f}%  [{td}]"
                )

        portfolio.process_day(bar_today, td)

    last_bars: Dict[str, pd.Series] = {}
    for sym in universe:
        df = daily_cache.get(sym)
        if df is None:
            continue
        row = df[df["date"] <= end].tail(1)
        if not row.empty:
            last_bars[sym] = row.iloc[0]

    if portfolio.open_trades:
        logger.info(f"Force-closing {len(portfolio.open_trades)} open positions")
        portfolio.force_close_all(last_bars, end)

    return portfolio


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PixityAI v7 Mean Reversion Backtest")
    parser.add_argument("--capital",       default=500_000, type=int)
    parser.add_argument("--universe-size", default=60, type=int)
    parser.add_argument("--rsi-threshold", default=35.0, type=float,
                        help="RSI oversold threshold (default 35)")
    parser.add_argument("--ret-threshold", default=-3.0, type=float,
                        help="5-day return threshold in pct, e.g. -3 means <= -3%% (default -3)")
    parser.add_argument("--debug-trades",  action="store_true")
    args = parser.parse_args()

    ret_thresh = args.ret_threshold / 100.0   # convert % to decimal

    db = DatabaseManager(ROOT / "data")
    with db.config_reader() as conn:
        rows = conn.execute(
            "SELECT instrument_key, trading_symbol FROM fo_stocks WHERE is_active = 1"
        ).fetchall()
    symbol_map = {r[0]: r[1] for r in rows}
    logger.info(f"F&O universe: {len(symbol_map)} symbols")
    logger.info(f"Signal params: RSI < {args.rsi_threshold}  |  5d return <= {args.ret_threshold:.1f}%")

    # Full date range: 2023 train through 2026 test
    all_start = date(2023, 1, 2)
    all_end   = date(2026, 2, 13)
    preload_from = all_start   # data starts here; no warmup available before

    logger.info(f"Preloading data {preload_from} to {all_end}...")
    daily_cache = preload_daily_ohlcv(
        data_root   = ROOT / "data",
        symbols_set = set(symbol_map.keys()),
        from_date   = preload_from,
        to_date     = all_end,
    )

    logger.info("Loading Nifty daily for 200DMA gate...")
    nifty_df = preload_nifty(ROOT / "data", preload_from, all_end)
    if nifty_df is not None:
        logger.info(f"Nifty: {len(nifty_df)} days ({nifty_df['date'].min()} to {nifty_df['date'].max()})")

    # Four periods — two walk-forward pairs
    periods = [
        (date(2023, 1,  2), date(2023, 12, 29), "TRAIN-A (2023)"),
        (date(2024, 1,  2), date(2024, 12, 31), "TEST-A  (2024)"),
        (date(2025, 1,  2), date(2025,  7, 31), "TRAIN-B (2025-H1)"),
        (date(2025, 8,  1), date(2026,  2, 13), "TEST-B  (2025-H2)"),
    ]

    # Universe A: ranked at 60th train-A day
    train_a_days = get_trading_days(ROOT / "data", date(2023, 1, 2), date(2023, 12, 29))
    rank_a = train_a_days[59] if len(train_a_days) >= 60 else train_a_days[-1]
    universe_a = select_universe(daily_cache, symbol_map, rank_a, args.universe_size)

    # Universe B: ranked at 60th train-B day (different liquidity regime)
    train_b_days = get_trading_days(ROOT / "data", date(2025, 1, 2), date(2025, 7, 31))
    rank_b = train_b_days[59] if len(train_b_days) >= 60 else train_b_days[-1]
    universe_b = select_universe(daily_cache, symbol_map, rank_b, args.universe_size)

    logger.info(f"Universe A (2023-2024): ranked as of {rank_a}")
    logger.info(f"Universe B (2025-2026): ranked as of {rank_b}")

    all_stats = {}
    for i, (p_start, p_end, p_label) in enumerate(periods):
        universe = universe_a if i < 2 else universe_b
        logger.info(f"\n=== {p_label} ===")
        port = run_backtest(
            start         = p_start,
            end           = p_end,
            capital       = args.capital,
            universe      = universe,
            symbol_map    = symbol_map,
            daily_cache   = daily_cache,
            nifty_df      = nifty_df,
            label         = p_label,
            rsi_threshold = args.rsi_threshold,
            ret_threshold = ret_thresh,
        )
        stats = print_report(port, p_label, p_start, p_end)
        all_stats[p_label] = stats
        if args.debug_trades:
            dump_trades(port, p_label)

    # Summary table
    print("\n" + "="*75)
    print("  WALK-FORWARD SUMMARY — v7 Mean Reversion")
    print("="*75)
    keys = ["trades", "win_rate", "profit_factor", "avg_r", "median_r",
            "targets", "stops", "time_stops", "max_dd", "total_pnl"]
    print(f"  {'Metric':<22}  {'TRAIN-A':>12}  {'TEST-A':>12}  {'TRAIN-B':>12}  {'TEST-B':>12}")
    print(f"  {'-'*66}")
    labels = list(all_stats.keys())
    for k in keys:
        vals = [all_stats.get(lb, {}).get(k, 0) for lb in labels]
        if isinstance(vals[0], float):
            row = "  ".join(f"{v:>12.3f}" for v in vals)
        else:
            row = "  ".join(f"{v:>12}" for v in vals)
        print(f"  {k:<22}  {row}")


if __name__ == "__main__":
    main()
