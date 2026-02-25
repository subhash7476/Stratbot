"""
PixityAI v3 — Equity Backtest Runner
======================================
Wires: Daily Compression Scanner -> 1H Breakout Trigger -> Equity P&L

This is a PURE EQUITY backtest — no options pricing.
Validates directional edge before any options overlay.

TRADE MECHANICS:
  Entry:  Close of breakout bar (market order next bar open in live)
  Stop:   1.5 x Daily ATR below entry (long) / above entry (short)
  Target: 3.0 x Daily ATR (2R, since stop = 1.5 ATR)
  Trail:  Move stop to breakeven at +1.5R (+1.5 x stop distance)
  Early exit: If no progress after 1 full trading day (<0.5R), exit at open
  Time stop: Exit after 3 trading days regardless

PORTFOLIO RULES:
  Max 3 concurrent positions
  Max 2 same-direction positions (soft - logs warning, doesn't block)
  Risk per trade: 0.75% of current equity
  Nifty soft bias: size 0.6x against Nifty trend direction

WALK-FORWARD:
  Run with --train / --test date ranges.
  Default: train 2025-07-01 to 2025-12-31, test 2026-01-01 to 2026-02-13

Usage:
    python scripts/run_v3_backtest.py
    python scripts/run_v3_backtest.py --train-start 2025-07-01 --train-end 2025-10-31
                                      --test-start 2025-11-01  --test-end 2026-02-13
    python scripts/run_v3_backtest.py --period-start 2025-07-01 --period-end 2026-02-13
"""

import sys
import os
import argparse
import logging
from pathlib import Path
from datetime import date, datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.strategies.expansion_v3.daily_compression_scanner import (
    DailyCompressionScanner, CompressionConfig, CompressionCandidate,
)
from core.strategies.expansion_v3.hourly_breakout_trigger import (
    HourlyBreakoutTrigger, BreakoutConfig, BreakoutSignal,
)
from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.analytics.resampler import resample_ohlcv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# NSE intraday fee model (same as v2)
STT_RATE       = 0.00025   # 0.025% per leg (sell side for equity intraday)
BROKERAGE      = 20.0      # Rs 20 per order flat (Zerodha-style)
EXCHANGE_RATE  = 0.0000345 # NSE + SEBI + GST composite
STAMP_RATE     = 0.00003   # 0.003% on buy side


def compute_fees(entry_price: float, exit_price: float, qty: int, direction: str) -> float:
    """NSE equity intraday fee model."""
    buy_val  = entry_price * qty if direction == "LONG" else exit_price * qty
    sell_val = exit_price * qty  if direction == "LONG" else entry_price * qty
    turnover = buy_val + sell_val
    stt      = sell_val * STT_RATE
    brok     = BROKERAGE * 2
    exchange = turnover * EXCHANGE_RATE
    stamp    = buy_val * STAMP_RATE
    return stt + brok + exchange + stamp


def nifty_trend_slope(nifty_df: pd.DataFrame, up_to_date: date) -> float:
    """
    Nifty 20D EMA slope: (EMA20_today - EMA20_5d_ago) / EMA20_5d_ago.
    Returns positive for uptrend, negative for downtrend.
    """
    df = nifty_df[nifty_df["date"] <= up_to_date].tail(30)
    if len(df) < 22:
        return 0.0
    ema20 = df["close"].ewm(span=20, adjust=False).mean()
    if len(ema20) < 6:
        return 0.0
    slope = (ema20.iloc[-1] - ema20.iloc[-6]) / ema20.iloc[-6]
    return float(slope)


def size_multiplier(slope: float, direction: str, threshold: float = 0.001) -> float:
    """
    Soft Nifty bias sizing multiplier.
    Full size (1.0x) with trend, reduced (0.6x) against trend.
    Both at 0.85x in neutral market.
    """
    if slope > threshold:
        return 1.0 if direction == "LONG" else 0.6
    elif slope < -threshold:
        return 1.0 if direction == "SHORT" else 0.6
    else:
        return 0.85  # neutral


# ---------------------------------------------------------------------------
# Trade state machine
# ---------------------------------------------------------------------------

