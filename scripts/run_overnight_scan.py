"""
Overnight Scan: TREND-only on high-vol symbols across 5m, 10m, 15m timeframes.
-------------------------------------------------------------------------------
Runs ATR% filter once, then scans the filtered symbols on each timeframe sequentially.
Results saved to scanner DB per timeframe. Prints comparison summary at the end.

Usage:
    python scripts/run_overnight_scan.py
    python scripts/run_overnight_scan.py --min-atr-pct 0.50   # stricter filter
"""
import sys
import os
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.analytics.resampler import resample_ohlcv
from core.analytics.indicators.atr import ATR
from core.backtest.symbol_scanner import SymbolScanner
from core.backtest.scan_persistence import ScanPersistence


def progress_printer(current, total, symbol, status):
    pct = (current / total * 100) if total > 0 else 0
    print(f"  [{current:>3}/{total}] {pct:5.1f}% | {symbol:<25} | {status}")


def filter_by_atr(db, symbols, min_atr_pct, test_end_str):
    """Filter symbols by mean ATR% >= threshold. Returns filtered list."""
    print(f"\n  Filtering {len(symbols)} symbols by ATR% >= {min_atr_pct}%...")
    query = MarketDataQuery(db)
    filter_end = datetime.strptime(test_end_str, "%Y-%m-%d")
    filter_start = filter_end - timedelta(days=120)

    filtered = []
    for i, sym_info in enumerate(symbols):
        if (i + 1) % 20 == 0 or i == 0:
            print(f"    [{i+1}/{len(symbols)}] scanning ATR%...", flush=True)
        try:
            df_1m = query.get_ohlcv(sym_info["instrument_key"],
                                     start_time=filter_start, end_time=filter_end, timeframe="1m")
            if df_1m.empty:
                continue
            df_1m["timestamp"] = pd.to_datetime(df_1m["timestamp"])
            df_1m.set_index("timestamp", inplace=True)
            df_15m = resample_ohlcv(df_1m, "15m")
            atr_vals = ATR(14).calculate(df_15m)
            atr_pct = (atr_vals / df_15m["close"]).dropna()
            mean_atr_pct = atr_pct.mean() * 100
            if mean_atr_pct >= min_atr_pct:
                filtered.append(sym_info)
        except Exception:
            pass

    print(f"  Filtered to {len(filtered)}/{len(symbols)} symbols (ATR% >= {min_atr_pct}%)\n")
    return filtered


