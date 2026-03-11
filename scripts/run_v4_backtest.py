"""
PixityAI v4 — Daily Expansion Backtest
========================================
Hypothesis: Daily compression -> multi-day directional expansion.

PIPELINE:
  1. Daily Compression Scanner (existing, reused)
  2. Daily Breakout Trigger (new): Close[T] > 20-bar prior high/low
  3. Entry: T+1 open
  4. Trade state machine: stop / target / breakeven trail / early-failure / time-stop
  5. Portfolio simulation: max 3 positions, risk-based sizing, Nifty soft bias

SPEC (locked):
  Stop:         1.5 × ATR(20) from Day T
  Target:       3.0 × ATR(20)  (= 2R)
  Breakeven:    at +1.5R (stop moves to entry)
  Trail:        after +2R -> trail at 1×ATR below price (long) / above price (short)
  Early exit:   if after 3 trading days < +0.5R -> exit at next open
  Time stop:    10 trading days

WALK-FORWARD:
  Train: 2024-06-01 to 2025-05-31   (~12 months, data from 2025-01-01 onwards
         so effective train starts 2025-01-01 with warmup from Oct 2024)
  Test:  2025-06-01 to 2026-02-13   (~8.5 months)

  NOTE: equity 1m data starts 2024-10-17. We use 200-day preload window.
  Effective first usable scan date: ~2025-06-01 (60d history needed = by ~Jan 2025).
  Train will be 2025-01-01 -> 2025-07-31, Test 2025-08-01 -> 2026-02-13.

FEES:
  NSE equity delivery / intraday style:
  Brokerage: Rs 20 flat x2
  STT: 0.025% sell-side
  Exchange: 0.00345% turnover
  Stamp: 0.003% buy-side

Usage:
    python scripts/run_v4_backtest.py
    python scripts/run_v4_backtest.py --train-start 2025-01-01 --train-end 2025-07-31
                                      --test-start  2025-08-01 --test-end  2026-02-13
    python scripts/run_v4_backtest.py --debug-trades
"""

from __future__ import annotations

import os
import sys
import argparse
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

os.environ["PYTHONIOENCODING"] = "utf-8"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.strategies.expansion_v3.daily_compression_scanner import (
    DailyCompressionScanner, CompressionConfig,
)
from core.strategies.expansion_v3.daily_breakout_trigger import (
    DailyBreakoutTrigger, DailyTriggerConfig, DailySignal,
)
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
STT_RATE      = 0.00025    # sell side
BROKERAGE     = 20.0       # per order, x2
EXCHANGE_RATE = 0.0000345  # composite
STAMP_RATE    = 0.00003    # buy side


def compute_fees(entry: float, exit_p: float, qty: int, direction: str) -> float:
    buy_val  = entry  * qty if direction == "LONG" else exit_p * qty
    sell_val = exit_p * qty if direction == "LONG" else entry  * qty
    turnover = buy_val + sell_val
    return (sell_val * STT_RATE) + (BROKERAGE * 2) + (turnover * EXCHANGE_RATE) + (buy_val * STAMP_RATE)


# ---------------------------------------------------------------------------
# Nifty soft-bias sizing
# ---------------------------------------------------------------------------

def nifty_slope(nifty_df: pd.DataFrame, up_to: date) -> float:
    """(EMA20_today - EMA20_5d_ago) / EMA20_5d_ago  using daily data."""
    df = nifty_df[nifty_df["date"] <= up_to].tail(30)
    if len(df) < 22:
        return 0.0
    ema = df["close"].ewm(span=20, adjust=False).mean()
    return float((ema.iloc[-1] - ema.iloc[-6]) / ema.iloc[-6]) if len(ema) >= 6 else 0.0


def size_mult(slope: float, direction: str, threshold: float = 0.001) -> float:
    if slope > threshold:
        return 1.0 if direction == "LONG" else 0.6
    elif slope < -threshold:
        return 1.0 if direction == "SHORT" else 0.6
    return 0.85