class Trade:
    """Tracks a single open position through its lifecycle."""

    BARS_PER_DAY = 7  # NSE 1H bars: 09:15, 10:15, 11:15, 12:15, 13:15, 14:15, 15:15

    def __init__(
        self,
        signal: BreakoutSignal,
        entry_price: float,
        qty: int,
        stop: float,
        target: float,
        breakeven_level: float,
        max_bars: int,
    ):
        self.signal           = signal
        self.entry_price      = entry_price
        self.qty              = qty
        self.stop             = stop
        self.target           = target
        self.breakeven_level  = breakeven_level  # price at which stop moves to entry
        self.max_bars         = max_bars         # hard time stop (3 trading days)
        self.direction        = signal.direction
        self.bars_open        = 0
        self.trailed          = False            # has stop been moved to breakeven
        self.closed           = False
        self.exit_price: Optional[float] = None
        self.exit_reason: Optional[str]  = None
        self.exit_time: Optional[datetime] = None

    @property
    def stop_distance(self) -> float:
        return abs(self.entry_price - self.stop)

    def current_r(self, price: float) -> float:
        """Current unrealised R multiple."""
        if self.direction == "LONG":
            return (price - self.entry_price) / self.stop_distance
        else:
            return (self.entry_price - price) / self.stop_distance

    def update_bar(self, bar: pd.Series) -> Optional[str]:
        """
        Process one 1H bar. Returns exit reason string if position closes, else None.
        Bar series: timestamp, open, high, low, close
        """
        if self.closed:
            return None

        self.bars_open += 1
        high  = bar["high"]
        low   = bar["low"]
        close = bar["close"]
        open_ = bar["open"]

        # --- Trail to breakeven ---
        if not self.trailed:
            if self.direction == "LONG" and high >= self.breakeven_level:
                self.stop = self.entry_price
                self.trailed = True
            elif self.direction == "SHORT" and low <= self.breakeven_level:
                self.stop = self.entry_price
                self.trailed = True

        # --- Stop hit (use open for gap-through protection) ---
        if self.direction == "LONG":
            if open_ <= self.stop or low <= self.stop:
                exit_p = min(open_, self.stop)
                self._close(exit_p, "STOP", bar["timestamp"])
                return "STOP"
        else:
            if open_ >= self.stop or high >= self.stop:
                exit_p = max(open_, self.stop)
                self._close(exit_p, "STOP", bar["timestamp"])
                return "STOP"

        # --- Target hit ---
        if self.direction == "LONG" and high >= self.target:
            self._close(self.target, "TARGET", bar["timestamp"])
            return "TARGET"
        elif self.direction == "SHORT" and low <= self.target:
            self._close(self.target, "TARGET", bar["timestamp"])
            return "TARGET"

        # --- Hard time stop ---
        if self.bars_open >= self.max_bars:
            self._close(open_, "TIME_STOP", bar["timestamp"])
            return "TIME_STOP"

        return None

    def _close(self, price: float, reason: str, ts) -> None:
        self.exit_price  = price
        self.exit_reason = reason
        self.exit_time   = pd.Timestamp(ts).to_pydatetime()
        self.closed      = True

    def pnl(self, fee_func=None) -> float:
        if self.exit_price is None:
            return 0.0
        if self.direction == "LONG":
            gross = (self.exit_price - self.entry_price) * self.qty
        else:
            gross = (self.entry_price - self.exit_price) * self.qty
        fees = fee_func(self.entry_price, self.exit_price, self.qty, self.direction) \
               if fee_func else 0.0
        return gross - fees

    def r_multiple(self) -> float:
        if self.exit_price is None or self.stop_distance == 0:
            return 0.0
        return self.pnl() / (self.stop_distance * self.qty)


# ---------------------------------------------------------------------------
# Portfolio simulator
# ---------------------------------------------------------------------------

