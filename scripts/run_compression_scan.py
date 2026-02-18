"""
PixityAI v3 — Daily Compression Scanner Runner
================================================
Runs the compression scan across the full historical period and reports:
  - Average daily candidate count (long + short)
  - Distribution by symbol (top 20 by frequency)
  - Distribution by month
  - Sample 10 candidate rows
  - ATR percentile cross-sectional distribution check

Usage:
    python scripts/run_compression_scan.py
    python scripts/run_compression_scan.py --start 2025-01-01 --end 2025-12-31
    python scripts/run_compression_scan.py --start 2025-01-01 --end 2025-06-30 --verbose
"""

import sys
import os
import argparse
import logging
from pathlib import Path
from datetime import date, datetime
from collections import defaultdict

import pandas as pd
import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.strategies.expansion_v3.daily_compression_scanner import (
    DailyCompressionScanner,
    CompressionConfig,
    CompressionCandidate,
)
from core.database.manager import DatabaseManager


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Symbol map loader
# ---------------------------------------------------------------------------

def load_symbol_map(db: DatabaseManager) -> dict:
    """Load instrument_key → trading_symbol from config.db fo_stocks table."""
    with db.config_reader() as conn:
        rows = conn.execute(
            "SELECT instrument_key, trading_symbol FROM fo_stocks WHERE is_active = 1"
        ).fetchall()
    symbol_map = {row[0]: row[1] for row in rows}
    logger.info(f"Loaded {len(symbol_map)} symbols from fo_stocks.")
    return symbol_map


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def print_report(all_results: dict, verbose: bool = False):
    """Print structured validation report."""

    all_candidates = []
    for dt, candidates in all_results.items():
        all_candidates.extend(candidates)

    total_days = len(all_results)
    days_with_candidates = sum(1 for c in all_results.values() if len(c) > 0)
    total_candidates = len(all_candidates)

    if total_days == 0:
        print("\n[!] No trading days scanned.")
        return

    print("\n" + "=" * 70)
    print("  PixityAI v3 — DAILY COMPRESSION SCAN REPORT")
    print("=" * 70)

    # --- Daily summary ---
    print(f"\n📅 DATE RANGE")
    dates = sorted(all_results.keys())
    print(f"   From : {dates[0]}")
    print(f"   To   : {dates[-1]}")
    print(f"   Days scanned       : {total_days}")
    print(f"   Days with candidates: {days_with_candidates} "
          f"({100*days_with_candidates/total_days:.1f}%)")

    # --- Candidate counts ---
    daily_counts = [len(v) for v in all_results.values()]
    long_counts = [sum(1 for c in v if c.direction == 'LONG') for v in all_results.values()]
    short_counts = [sum(1 for c in v if c.direction == 'SHORT') for v in all_results.values()]

    print(f"\n📊 DAILY CANDIDATE COUNTS")
    print(f"   Total candidates   : {total_candidates}")
    print(f"   Avg per day (total): {np.mean(daily_counts):.1f}")
    print(f"   Avg per day (long) : {np.mean(long_counts):.1f}")
    print(f"   Avg per day (short): {np.mean(short_counts):.1f}")
    print(f"   Max in a day       : {max(daily_counts)}")
    print(f"   Min in a day       : {min(daily_counts)}")
    print(f"   Median per day     : {np.median(daily_counts):.1f}")

    # --- Filter density assessment ---
    avg = np.mean(daily_counts)
    print(f"\n🎯 FILTER DENSITY ASSESSMENT")
    if avg < 3:
        verdict = "⚠️  TOO TIGHT — fewer than 3 candidates/day average. Relax filters."
    elif avg > 30:
        verdict = "⚠️  TOO LOOSE — more than 30 candidates/day average. Tighten filters."
    else:
        verdict = "✅ BALANCED — candidate count in target range (3–30/day)."
    print(f"   Avg/day: {avg:.1f}  →  {verdict}")

    # --- Monthly distribution ---
    print(f"\n📆 MONTHLY DISTRIBUTION")
    monthly: dict = defaultdict(lambda: {"long": 0, "short": 0, "days": 0})
    for dt, candidates in all_results.items():
        key = dt.strftime("%Y-%m")
        monthly[key]["days"] += 1
        for c in candidates:
            monthly[key][c.direction.lower()] += 1

    print(f"   {'Month':<10} {'Days':>5} {'Long':>6} {'Short':>6} {'Total':>7} {'Avg/Day':>8}")
    print(f"   {'-'*10} {'-'*5} {'-'*6} {'-'*6} {'-'*7} {'-'*8}")
    for month in sorted(monthly.keys()):
        m = monthly[month]
        total = m["long"] + m["short"]
        avg_d = total / m["days"] if m["days"] > 0 else 0
        print(f"   {month:<10} {m['days']:>5} {m['long']:>6} {m['short']:>6} {total:>7} {avg_d:>8.1f}")

    # --- Top symbols by frequency ---
    print(f"\n🏆 TOP 20 SYMBOLS BY CANDIDATE FREQUENCY")
    sym_counts: dict = defaultdict(lambda: {"LONG": 0, "SHORT": 0})
    for c in all_candidates:
        sym_counts[c.trading_symbol][c.direction] += 1

    sym_df = pd.DataFrame([
        {
            "symbol": sym,
            "long": counts["LONG"],
            "short": counts["SHORT"],
            "total": counts["LONG"] + counts["SHORT"],
        }
        for sym, counts in sym_counts.items()
    ]).sort_values("total", ascending=False).head(20)

    print(f"   {'Symbol':<15} {'Long':>6} {'Short':>6} {'Total':>7}")
    print(f"   {'-'*15} {'-'*6} {'-'*6} {'-'*7}")
    for _, row in sym_df.iterrows():
        print(f"   {row['symbol']:<15} {row['long']:>6} {row['short']:>6} {row['total']:>7}")

    # --- ATR percentile distribution check ---
    if all_candidates:
        atr_ranks = [c.atr_universe_rank for c in all_candidates]
        atr_pcts  = [c.atr_pct for c in all_candidates]
        rs_vals = [c.rs_5d for c in all_candidates]
        range_ratios = [c.range_ratio for c in all_candidates]

        print(f"\n📈 INDICATOR DISTRIBUTION (sanity check)")
        print(f"   ATR Universe Rank — mean: {np.mean(atr_ranks):.1f}  "
              f"max: {max(atr_ranks):.1f}  (all should be < 30)")
        print(f"   ATR% (raw)        — mean: {np.mean(atr_pcts)*100:.3f}%  "
              f"std: {np.std(atr_pcts)*100:.3f}%")
        print(f"   RS_5d (%)         — mean: {np.mean(rs_vals)*100:.2f}  "
              f"std: {np.std(rs_vals)*100:.2f}")
        print(f"   Range Ratio       — mean: {np.mean(range_ratios):.3f}  "
              f"(all should be < 0.50)")

        long_rs = [c.rs_5d for c in all_candidates if c.direction == 'LONG']
        short_rs = [c.rs_5d for c in all_candidates if c.direction == 'SHORT']
        if long_rs:
            print(f"   Long RS_5d > 0  — ✅ {all(r > 0 for r in long_rs)} "
                  f"(mean: {np.mean(long_rs)*100:.3f}%)")
        if short_rs:
            print(f"   Short RS_5d < 0 — ✅ {all(r < 0 for r in short_rs)} "
                  f"(mean: {np.mean(short_rs)*100:.3f}%)")

    # --- Sample rows ---
    print(f"\n📋 SAMPLE CANDIDATES (10 random rows)")
    if all_candidates:
        sample = all_candidates[:10] if len(all_candidates) <= 10 else \
                 [all_candidates[i] for i in np.random.choice(len(all_candidates), 10, replace=False)]
        sample_df = pd.DataFrame([c.to_dict() for c in sorted(sample, key=lambda x: x.scan_date)])
        cols = ["scan_date", "trading_symbol", "direction", "atr_universe_rank",
                "atr_pct", "rs_5d", "range_ratio", "price_vs_structure", "liquidity_cr", "close"]
        print(sample_df[cols].to_string(index=False))

    # --- Verbose: full daily log ---
    if verbose:
        print(f"\n📜 FULL DAILY LOG")
        print(f"   {'Date':<12} {'Long':>5} {'Short':>6} {'Total':>7}")
        print(f"   {'-'*12} {'-'*5} {'-'*6} {'-'*7}")
        for dt in sorted(all_results.keys()):
            candidates = all_results[dt]
            n_long = sum(1 for c in candidates if c.direction == 'LONG')
            n_short = sum(1 for c in candidates if c.direction == 'SHORT')
            print(f"   {dt.isoformat():<12} {n_long:>5} {n_short:>6} {len(candidates):>7}")

    print("\n" + "=" * 70)
    print("  Scan complete. Review filter density before proceeding to 1H trigger.")
    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PixityAI v3 Daily Compression Scanner")
    parser.add_argument("--start", default="2025-01-01",
                        help="Start date (YYYY-MM-DD). Default: 2025-01-01")
    parser.add_argument("--end", default=None,
                        help="End date (YYYY-MM-DD). Default: today")
    parser.add_argument("--verbose", action="store_true",
                        help="Print full daily log")
    parser.add_argument("--save", default=None,
                        help="Save results to CSV path (optional)")
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end) if args.end else date.today()

    logger.info(f"PixityAI v3 — Daily Compression Scanner")
    logger.info(f"Range: {start_date} → {end_date}")

    # Load symbol map
    db = DatabaseManager(ROOT / "data")
    symbol_map = load_symbol_map(db)

    if not symbol_map:
        logger.error("No symbols loaded. Check fo_stocks table in config.db.")
        sys.exit(1)

    # Build scanner — v3 config (cross-sectional ATR ranking)
    cfg = CompressionConfig(
        atr_universe_pct_threshold=30.0,
        atr_own_history_days=60,
        atr_period=20,
        range_compression_ratio=0.50,
        structure_proximity_long=0.90,
        structure_proximity_short=1.10,
        min_liquidity_cr=10.0,
        rs_lookback=5,
        min_history=65,
    )

    scanner = DailyCompressionScanner(
        data_root=ROOT / "data",
        config=cfg,
        symbol_map=symbol_map,
    )

    # Run
    results = scanner.scan_range(start_date, end_date)

    # Report
    print_report(results, verbose=args.verbose)

    # Optional CSV save
    if args.save:
        all_rows = []
        for dt, candidates in results.items():
            for c in candidates:
                all_rows.append(c.to_dict())
        if all_rows:
            df = pd.DataFrame(all_rows)
            df.to_csv(args.save, index=False)
            logger.info(f"Saved {len(all_rows)} candidate rows to {args.save}")

    return results


if __name__ == "__main__":
    main()