# ---------------------------------------------------------------------------
# Trade state machine
# ---------------------------------------------------------------------------

class Trade:
    """
    Single position lifecycle on daily bars.

    Spec:
      Stop:        entry - 1.5*ATR  (long) / entry + 1.5*ATR (short)
      Target:      entry + 3.0*ATR  (long) / entry - 3.0*ATR (short)
      Breakeven:   move stop to entry after +1.5R
      Trail:       after +2R, trail stop at 1*ATR below price
      Early exit:  if bars_open == 3 and current_r < 0.5 -> exit at next open
      Time stop:   10 trading days
    """

    MAX_BARS        = 10
    EARLY_EXIT_BARS = 3      # check at end of day 3
    EARLY_EXIT_R    = 0.5    # must be above this R or exit
    BREAKEVEN_R     = 1.5
    TRAIL_R         = 2.0
    TRAIL_ATR_MULT  = 1.0    # trail at 1x ATR below/above price

    def __init__(
        self,
        signal: DailySignal,
        entry_price: float,
        qty: int,
        stop: float,
        target: float,
        daily_atr: float,
        early_exit_enabled: bool = True,
    ):
        self.signal       = signal
        self.entry_price  = entry_price
        self.qty          = qty
        self.stop         = stop
        self.target       = target
        self.daily_atr    = daily_atr
        self.direction    = signal.direction
        self.early_exit_enabled = early_exit_enabled

        self.bars_open    = 0
        self.trailed      = False
        self.at_breakeven = False
        self.pending_early_exit = False  # flag: exit at open of next bar

        self.closed       = False
        self.exit_price: Optional[float] = None
        self.exit_reason: Optional[str]  = None
        self.exit_date:   Optional[date] = None

    @property
    def stop_dist(self) -> float:
        return abs(self.entry_price - self.stop)

    def current_r(self, price: float) -> float:
        if self.stop_dist == 0:
            return 0.0
        if self.direction == "LONG":
            return (price - self.entry_price) / self.stop_dist
        return (self.entry_price - price) / self.stop_dist

    def update_bar(self, bar: pd.Series) -> Optional[str]:
        """
        Process one daily bar.
        bar must have: date, open, high, low, close
        Returns exit reason or None.

        Order of operations:
          1. If pending_early_exit: exit at open (set at end of previous bar)
          2. Stop check (intrabar, use open for gap protection)
          3. Target check
          4. Update trail / breakeven
          5. Time stop at end of bar
          6. Early failure check at end of bar (set flag for next bar)
        """
        if self.closed:
            return None

        open_  = float(bar["open"])
        high   = float(bar["high"])
        low    = float(bar["low"])
        close  = float(bar["close"])
        bar_dt = bar["date"] if isinstance(bar["date"], date) else bar["date"].date()

        # 1. Pending early exit: exit at today's open
        if self.pending_early_exit:
            self._close(open_, "EARLY_EXIT", bar_dt)
            return "EARLY_EXIT"

        self.bars_open += 1

        # 2. Stop check — gap protection: use min(open, stop) for longs
        if self.direction == "LONG":
            if open_ <= self.stop or low <= self.stop:
                exit_p = min(open_, self.stop)
                self._close(exit_p, "STOP", bar_dt)
                return "STOP"
        else:
            if open_ >= self.stop or high >= self.stop:
                exit_p = max(open_, self.stop)
                self._close(exit_p, "STOP", bar_dt)
                return "STOP"

        # 3. Target check
        if self.direction == "LONG" and high >= self.target:
            self._close(self.target, "TARGET", bar_dt)
            return "TARGET"
        elif self.direction == "SHORT" and low <= self.target:
            self._close(self.target, "TARGET", bar_dt)
            return "TARGET"

        # 4. Update breakeven / trail using bar close
        r_now = self.current_r(close)

        if not self.at_breakeven and r_now >= self.BREAKEVEN_R:
            self.stop = self.entry_price
            self.at_breakeven = True

        if r_now >= self.TRAIL_R:
            # Trail stop at 1*ATR below/above close
            if self.direction == "LONG":
                trail_stop = close - self.TRAIL_ATR_MULT * self.daily_atr
                self.stop = max(self.stop, trail_stop)
            else:
                trail_stop = close + self.TRAIL_ATR_MULT * self.daily_atr
                self.stop = min(self.stop, trail_stop)
            self.trailed = True

        # 5. Time stop
        if self.bars_open >= self.MAX_BARS:
            self._close(close, "TIME_STOP", bar_dt)
            return "TIME_STOP"

        # 6. Early failure check (at end of bar 3) — skipped if disabled
        if self.early_exit_enabled and \
                self.bars_open == self.EARLY_EXIT_BARS and r_now < self.EARLY_EXIT_R:
            self.pending_early_exit = True  # exit at next bar's open

        return None

    def _close(self, price: float, reason: str, dt) -> None:
        self.exit_price  = float(price)
        self.exit_reason = reason
        self.exit_date   = dt if isinstance(dt, date) else dt.date()
        self.closed      = True

    def pnl(self, fee_func=None) -> float:
        if self.exit_price is None:
            return 0.0
        gross = (self.exit_price - self.entry_price) * self.qty \
                if self.direction == "LONG" \
                else (self.entry_price - self.exit_price) * self.qty
        fees = fee_func(self.entry_price, self.exit_price, self.qty, self.direction) \
               if fee_func else 0.0
        return gross - fees

    def r_multiple(self) -> float:
        if self.exit_price is None or self.stop_dist == 0:
            return 0.0
        return self.pnl() / (self.stop_dist * self.qty)


