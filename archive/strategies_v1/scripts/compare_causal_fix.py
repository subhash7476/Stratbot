"""
A/B Comparison: Old (biased) vs New (causal) swing detection.
Both runs use the SAME handler.py (with position tracker fix + fees).
The ONLY variable is the swing detection function.

Runs 8 backtests: 2 swing modes x 2 symbols x 2 periods.
"""
import sys
import os
import logging
from datetime import datetime
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import core.strategies.pixityAI_batch_events as batch_mod
import pandas as pd
import numpy as np

from core.database.manager import DatabaseManager
from core.backtest.runner import BacktestRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("core.database").setLevel(logging.WARNING)
logging.getLogger("core.runner").setLevel(logging.WARNING)
logging.getLogger("core.execution").setLevel(logging.WARNING)
logging.getLogger("core.brokers").setLevel(logging.WARNING)

db = DatabaseManager(Path("data"))
runner = BacktestRunner(db)

SYMBOLS = [
    ("NSE_EQ|INE155A01022", "Tata Power"),
    ("NSE_EQ|INE118H01025", "Bajaj Finance"),
]

PERIODS = [
    ("train", datetime(2024, 10, 17), datetime(2025, 5, 31)),
    ("test",  datetime(2025, 6, 1),   datetime(2025, 12, 31)),
]

PARAMS = {"skip_meta_model": True, "use_signal_quality_filter": False}


# ── Swing detection variants ──────────────────────────────────────────

def find_swing_highs_BIASED(highs: pd.Series, period: int = 5) -> pd.Series:
    """OLD: assigns swing at bar i (uses future bars for confirmation)."""
    result = pd.Series(np.nan, index=highs.index)
    for i in range(period, len(highs) - period):
        window = highs.iloc[i - period: i + period + 1]
        if highs.iloc[i] == window.max():
            result.iloc[i] = highs.iloc[i]
    return result.ffill()

def find_swing_lows_BIASED(lows: pd.Series, period: int = 5) -> pd.Series:
    """OLD: assigns swing at bar i (uses future bars for confirmation)."""
    result = pd.Series(np.nan, index=lows.index)
    for i in range(period, len(lows) - period):
        window = lows.iloc[i - period: i + period + 1]
        if lows.iloc[i] == window.min():
            result.iloc[i] = lows.iloc[i]
    return result.ffill()

def find_swing_highs_CAUSAL(highs: pd.Series, period: int = 5) -> pd.Series:
    """NEW: assigns swing at bar i+period (causal — no look-ahead)."""
    result = pd.Series(np.nan, index=highs.index)
    for i in range(period, len(highs) - period):
        window = highs.iloc[i - period: i + period + 1]
        if highs.iloc[i] == window.max():
            result.iloc[i + period] = highs.iloc[i]
    return result.ffill()

def find_swing_lows_CAUSAL(lows: pd.Series, period: int = 5) -> pd.Series:
    """NEW: assigns swing at bar i+period (causal — no look-ahead)."""
    result = pd.Series(np.nan, index=lows.index)
    for i in range(period, len(lows) - period):
        window = lows.iloc[i - period: i + period + 1]
        if lows.iloc[i] == window.min():
            result.iloc[i + period] = lows.iloc[i]
    return result.ffill()


def run_backtests(label: str, swing_high_fn, swing_low_fn):
    """Run 4 backtests with the given swing detection functions."""
    # Monkey-patch the batch module
    batch_mod.find_swing_highs = swing_high_fn
    batch_mod.find_swing_lows = swing_low_fn

    results = []
    for sym_key, sym_name in SYMBOLS:
        for period_name, start, end in PERIODS:
            run_id = f"{label}_{sym_name.lower().replace(' ', '_')}_{period_name}"
            try:
                runner.run(
                    strategy_id="pixityAI_meta",
                    symbol=sym_key,
                    start_time=start,
                    end_time=end,
                    initial_capital=100000.0,
                    strategy_params=dict(PARAMS),
                    timeframe="15m",
                    run_id=run_id,
                )
                with db.backtest_index_reader() as conn:
                    row = conn.execute(
                        "SELECT total_trades, win_rate, total_pnl, max_drawdown "
                        "FROM backtest_runs WHERE run_id = ?",
                        [run_id],
                    ).fetchone()
                    if row:
                        results.append({
                            "symbol": sym_name,
                            "period": period_name,
                            "trades": row[0],
                            "wr": row[1],
                            "pnl": row[2],
                            "dd": row[3],
                        })
                        print(f"    [{label}] {sym_name} {period_name}: "
                              f"Trades={row[0]}, WR={row[1]:.1f}%, PnL=Rs {row[2]:,.0f}, DD={row[3]:.1f}%")
            except Exception as e:
                print(f"    [{label}] {sym_name} {period_name}: FAILED — {e}")
                results.append({
                    "symbol": sym_name, "period": period_name,
                    "trades": 0, "wr": 0, "pnl": 0, "dd": 0,
                })
    return results


def main():
    print(f"\n{'='*80}")
    print(f"  A/B COMPARISON: Biased vs Causal Swing Detection")
    print(f"  Both use identical handler (fees + position tracker fix)")
    print(f"  Only variable: swing detection timing")
    print(f"{'='*80}\n")

    print("  Phase A: Running with OLD (biased) swing detection...\n")
    old = run_backtests("biased", find_swing_highs_BIASED, find_swing_lows_BIASED)

    print(f"\n  Phase B: Running with NEW (causal) swing detection...\n")
    new = run_backtests("causal", find_swing_highs_CAUSAL, find_swing_lows_CAUSAL)

    # Restore causal version as the permanent code
    batch_mod.find_swing_highs = find_swing_highs_CAUSAL
    batch_mod.find_swing_lows = find_swing_lows_CAUSAL

    # Print comparison
    print(f"\n{'='*80}")
    print(f"  RESULTS: Old (biased) vs New (causal) — Same handler, same fees")
    print(f"{'='*80}")
    print(f"  {'Symbol':<16} {'Period':<7} | {'BIASED PnL':>11} {'Trades':>7} {'WR':>6} | {'CAUSAL PnL':>11} {'Trades':>7} {'WR':>6} | {'Delta':>8}")
    print(f"  {'-'*95}")

    for o, n in zip(old, new):
        delta = n["pnl"] - o["pnl"]
        sign = "+" if delta >= 0 else ""
        print(
            f"  {o['symbol']:<16} {o['period']:<7} | "
            f"Rs {o['pnl']:>8,.0f} {o['trades']:>7} {o['wr']:>5.1f}% | "
            f"Rs {n['pnl']:>8,.0f} {n['trades']:>7} {n['wr']:>5.1f}% | "
            f"{sign}Rs {delta:,.0f}"
        )

    print(f"\n{'='*80}\n")


if __name__ == "__main__":
    main()
