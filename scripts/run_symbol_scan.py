"""
Symbol Scanner CLI -- Run Walk-Forward Validation Across All Symbols
-------------------------------------------------------------------
Usage:
    python scripts/run_symbol_scan.py                          # Full scan (all symbols)
    python scripts/run_symbol_scan.py --symbols INE155A01022 INE118H01025  # Specific symbols
    python scripts/run_symbol_scan.py --limit 10               # First 10 symbols only
    python scripts/run_symbol_scan.py --timeframe 15m          # Explicit timeframe
"""
import sys
import os
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

# Ensure project root is on path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.analytics.resampler import resample_ohlcv
from core.analytics.indicators.atr import ATR
from core.backtest.symbol_scanner import SymbolScanner
from core.backtest.scan_persistence import ScanPersistence


def progress_printer(current: int, total: int, symbol: str, status: str):
    """Print progress to console."""
    pct = (current / total * 100) if total > 0 else 0
    print(f"  [{current:>3}/{total}] {pct:5.1f}% | {symbol:<25} | {status}")


def main():
    parser = argparse.ArgumentParser(description="PixityAI Symbol Scanner -- Walk-Forward Validation")
    parser.add_argument("--symbols", nargs="*", help="Specific ISINs to scan (e.g. INE155A01022)")
    parser.add_argument("--limit", type=int, default=0, help="Limit to first N symbols (0=all)")
    parser.add_argument("--timeframe", default="15m", help="Timeframe (default: 15m)")
    parser.add_argument("--capital", type=float, default=100000.0, help="Initial capital (default: 100000)")
    parser.add_argument("--train-start", default="2024-10-17", help="Train period start (YYYY-MM-DD)")
    parser.add_argument("--train-end", default="2025-05-31", help="Train period end")
    parser.add_argument("--test-start", default="2025-06-01", help="Test period start")
    parser.add_argument("--test-end", default="2025-12-31", help="Test period end")
    parser.add_argument("--skip-reversion", action="store_true", help="Disable REVERSION signals (TREND-only)")
    parser.add_argument("--min-atr-pct", type=float, default=0.0, help="Min ATR%% to include symbol (e.g. 0.43)")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-symbol progress output")
    parser.add_argument("--no-save", action="store_true", help="Don't persist results to DB")
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Reduce noise from sub-modules
    logging.getLogger("core.database").setLevel(logging.WARNING)
    logging.getLogger("core.runner").setLevel(logging.WARNING)
    logging.getLogger("core.execution").setLevel(logging.WARNING)
    logging.getLogger("core.brokers").setLevel(logging.WARNING)

    db = DatabaseManager(Path("data"))
    scanner = SymbolScanner(db)

    # Build symbol list
    if args.symbols:
        symbols = [{"instrument_key": f"NSE_EQ|{s}", "trading_symbol": s} for s in args.symbols]
    else:
        symbols = scanner.get_all_equity_symbols()

    if args.limit > 0:
        symbols = symbols[:args.limit]

    # ATR% volatility pre-filter
    if args.min_atr_pct > 0:
        print(f"\n  Filtering symbols by ATR% >= {args.min_atr_pct}%...")
        query = MarketDataQuery(db)
        filtered = []
        filter_end = datetime.strptime(args.test_end, "%Y-%m-%d")
        filter_start = filter_end - timedelta(days=120)  # Last ~4 months for ATR calc
        total = len(symbols)
        for i, sym_info in enumerate(symbols):
            if (i + 1) % 20 == 0 or i == 0:
                print(f"    [{i+1}/{total}] scanning ATR%...", flush=True)
            try:
                df_1m = query.get_ohlcv(sym_info["instrument_key"], start_time=filter_start, end_time=filter_end, timeframe="1m")
                if df_1m.empty:
                    continue
                df_1m["timestamp"] = pd.to_datetime(df_1m["timestamp"])
                df_1m.set_index("timestamp", inplace=True)
                df_15m = resample_ohlcv(df_1m, "15m")
                atr_vals = ATR(14).calculate(df_15m)
                atr_pct = (atr_vals / df_15m["close"]).dropna()
                mean_atr_pct = atr_pct.mean() * 100  # as percentage
                if mean_atr_pct >= args.min_atr_pct:
                    filtered.append(sym_info)
            except Exception:
                pass
        print(f"  Filtered to {len(filtered)}/{len(symbols)} symbols (ATR% >= {args.min_atr_pct}%)")
        symbols = filtered

    train_start = datetime.strptime(args.train_start, "%Y-%m-%d")
    train_end = datetime.strptime(args.train_end, "%Y-%m-%d")
    test_start = datetime.strptime(args.test_start, "%Y-%m-%d")
    test_end = datetime.strptime(args.test_end, "%Y-%m-%d")

    # Build strategy params
    strategy_params = {
        "skip_meta_model": True,
        "use_signal_quality_filter": False,
    }
    if args.skip_reversion:
        strategy_params["skip_reversion"] = True

    mode_label = "TREND-only" if args.skip_reversion else "TREND+REVERSION"
    atr_label = f" | ATR% >= {args.min_atr_pct}%" if args.min_atr_pct > 0 else ""

    print(f"\n{'='*70}")
    print(f"  PixityAI Symbol Scanner")
    print(f"  Symbols: {len(symbols)} | Timeframe: {args.timeframe} | Capital: Rs {args.capital:,.0f}")
    print(f"  Mode: {mode_label}{atr_label}")
    print(f"  Train: {args.train_start} -> {args.train_end}")
    print(f"  Test:  {args.test_start} -> {args.test_end}")
    print(f"{'='*70}\n")

    callback = None if args.quiet else progress_printer
    scan = scanner.scan_all_symbols(
        symbols=symbols,
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
        initial_capital=args.capital,
        timeframe=args.timeframe,
        strategy_params=strategy_params,
        progress_callback=callback,
    )

    # Save to DB
    if not args.no_save:
        persistence = ScanPersistence(db)
        persistence.save_scan(scan)
        print(f"\nResults saved to scanner DB (scan_id: {scan.scan_id})")

    # Print summary
    profitable = [r for r in scan.symbol_results if r.is_profitable]
    failed = [r for r in scan.symbol_results if r.error]

    print(f"\n{'='*70}")
    print(f"  SCAN COMPLETE: {scan.profitable_symbols}/{scan.total_symbols} profitable")
    print(f"  Failed/Skipped: {len(failed)}")
    print(f"{'='*70}")

    if profitable:
        print(f"\n  Rank | {'Symbol':<25} | {'Train PnL':>10} | {'Test PnL':>10} | {'Test WR':>7} | {'Test DD':>7} | Trades")
        print(f"  {'-'*100}")
        for r in profitable:
            print(
                f"  {r.rank:>4} | {r.trading_symbol:<25} | "
                f"Rs {r.train_pnl:>8,.0f} | Rs {r.test_pnl:>8,.0f} | "
                f"{r.test_win_rate:>5.1f}% | {r.test_max_dd:>5.1f}% | "
                f"{r.train_trades + r.test_trades}"
            )

        # Correlation matrix
        print("\n  Computing correlation matrix...")
        corr = scanner.compute_correlation_matrix(scan, timeframe=args.timeframe)
        if corr is not None:
            print(f"\n  Pairwise Return Correlation (Test Period):")
            print(corr.round(2).to_string())
        else:
            print("  Not enough profitable symbols for correlation analysis.")
    else:
        print("\n  No profitable symbols found.")

    print()


if __name__ == "__main__":
    main()