def run_scan_for_timeframe(scanner, db, symbols, timeframe, strategy_params,
                           train_start, train_end, test_start, test_end, capital):
    """Run full walk-forward scan for one timeframe. Returns ScanResults."""
    print(f"\n{'='*70}")
    print(f"  SCAN: {timeframe} | {len(symbols)} symbols | TREND-only")
    print(f"  Train: {train_start.date()} -> {train_end.date()}")
    print(f"  Test:  {test_start.date()} -> {test_end.date()}")
    print(f"{'='*70}\n")

    t0 = time.time()
    scan = scanner.scan_all_symbols(
        symbols=symbols,
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
        initial_capital=capital,
        timeframe=timeframe,
        strategy_params=strategy_params,
        progress_callback=progress_printer,
    )

    elapsed = time.time() - t0
    profitable = [r for r in scan.symbol_results if r.is_profitable]
    failed = [r for r in scan.symbol_results if r.error]

    print(f"\n  {timeframe} DONE in {elapsed/60:.1f} min: "
          f"{scan.profitable_symbols}/{scan.total_symbols} profitable, "
          f"{len(failed)} errors")

    if profitable:
        print(f"\n  Rank | {'Symbol':<25} | {'Train PnL':>10} | {'Test PnL':>10} | "
              f"{'Test WR':>7} | {'Test DD':>7} | Trades")
        print(f"  {'-'*100}")
        for r in profitable:
            print(f"  {r.rank:>4} | {r.trading_symbol:<25} | "
                  f"Rs {r.train_pnl:>8,.0f} | Rs {r.test_pnl:>8,.0f} | "
                  f"{r.test_win_rate:>5.1f}% | {r.test_max_dd:>5.1f}% | "
                  f"{r.train_trades + r.test_trades}")

    # Save
    persistence = ScanPersistence(db)
    persistence.save_scan(scan)
    print(f"  Saved: scan_id={scan.scan_id}")

    return scan


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Overnight multi-timeframe TREND-only scan")
    parser.add_argument("--min-atr-pct", type=float, default=0.43, help="Min ATR%% (default: 0.43)")
    parser.add_argument("--capital", type=float, default=100000.0, help="Initial capital")
    parser.add_argument("--train-start", default="2024-10-17")
    parser.add_argument("--train-end", default="2025-05-31")
    parser.add_argument("--test-start", default="2025-06-01")
    parser.add_argument("--test-end", default="2025-12-31")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("core.database").setLevel(logging.WARNING)
    logging.getLogger("core.runner").setLevel(logging.WARNING)
    logging.getLogger("core.execution").setLevel(logging.WARNING)
    logging.getLogger("core.brokers").setLevel(logging.WARNING)

    db = DatabaseManager(Path("data"))
    scanner = SymbolScanner(db)

    # Load and filter symbols once
    all_symbols = scanner.get_all_equity_symbols()
    symbols = filter_by_atr(db, all_symbols, args.min_atr_pct, args.test_end)

    if not symbols:
        print("No symbols passed ATR% filter. Exiting.")
        return

    train_start = datetime.strptime(args.train_start, "%Y-%m-%d")
    train_end = datetime.strptime(args.train_end, "%Y-%m-%d")
    test_start = datetime.strptime(args.test_start, "%Y-%m-%d")
    test_end = datetime.strptime(args.test_end, "%Y-%m-%d")

    strategy_params = {
        "skip_meta_model": True,
        "use_signal_quality_filter": False,
        "skip_reversion": True,
    }

    timeframes = ["5m", "10m", "15m"]
    results = {}
    total_start = time.time()

    print(f"\n{'#'*70}")
    print(f"  OVERNIGHT SCAN: TREND-only | {len(symbols)} high-vol symbols")
    print(f"  Timeframes: {', '.join(timeframes)}")
    print(f"  ATR% >= {args.min_atr_pct}% | Capital: Rs {args.capital:,.0f}")
    print(f"{'#'*70}")

    for tf in timeframes:
        results[tf] = run_scan_for_timeframe(
            scanner, db, symbols, tf, strategy_params,
            train_start, train_end, test_start, test_end, args.capital
        )

    # Final comparison summary
    total_elapsed = time.time() - total_start
    print(f"\n\n{'#'*70}")
    print(f"  OVERNIGHT SCAN COMPLETE - {total_elapsed/3600:.1f} hours total")
    print(f"{'#'*70}")

    print(f"\n  {'Timeframe':<10} | {'Profitable':>10} | {'Total':>6} | "
          f"{'Agg Test PnL':>14} | {'Avg Test PnL':>14} | {'Avg WR':>7}")
    print(f"  {'-'*75}")

    for tf in timeframes:
        scan = results[tf]
        all_results = [r for r in scan.symbol_results if not r.error]
        profitable_count = sum(1 for r in all_results if r.is_profitable)
        total_test_pnl = sum(r.test_pnl for r in all_results)
        avg_test_pnl = total_test_pnl / len(all_results) if all_results else 0
        avg_wr = np.mean([r.test_win_rate for r in all_results if r.test_trades > 0]) if all_results else 0

        print(f"  {tf:<10} | {profitable_count:>10} | {len(all_results):>6} | "
              f"Rs {total_test_pnl:>11,.0f} | Rs {avg_test_pnl:>11,.0f} | {avg_wr:>5.1f}%")

    # Per-timeframe top symbols
    for tf in timeframes:
        scan = results[tf]
        profitable = [r for r in scan.symbol_results if r.is_profitable]
        if profitable:
            print(f"\n  {tf} Top Profitable:")
            for r in profitable[:10]:
                print(f"    {r.trading_symbol:<25} | Train Rs {r.train_pnl:>8,.0f} | "
                      f"Test Rs {r.test_pnl:>8,.0f} | WR {r.test_win_rate:.1f}% | DD {r.test_max_dd:.1f}%")

    print()


if __name__ == "__main__":
    main()