# ---------------------------------------------------------------------------
# Portfolio simulator
# ---------------------------------------------------------------------------

class Portfolio:
    MAX_POSITIONS = 3
    RISK_PCT      = 0.0075   # 0.75% per trade
    STOP_MULT     = 1.5      # stop distance = 1.5 * ATR
    TARGET_MULT   = 3.0      # target = 3.0 * ATR (2R)

    def __init__(self, capital: float = 500_000.0, early_exit_enabled: bool = True):
        self.equity   = capital
        self.initial  = capital
        self.peak     = capital
        self.open_trades: List[Trade] = []
        self.closed_trades: List[Trade] = []
        self.equity_curve: List[Tuple[date, float]] = []
        self.max_dd   = 0.0
        self.early_exit_enabled = early_exit_enabled

    def _update_dd(self) -> None:
        self.peak = max(self.peak, self.equity)
        dd = (self.peak - self.equity) / self.peak
        self.max_dd = max(self.max_dd, dd)

    def open_symbols(self) -> set:
        return {t.signal.symbol for t in self.open_trades}

    def can_enter(self, symbol: str) -> bool:
        if len(self.open_trades) >= self.MAX_POSITIONS:
            return False
        if symbol in self.open_symbols():
            return False
        return True

    def enter(self, signal: DailySignal, entry_price: float, nifty_slope_val: float) -> Optional[Trade]:
        if not self.can_enter(signal.symbol):
            return None

        stop_dist = signal.daily_atr * self.STOP_MULT
        if stop_dist <= 0 or entry_price <= 0:
            return None

        raw_risk  = self.equity * self.RISK_PCT
        mult      = size_mult(nifty_slope_val, signal.direction)
        risk_amt  = raw_risk * mult
        qty       = max(1, int(risk_amt / stop_dist))

        if signal.direction == "LONG":
            stop   = entry_price - stop_dist
            target = entry_price + signal.daily_atr * self.TARGET_MULT
        else:
            stop   = entry_price + stop_dist
            target = entry_price - signal.daily_atr * self.TARGET_MULT

        trade = Trade(
            signal              = signal,
            entry_price         = entry_price,
            qty                 = qty,
            stop                = stop,
            target              = target,
            daily_atr           = signal.daily_atr,
            early_exit_enabled  = self.early_exit_enabled,
        )
        self.open_trades.append(trade)
        return trade

    def process_day(self, bar_by_symbol: Dict[str, pd.Series], today: date) -> None:
        """Update all open trades with today's daily bar."""
        to_close = []
        for trade in self.open_trades:
            bar = bar_by_symbol.get(trade.signal.symbol)
            if bar is None:
                continue
            reason = trade.update_bar(bar)
            if reason:
                pnl = trade.pnl(compute_fees)
                self.equity += pnl
                self._update_dd()
                to_close.append(trade)
                self.closed_trades.append(trade)

        for t in to_close:
            self.open_trades.remove(t)

        self.equity_curve.append((today, self.equity))

    def force_close_all(self, bar_by_symbol: Dict[str, pd.Series], today: date) -> None:
        for trade in list(self.open_trades):
            bar = bar_by_symbol.get(trade.signal.symbol)
            price = float(bar["close"]) if bar is not None else trade.entry_price
            trade._close(price, "FORCE_CLOSE", today)
            pnl = trade.pnl(compute_fees)
            self.equity += pnl
            self._update_dd()
            self.closed_trades.append(trade)
        self.open_trades.clear()


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(portfolio: Portfolio, label: str, start: date, end: date) -> dict:
    trades = portfolio.closed_trades
    if not trades:
        print(f"\n[{label}] No trades.")
        return {}

    pnls    = [t.pnl(compute_fees) for t in trades]
    rs      = [t.r_multiple() for t in trades]
    winners = [p for p in pnls if p > 0]
    losers  = [p for p in pnls if p <= 0]
    wr      = len(winners) / len(trades) * 100
    exp     = np.mean(pnls)
    pf_den  = abs(sum(losers)) if losers else 1e-9
    pf      = sum(winners) / pf_den if winners else 0.0
    total   = sum(pnls)
    avg_r   = np.mean(rs)
    med_r   = np.median(rs)
    dd      = portfolio.max_dd * 100
    ret     = (portfolio.equity / portfolio.initial - 1) * 100

    exits = defaultdict(int)
    for t in trades:
        exits[t.exit_reason] += 1

    print(f"\n{'='*65}")
    print(f"  PixityAI v4 — {label}")
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
        ("< -1R",       sum(1 for r in rs if r < -1.0)),
        ("-1 to -0.5R", sum(1 for r in rs if -1.0 <= r < -0.5)),
        ("-0.5 to 0R",  sum(1 for r in rs if -0.5 <= r < 0)),
        ("0 to +0.5R",  sum(1 for r in rs if 0 <= r < 0.5)),
        ("+0.5 to +1R", sum(1 for r in rs if 0.5 <= r < 1.0)),
        ("+1 to +1.5R", sum(1 for r in rs if 1.0 <= r < 1.5)),
        ("+1.5 to +2R", sum(1 for r in rs if 1.5 <= r < 2.0)),
        (">= +2R",      sum(1 for r in rs if r >= 2.0)),
    ]
    print(f"\n  R DISTRIBUTION:")
    for lbl, cnt in buckets:
        bar = "#" * cnt
        print(f"    {lbl:<16} {cnt:>3}  {bar}")

    # Skew
    if len(rs) >= 5:
        from scipy.stats import skew as _skew
        sk = _skew(rs)
        verdict = "RIGHT-SKEWED [good]" if sk > 0.1 else \
                  "SYMMETRIC"           if sk > -0.1 else \
                  "LEFT-SKEWED [bad]"
        print(f"\n  R skew: {sk:.3f}  ->  {verdict}")

    # Directional
    longs  = [t for t in trades if t.direction == "LONG"]
    shorts = [t for t in trades if t.direction == "SHORT"]
    if longs:
        lp  = sum(t.pnl(compute_fees) for t in longs)
        lwr = sum(1 for t in longs if t.pnl(compute_fees) > 0) / len(longs) * 100
        print(f"\n  LONG:  {len(longs):>3} trades  WR={lwr:.0f}%  PnL=Rs {lp:,.0f}")
    if shorts:
        sp  = sum(t.pnl(compute_fees) for t in shorts)
        swr = sum(1 for t in shorts if t.pnl(compute_fees) > 0) / len(shorts) * 100
        print(f"  SHORT: {len(shorts):>3} trades  WR={swr:.0f}%  PnL=Rs {sp:,.0f}")

    # Kill criteria
    print(f"\n  KILL CRITERIA:")
    print(f"    Expectancy > 0:      {'PASS' if exp > 0 else 'FAIL'}")
    print(f"    Profit Factor >= 1.2:{'PASS' if pf >= 1.2 else 'FAIL'}")
    print(f"    Avg R >= 0.25:       {'PASS' if avg_r >= 0.25 else 'FAIL'}")
    print(f"    Targets hit:         {exits.get('TARGET', 0)} / {len(trades)}")
    print(f"    Time-stop dominated: {'YES [bad]' if exits.get('TIME_STOP', 0) > len(trades) * 0.6 else 'no'}")
    print(f"{'='*65}\n")

    return {
        "trades": len(trades), "win_rate": wr, "total_pnl": total,
        "expectancy": exp, "profit_factor": pf, "max_dd": dd,
        "avg_r": avg_r, "median_r": med_r, "skew": sk if len(rs) >= 5 else 0.0,
        "targets": exits.get("TARGET", 0), "time_stops": exits.get("TIME_STOP", 0),
    }