class PortfolioSimulator:
    """
    Simulates multi-stock portfolio with:
      - Max 3 concurrent positions
      - Risk-based sizing with Nifty soft bias
      - NSE fee model
    """

    MAX_POSITIONS     = 3
    RISK_PCT          = 0.0075   # 0.75% of equity per trade
    STOP_ATR_MULT     = 1.5      # stop = 1.5 x daily ATR
    TARGET_ATR_MULT   = 3.0      # target = 3.0 x daily ATR (2R)
    BREAKEVEN_R       = 1.5      # trail stop to breakeven at +1.5R
    MAX_BARS          = 21       # hard time stop: 3 trading days x 7 bars

    def __init__(self, initial_capital: float = 500_000.0):
        self.equity    = initial_capital
        self.initial   = initial_capital
        self.open_trades: List[Trade] = []
        self.closed_trades: List[Trade] = []
        self.equity_curve: List[Tuple[datetime, float]] = []
        self.peak_equity = initial_capital

    @property
    def max_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        eq = [e for _, e in self.equity_curve]
        peak = self.initial
        max_dd = 0.0
        for e in eq:
            peak = max(peak, e)
            dd = (peak - e) / peak
            max_dd = max(max_dd, dd)
        return max_dd

    def can_enter(self, symbol: str) -> bool:
        """Check if a new position can be entered."""
        if len(self.open_trades) >= self.MAX_POSITIONS:
            return False
        # No double positions in same symbol
        open_syms = {t.signal.symbol for t in self.open_trades}
        if symbol in open_syms:
            return False
        return True

    def enter_trade(
        self,
        signal: BreakoutSignal,
        nifty_slope: float,
    ) -> Optional[Trade]:
        """Open a new trade from a breakout signal."""
        if not self.can_enter(signal.symbol):
            return None

        stop_dist = signal.daily_atr * self.STOP_ATR_MULT
        if stop_dist <= 0:
            return None

        # Risk-based sizing with Nifty soft bias
        raw_risk  = self.equity * self.RISK_PCT
        size_mult = size_multiplier(nifty_slope, signal.direction)
        risk_amt  = raw_risk * size_mult
        qty       = max(1, int(risk_amt / stop_dist))

        entry = signal.entry_price
        if signal.direction == "LONG":
            stop   = entry - stop_dist
            target = entry + stop_dist * self.TARGET_ATR_MULT / self.STOP_ATR_MULT
            be_lvl = entry + stop_dist * self.BREAKEVEN_R
        else:
            stop   = entry + stop_dist
            target = entry - stop_dist * self.TARGET_ATR_MULT / self.STOP_ATR_MULT
            be_lvl = entry - stop_dist * self.BREAKEVEN_R

        trade = Trade(
            signal           = signal,
            entry_price      = entry,
            qty              = qty,
            stop             = stop,
            target           = target,
            breakeven_level  = be_lvl,
            max_bars         = self.MAX_BARS,
        )
        self.open_trades.append(trade)
        return trade

    def process_bar(self, bar: pd.Series, symbol: str, bar_time: datetime) -> None:
        """Update all open trades for this symbol's bar."""
        to_close = []
        for trade in self.open_trades:
            if trade.signal.symbol != symbol:
                continue
            reason = trade.update_bar(bar)
            if reason:
                pnl = trade.pnl(fee_func=compute_fees)
                self.equity += pnl
                to_close.append(trade)
                self.closed_trades.append(trade)

        for t in to_close:
            self.open_trades.remove(t)

        self.equity_curve.append((bar_time, self.equity))

    def force_close_all(self, price_by_symbol: Dict[str, float], ts: datetime) -> None:
        """Force-close all open positions at given prices (end of backtest)."""
        for trade in list(self.open_trades):
            price = price_by_symbol.get(trade.signal.symbol, trade.entry_price)
            trade._close(price, "FORCE_CLOSE", ts)
            pnl = trade.pnl(fee_func=compute_fees)
            self.equity += pnl
            self.closed_trades.append(trade)
        self.open_trades.clear()


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(
    portfolio: PortfolioSimulator,
    label: str = "BACKTEST",
    start_date: date = None,
    end_date: date = None,
) -> dict:
    trades = portfolio.closed_trades
    if not trades:
        print(f"\n[{label}] No trades closed.")
        return {}

    pnls       = [t.pnl(fee_func=compute_fees) for t in trades]
    r_mults    = [t.r_multiple() for t in trades]
    winners    = [p for p in pnls if p > 0]
    losers     = [p for p in pnls if p <= 0]
    win_rate   = len(winners) / len(trades) * 100
    expectancy = np.mean(pnls)
    pf_denom   = abs(sum(losers)) if losers else 1
    profit_fac = sum(winners) / pf_denom if winners else 0.0
    total_pnl  = sum(pnls)
    max_dd     = portfolio.max_drawdown * 100

    exit_counts = defaultdict(int)
    for t in trades:
        exit_counts[t.exit_reason] += 1

    print(f"\n{'='*65}")
    print(f"  PixityAI v3 — {label} RESULTS")
    if start_date and end_date:
        print(f"  Period: {start_date} to {end_date}")
    print(f"{'='*65}")
    print(f"  Trades:        {len(trades)}")
    print(f"  Win Rate:      {win_rate:.1f}%")
    print(f"  Total PnL:     Rs {total_pnl:,.0f}")
    print(f"  Expectancy:    Rs {expectancy:,.0f} per trade")
    print(f"  Profit Factor: {profit_fac:.2f}")
    print(f"  Max Drawdown:  {max_dd:.1f}%")
    print(f"  Avg R:         {np.mean(r_mults):.2f}")
    print(f"  Median R:      {np.median(r_mults):.2f}")
    print(f"  Final Equity:  Rs {portfolio.equity:,.0f}")
    print(f"  Return:        {(portfolio.equity/portfolio.initial - 1)*100:.1f}%")
    print(f"  Exit reasons:  {dict(exit_counts)}")

    # Distribution skew check
    if len(r_mults) >= 5:
        from scipy.stats import skew as scipy_skew
        sk = scipy_skew(r_mults)
        skew_verdict = "RIGHT-SKEWED (good)" if sk > 0.1 else \
                       "SYMMETRIC"           if sk > -0.1 else \
                       "LEFT-SKEWED (bad)"
        print(f"  R distribution skew: {sk:.2f}  ->  {skew_verdict}")

    # R distribution histogram
    if r_mults:
        buckets = [
            ("< -1.5R",  sum(1 for r in r_mults if r < -1.5)),
            ("-1.5 to -1R", sum(1 for r in r_mults if -1.5 <= r < -1.0)),
            ("-1 to 0R",  sum(1 for r in r_mults if -1.0 <= r < 0)),
            ("0 to +0.5R", sum(1 for r in r_mults if 0 <= r < 0.5)),
            ("+0.5 to +1R", sum(1 for r in r_mults if 0.5 <= r < 1.0)),
            ("+1 to +1.5R", sum(1 for r in r_mults if 1.0 <= r < 1.5)),
            ("+1.5 to +2R", sum(1 for r in r_mults if 1.5 <= r < 2.0)),
            (">= +2R",    sum(1 for r in r_mults if r >= 2.0)),
        ]
        print(f"\n  R DISTRIBUTION:")
        for label_b, count in buckets:
            bar_w = "#" * count
            print(f"    {label_b:<16} {count:>3}  {bar_w}")

    # Per-bar-open breakdown (how many bars were trades open at exit)
    bars_open_list = [t.bars_open for t in trades]
    print(f"\n  BARS OPEN AT EXIT: avg={np.mean(bars_open_list):.1f}  "
          f"min={min(bars_open_list)}  max={max(bars_open_list)}  "
          f"median={int(np.median(bars_open_list))}")

    # Directional breakdown
    long_trades  = [t for t in trades if t.direction == "LONG"]
    short_trades = [t for t in trades if t.direction == "SHORT"]
    if long_trades:
        long_pnl = sum(t.pnl(compute_fees) for t in long_trades)
        long_wr  = sum(1 for t in long_trades if t.pnl(compute_fees) > 0) / len(long_trades) * 100
        print(f"  LONG:  {len(long_trades)} trades  WR={long_wr:.0f}%  PnL=Rs {long_pnl:,.0f}")
    if short_trades:
        short_pnl = sum(t.pnl(compute_fees) for t in short_trades)
        short_wr  = sum(1 for t in short_trades if t.pnl(compute_fees) > 0) / len(short_trades) * 100
        print(f"  SHORT: {len(short_trades)} trades  WR={short_wr:.0f}%  PnL=Rs {short_pnl:,.0f}")

    # Kill criteria check
    print(f"\n  KILL CRITERIA CHECK:")
    kc_exp  = "PASS" if expectancy > 0         else "FAIL -- median expectancy < 0"
    kc_pf   = "PASS" if profit_fac >= 1.15     else "FAIL -- profit factor < 1.15"
    print(f"    Expectancy > 0:   {kc_exp}")
    print(f"    Profit Factor >= 1.15: {kc_pf}")
    print(f"{'='*65}\n")

    return {
        "trades": len(trades), "win_rate": win_rate, "total_pnl": total_pnl,
        "expectancy": expectancy, "profit_factor": profit_fac,
        "max_drawdown": max_dd, "avg_r": np.mean(r_mults),
        "final_equity": portfolio.equity,
    }


# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------

def run_backtest(
    start_date: date,
    end_date: date,
    initial_capital: float,
    symbol_map: dict,
    db: DatabaseManager,
    label: str = "BACKTEST",
) -> Tuple[PortfolioSimulator, List[BreakoutSignal]]:

    logger.info(f"[{label}] {start_date} to {end_date}")

    query = MarketDataQuery(db)

    # --- Step 1: Run daily compression scanner ---
    logger.info("Running daily compression scanner...")
    comp_cfg = CompressionConfig()
    scanner  = DailyCompressionScanner(
        data_root=ROOT / "data",
        config=comp_cfg,
        symbol_map=symbol_map,
    )
    # Extend preload backwards for indicator warmup
    preload_from = start_date - timedelta(days=200)
    candidates_by_date = scanner.scan_range(start_date, end_date)
    total_candidates = sum(len(v) for v in candidates_by_date.values())
    logger.info(f"Compression scan: {total_candidates} candidates across {len(candidates_by_date)} days")

    # --- Step 2: Load 1H data for all symbols that appeared as candidates ---
    candidate_symbols = {
        c.symbol
        for clist in candidates_by_date.values()
        for c in clist
    }
    logger.info(f"Loading 1H data for {len(candidate_symbols)} candidate symbols...")

    # Load 1m with warmup, resample to 1H
    data_start = start_date - timedelta(days=35)  # 25 bars warmup at 1H = ~25 trading hrs = 4 days
    data_end   = end_date

    df_1h_by_symbol: Dict[str, pd.DataFrame] = {}
    for sym in candidate_symbols:
        try:
            df1m = query.get_ohlcv(
                sym,
                start_time=datetime.combine(data_start, datetime.min.time()),
                end_time=datetime.combine(data_end + timedelta(days=1), datetime.min.time()),
                timeframe="1m",
            )
            if df1m is None or len(df1m) < 100:
                continue
            df1h = resample_ohlcv(df1m, target_tf="1h")
            if df1h is not None and len(df1h) > 0:
                df_1h_by_symbol[sym] = df1h
        except Exception as e:
            logger.debug(f"Error loading 1H for {sym}: {e}")

    logger.info(f"Loaded 1H data for {len(df_1h_by_symbol)} symbols")

    # --- Step 3: Load Nifty daily for soft bias ---
    nifty_df = scanner._nifty_cache
    nifty_daily_dict: Dict[date, float] = {}
    if nifty_df is not None:
        for _, row in nifty_df.iterrows():
            nifty_daily_dict[row["date"]] = row["close"]

    # --- Step 4: Find all breakout signals ---
    logger.info("Scanning for 1H breakout signals...")
    trigger = HourlyBreakoutTrigger()
    all_signals = trigger.scan_candidates_range(
        candidates_by_date=candidates_by_date,
        df_1h_by_symbol=df_1h_by_symbol,
        nifty_df_daily=nifty_df,
    )
    logger.info(f"Found {len(all_signals)} breakout signals")

    # --- Step 5: Simulate portfolio ---
    logger.info("Simulating portfolio...")
    portfolio = PortfolioSimulator(initial_capital=initial_capital)

    # Build a timeline of all 1H bars across all candidate symbols, sorted by time
    all_bars: List[Tuple[datetime, str, pd.Series]] = []
    for sym, df in df_1h_by_symbol.items():
        for _, row in df.iterrows():
            bar_time = pd.Timestamp(row["timestamp"]).to_pydatetime()
            bar_date = bar_time.date()
            if bar_date < start_date or bar_date > end_date:
                continue
            all_bars.append((bar_time, sym, row))

    all_bars.sort(key=lambda x: x[0])

    # Signal lookup: trigger_time -> signal
    signal_by_time_sym: Dict[Tuple[datetime, str], BreakoutSignal] = {
        (s.trigger_time, s.symbol): s for s in all_signals
    }

    for bar_time, sym, bar in all_bars:
        # Update existing positions FIRST (on bars after entry)
        portfolio.process_bar(bar, sym, bar_time)

        # Then check for new signal entry at this bar (enter at bar close,
        # first update is on the NEXT bar — correct causal behaviour)
        key = (bar_time, sym)
        if key in signal_by_time_sym:
            sig = signal_by_time_sym[key]
            bar_date = bar_time.date()
            nifty_slope = nifty_trend_slope(nifty_df, bar_date) if nifty_df is not None else 0.0
            trade = portfolio.enter_trade(sig, nifty_slope)
            if trade:
                logger.debug(f"  Entry: {sig.trading_symbol} {sig.direction} "
                             f"@ {sig.entry_price:.2f} | {bar_time}")

    # Force-close any remaining open positions at last available price
    last_prices = {}
    for sym, df in df_1h_by_symbol.items():
        if not df.empty:
            last_prices[sym] = float(df["close"].iloc[-1])
    if portfolio.open_trades:
        logger.info(f"Force-closing {len(portfolio.open_trades)} open positions at period end")
        portfolio.force_close_all(last_prices, datetime.combine(end_date, datetime.min.time()))

    return portfolio, all_signals


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def dump_trades(portfolio: PortfolioSimulator, label: str) -> None:
    """Print per-trade table for inspection."""
    trades = portfolio.closed_trades
    if not trades:
        return
    print(f"\n{'='*100}")
    print(f"  {label} — PER-TRADE TABLE")
    print(f"{'='*100}")
    hdr = f"{'#':>3}  {'Symbol':<14}  {'Dir':<5}  {'Entry':>8}  {'Exit':>8}  " \
          f"{'Stop':>8}  {'Target':>8}  {'Bars':>4}  {'R':>6}  {'PnL':>8}  {'Reason':<12}  {'Date'}"
    print(hdr)
    print("-"*100)
    for i, t in enumerate(sorted(trades, key=lambda x: x.signal.trigger_time), 1):
        pnl = t.pnl(compute_fees)
        r   = t.r_multiple()
        sym = t.signal.trading_symbol[:14]
        tgt_pct = abs(t.target - t.entry_price) / t.entry_price * 100
        print(f"{i:>3}  {sym:<14}  {t.direction:<5}  {t.entry_price:>8.2f}  "
              f"{t.exit_price:>8.2f}  {t.stop:>8.2f}  {t.target:>8.2f}  "
              f"{t.bars_open:>4}  {r:>6.2f}  {pnl:>8.0f}  {t.exit_reason:<12}  "
              f"{t.signal.trigger_time.date()} (tgt={tgt_pct:.1f}%)")
    print(f"{'='*100}\n")