def dump_trades(portfolio: Portfolio, label: str, n_best: int = 10, n_worst: int = 10) -> None:
    trades = sorted(portfolio.closed_trades, key=lambda t: t.r_multiple())
    if not trades:
        return

    def _print_trade_table(subset: List[Trade], header: str) -> None:
        print(f"\n  {header}")
        print(f"  {'#':>3}  {'Symbol':<12}  {'Dir':<5}  {'Entry':>8}  {'Exit':>8}  "
              f"{'ATR':>6}  {'Stop':>8}  {'Target':>8}  {'Days':>4}  "
              f"{'R':>6}  {'PnL':>8}  Reason       Entry-date")
        print(f"  {'-'*110}")
        for i, t in enumerate(subset, 1):
            pnl = t.pnl(compute_fees)
            r   = t.r_multiple()
            tgt_pct = abs(t.target - t.entry_price) / t.entry_price * 100 if t.entry_price else 0
            stp_pct = abs(t.stop   - t.entry_price) / t.entry_price * 100 if t.entry_price else 0
            print(f"  {i:>3}  {t.signal.trading_symbol[:12]:<12}  {t.direction:<5}  "
                  f"{t.entry_price:>8.2f}  {t.exit_price:>8.2f}  "
                  f"{t.daily_atr:>6.2f}  {t.stop:>8.2f}  {t.target:>8.2f}  "
                  f"{t.bars_open:>4}  {r:>6.2f}  {pnl:>8.0f}  "
                  f"{t.exit_reason:<12} {t.signal.entry_date}  "
                  f"(stp={stp_pct:.1f}% tgt={tgt_pct:.1f}%)")

    print(f"\n{'='*65}")
    print(f"  {label} — TRADE SAMPLES")
    print(f"{'='*65}")
    _print_trade_table(trades[-n_best:][::-1], f"TOP {n_best} BEST TRADES")
    _print_trade_table(trades[:n_worst],       f"TOP {n_worst} WORST TRADES")


# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------

def run_backtest(
    start: date,
    end:   date,
    capital: float,
    symbol_map: dict,
    label: str = "BACKTEST",
    longs_only: bool = False,
    early_exit_enabled: bool = True,
) -> Portfolio:
    logger.info(f"[{label}] {start} to {end}")

    # ── Step 1: Compression scan ──────────────────────────────────────────
    comp_cfg = CompressionConfig(
        atr_universe_pct_threshold = 30.0,
        range_compression_ratio    = 0.50,
        structure_proximity_long   = 0.85,   # within 15% of 60d high (spec)
        structure_proximity_short  = 1.15,   # within 15% of 60d low
        min_liquidity_cr           = 10.0,
    )
    scanner = DailyCompressionScanner(
        data_root  = ROOT / "data",
        config     = comp_cfg,
        symbol_map = symbol_map,
    )
    candidates_by_date = scanner.scan_range(start, end)
    total_cands = sum(len(v) for v in candidates_by_date.values())
    logger.info(f"Compression scan: {total_cands} candidates over {len(candidates_by_date)} days")

    # ── Step 2: Build full daily OHLCV dict for candidate symbols ────────
    # We already have scanner._daily_cache built. Reuse it.
    df_daily_by_symbol: Dict[str, pd.DataFrame] = {}
    candidate_symbols = {
        c.symbol for clist in candidates_by_date.values() for c in clist
    }
    for sym in candidate_symbols:
        if sym in scanner._daily_cache:
            df = scanner._daily_cache[sym]
            df_daily_by_symbol[sym] = df

    logger.info(f"Daily OHLCV available for {len(df_daily_by_symbol)} candidate symbols")

    # ── Step 3: Nifty daily for soft bias ────────────────────────────────
    nifty_df = scanner._nifty_cache   # already loaded

    # ── Step 4: Trading days list ─────────────────────────────────────────
    trading_days = scanner._get_trading_days(start, end)

    # ── Step 5: Daily breakout trigger ───────────────────────────────────
    trig_cfg = DailyTriggerConfig(
        breakout_lookback     = 20,
        candidate_expiry_days = 7,
    )
    trigger  = DailyBreakoutTrigger(trig_cfg)
    signals  = trigger.scan_range(candidates_by_date, df_daily_by_symbol, trading_days)
    if longs_only:
        signals = [s for s in signals if s.direction == "LONG"]
        logger.info(f"Daily trigger: {len(signals)} signals (LONG only)")
    else:
        logger.info(f"Daily trigger: {len(signals)} signals")

    # Index signals by entry_date and symbol for O(1) lookup
    signals_by_entry: Dict[Tuple[date, str], DailySignal] = {}
    for sig in signals:
        key = (sig.entry_date, sig.symbol)
        if key not in signals_by_entry:
            signals_by_entry[key] = sig

    # ── Step 6: Portfolio simulation ──────────────────────────────────────
    portfolio = Portfolio(capital=capital, early_exit_enabled=early_exit_enabled)
    if not early_exit_enabled:
        logger.info("Early exit rule: DISABLED (trades run to stop/target/time-stop only)")

    for td in trading_days:
        # Build today's bar lookup for all symbols
        bar_today: Dict[str, pd.Series] = {}
        for sym, df in df_daily_by_symbol.items():
            row = df[df["date"] == td]
            if not row.empty:
                bar_today[sym] = row.iloc[0]

        # Also need Nifty for sizing
        nifty_slope_today = nifty_slope(nifty_df, td) if nifty_df is not None else 0.0

        # ── Entries: signals whose entry_date == today ──
        for sym in list(candidate_symbols):
            key = (td, sym)
            if key not in signals_by_entry:
                continue
            sig = signals_by_entry[key]
            bar = bar_today.get(sym)
            if bar is None:
                continue

            # Entry price = today's open (T+1 open)
            entry_price = float(bar["open"])
            if entry_price <= 0:
                continue

            trade = portfolio.enter(sig, entry_price, nifty_slope_today)
            if trade:
                logger.debug(
                    f"  ENTRY: {sig.trading_symbol} {sig.direction} "
                    f"@ {entry_price:.2f}  stop={trade.stop:.2f}  "
                    f"tgt={trade.target:.2f}  [{td}]"
                )

        # ── Update open positions with today's bar ──
        portfolio.process_day(bar_today, td)

    # Force-close anything remaining
    last_bars: Dict[str, pd.Series] = {}
    for sym, df in df_daily_by_symbol.items():
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
    parser = argparse.ArgumentParser(description="PixityAI v4 Daily Backtest")
    parser.add_argument("--train-start", default="2025-01-02")
    parser.add_argument("--train-end",   default="2025-07-31")
    parser.add_argument("--test-start",  default="2025-08-01")
    parser.add_argument("--test-end",    default="2026-02-13")
    parser.add_argument("--capital",     default=500_000, type=int)
    parser.add_argument("--debug-trades", action="store_true",
                        help="Print best/worst trade tables")
    parser.add_argument("--longs-only",  action="store_true",
                        help="Only take LONG signals (drop shorts entirely)")
    parser.add_argument("--no-early-exit", action="store_true",
                        help="Disable 3-day early exit rule (let trades run to stop/target/time-stop)")
    parser.add_argument("--single",      action="store_true")
    parser.add_argument("--period-start", default=None)
    parser.add_argument("--period-end",   default=None)
    args = parser.parse_args()

    db = DatabaseManager(ROOT / "data")
    with db.config_reader() as conn:
        rows = conn.execute(
            "SELECT instrument_key, trading_symbol FROM fo_stocks WHERE is_active = 1"
        ).fetchall()
    symbol_map = {r[0]: r[1] for r in rows}
    logger.info(f"Universe: {len(symbol_map)} symbols")

    lo  = args.longs_only
    nee = not args.no_early_exit   # early_exit_enabled = True unless --no-early-exit
    if lo:
        logger.info("Mode: LONG signals only")
    if args.no_early_exit:
        logger.info("Early exit rule: DISABLED")

    if args.single and args.period_start:
        p_start = date.fromisoformat(args.period_start)
        p_end   = date.fromisoformat(args.period_end or args.test_end)
        port    = run_backtest(p_start, p_end, args.capital, symbol_map, "FULL",
                               longs_only=lo, early_exit_enabled=nee)
        print_report(port, "FULL PERIOD", p_start, p_end)
        if args.debug_trades:
            dump_trades(port, "FULL PERIOD")
    else:
        tr_s = date.fromisoformat(args.train_start)
        tr_e = date.fromisoformat(args.train_end)
        te_s = date.fromisoformat(args.test_start)
        te_e = date.fromisoformat(args.test_end)

        logger.info("=== TRAIN ===")
        train_port = run_backtest(tr_s, tr_e, args.capital, symbol_map, "TRAIN",
                                  longs_only=lo, early_exit_enabled=nee)
        train_stats = print_report(train_port, "TRAIN", tr_s, tr_e)
        if args.debug_trades:
            dump_trades(train_port, "TRAIN")

        logger.info("=== TEST ===")
        test_port  = run_backtest(te_s, te_e, args.capital, symbol_map, "TEST",
                                  longs_only=lo, early_exit_enabled=nee)
        test_stats  = print_report(test_port,  "TEST",  te_s, te_e)
        if args.debug_trades:
            dump_trades(test_port, "TEST")

        print("\nWALK-FORWARD SUMMARY")
        print(f"  {'Metric':<20}  {'TRAIN':>12}  {'TEST':>12}")
        print(f"  {'-'*46}")
        for k in ["trades", "win_rate", "profit_factor", "avg_r", "median_r",
                  "targets", "time_stops", "max_dd", "total_pnl"]:
            tv = train_stats.get(k, 0)
            sv = test_stats.get(k, 0)
            if isinstance(tv, float):
                print(f"  {k:<20}  {tv:>12.3f}  {sv:>12.3f}")
            else:
                print(f"  {k:<20}  {tv:>12}  {sv:>12}")


if __name__ == "__main__":
    main()