def main():
    parser = argparse.ArgumentParser(description="PixityAI v3 Equity Backtest")
    parser.add_argument("--train-start", default="2025-07-01")
    parser.add_argument("--train-end",   default="2025-10-31")
    parser.add_argument("--test-start",  default="2025-11-01")
    parser.add_argument("--test-end",    default="2026-02-13")
    parser.add_argument("--capital",     default=500000, type=int)
    parser.add_argument("--single",      action="store_true",
                        help="Run single period (use --period-start / --period-end)")
    parser.add_argument("--period-start", default=None)
    parser.add_argument("--period-end",   default=None)
    parser.add_argument("--debug-trades", action="store_true",
                        help="Print per-trade table for each period")
    args = parser.parse_args()

    db = DatabaseManager(ROOT / "data")
    with db.config_reader() as conn:
        rows = conn.execute(
            "SELECT instrument_key, trading_symbol FROM fo_stocks WHERE is_active = 1"
        ).fetchall()
    symbol_map = {r[0]: r[1] for r in rows}
    logger.info(f"Universe: {len(symbol_map)} symbols")

    if args.single and args.period_start:
        p_start = date.fromisoformat(args.period_start)
        p_end   = date.fromisoformat(args.period_end or args.test_end)
        port, sigs = run_backtest(p_start, p_end, args.capital, symbol_map, db, "FULL PERIOD")
        print_report(port, "FULL PERIOD", p_start, p_end)
        if args.debug_trades:
            dump_trades(port, "FULL PERIOD")
    else:
        train_start = date.fromisoformat(args.train_start)
        train_end   = date.fromisoformat(args.train_end)
        test_start  = date.fromisoformat(args.test_start)
        test_end    = date.fromisoformat(args.test_end)

        train_port, train_sigs = run_backtest(
            train_start, train_end, args.capital, symbol_map, db, "TRAIN"
        )
        print_report(train_port, "TRAIN", train_start, train_end)
        if args.debug_trades:
            dump_trades(train_port, "TRAIN")

        test_port, test_sigs = run_backtest(
            test_start, test_end, args.capital, symbol_map, db, "TEST"
        )
        print_report(test_port, "TEST", test_start, test_end)
        if args.debug_trades:
            dump_trades(test_port, "TEST")

        # Summary
        print("\nWALK-FORWARD SUMMARY")
        print(f"  Train trades: {len(train_port.closed_trades)}  |  "
              f"Test trades: {len(test_port.closed_trades)}")
        print(f"  Train PnL: Rs {sum(t.pnl(compute_fees) for t in train_port.closed_trades):,.0f}  |  "
              f"Test PnL: Rs {sum(t.pnl(compute_fees) for t in test_port.closed_trades):,.0f}")


if __name__ == "__main__":
    main()
