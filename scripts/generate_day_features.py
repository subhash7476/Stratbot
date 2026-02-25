"""
Day-Type Feature Generator
==========================
Processes Nifty 50 1-minute data (2023–2026) and outputs per-year CSVs
with 53 engineered features per trading day, for unsupervised day-type clustering.

Usage:
    python scripts/generate_day_features.py
    python scripts/generate_day_features.py --symbol "NSE_INDEX|Nifty 50" --start 2023-01-01 --end 2026-02-13
    python scripts/generate_day_features.py --year 2025
    python scripts/generate_day_features.py --start 2024-01-01 --end 2024-12-31 --output-dir data/features/test/

Output:
    data/features/day_type/nifty_day_features_2023.csv
    data/features/day_type/nifty_day_features_2024.csv
    data/features/day_type/nifty_day_features_2025.csv
    data/features/day_type/nifty_day_features_2026.csv
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb
from core.database.utils.market_hours import MarketHours
from core.analytics.day_features import (
    compute_session_features,
    block_a_gap_context,
    finalize_dataframe,
    EXPECTED_BARS,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

DEFAULT_SYMBOL  = "NSE_INDEX|Nifty 50"
DEFAULT_OUTPUT  = ROOT / "data" / "features" / "day_type"
DATA_ROOT       = ROOT / "data"
CANDLE_DIR_1M   = DATA_ROOT / "market_data" / "nse" / "candles" / "1m"


# ── Date range utilities ───────────────────────────────────────────────────────

def get_trading_days(start: date, end: date) -> list[date]:
    """Return all NSE trading days in [start, end]."""
    days = []
    d = start
    while d <= end:
        dt = datetime.combine(d, datetime.min.time())
        if MarketHours.is_trading_day(dt):
            days.append(d)
        d += timedelta(days=1)
    return days


def available_date_range(data_root: Path) -> tuple[date, date]:
    """Detect earliest and latest 1m DuckDB files."""
    candle_dir = data_root / "market_data" / "nse" / "candles" / "1m"
    files = sorted(candle_dir.glob("*.duckdb"))
    if not files:
        raise FileNotFoundError(f"No 1m candle files found in {candle_dir}")
    first = date.fromisoformat(files[0].stem)
    last  = date.fromisoformat(files[-1].stem)
    return first, last


# ── Session loading ────────────────────────────────────────────────────────────

def load_session(d: date, symbol: str) -> pd.DataFrame:
    """
    Load 1m session data for a single trading day directly from DuckDB.
    Bypasses MarketDataQuery (which is optimised for recent data, not arbitrary historical dates).
    """
    db_path = CANDLE_DIR_1M / f"{d.isoformat()}.duckdb"
    if not db_path.exists():
        return pd.DataFrame()
    try:
        con = duckdb.connect(str(db_path), read_only=True)
        df = con.execute(
            "SELECT * FROM candles WHERE symbol = ? ORDER BY timestamp",
            [symbol]
        ).df()
        con.close()
    except Exception as e:
        logger.warning(f"  Could not read {db_path}: {e}")
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    # Filter to session hours only (9:15–15:29 IST)
    hour_min = df['timestamp'].dt.hour * 60 + df['timestamp'].dt.minute
    df = df[(hour_min >= 555) & (hour_min <= 929)].reset_index(drop=True)
    return df


# ── Main generation loop ───────────────────────────────────────────────────────

def generate_features(
    symbol: str,
    start: date,
    end: date,
    output_dir: Path,
) -> pd.DataFrame:
    """
    Generate daily feature rows for all trading days in [start, end].
    Returns the full DataFrame (all years combined).
    """
    trading_days = get_trading_days(start, end)
    logger.info(f"Generating features for {symbol}")
    logger.info(f"Date range: {start} → {end} ({len(trading_days)} trading days)")

    rows = []
    skipped = 0

    for i, d in enumerate(trading_days):
        if (i + 1) % 50 == 0:
            logger.info(f"  Progress: {i+1}/{len(trading_days)} days processed")

        session = load_session(d, symbol)

        if session.empty:
            logger.debug(f"  Skipping {d}: no data")
            skipped += 1
            continue

        features = compute_session_features(session)
        if not features:
            logger.debug(f"  Skipping {d}: too few bars ({len(session)})")
            skipped += 1
            continue

        features['date'] = d
        features['symbol'] = symbol
        rows.append(features)

    logger.info(f"Collected {len(rows)} days ({skipped} skipped)")

    if not rows:
        logger.error("No data collected. Exiting.")
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index('date').sort_index()
    df.index = pd.to_datetime(df.index)

    # ── Block A: fill gap context from previous days ──────────────────────────
    logger.info("Computing Block A (gap & previous context)...")
    a_rows = []
    for i in range(len(df)):
        a = block_a_gap_context(i, df)
        a_rows.append(a)
    a_df = pd.DataFrame(a_rows, index=df.index)
    for col in a_df.columns:
        df[col] = a_df[col].values

    # ── Finalize: rolling percentiles, drop internal cols ────────────────────
    logger.info("Finalizing rolling features...")
    df = finalize_dataframe(df)

    # ── Save per-year CSVs ────────────────────────────────────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)
    years = df.index.year.unique()
    for year in sorted(years):
        year_df = df[df.index.year == year]
        fname = output_dir / f"nifty_day_features_{year}.csv"
        year_df.to_csv(fname)
        logger.info(f"  Saved {len(year_df)} rows → {fname}")

    return df


# ── Audit report ─────────────────────────────────────────────────────────────

FEATURE_COLS = [
    # Block A
    'gap_pct', 'gap_dir', 'gap_size_pct60', 'prev_day_return', 'prev_day_range',
    'prev_day_clv', 'prev_day_slope', 'prev_day_vol_pct',
    # Block B
    'open_5m_ret', 'open_15m_ret', 'open_30m_ret', 'open_30m_range',
    'open_30m_range_ratio', 'open_30m_high_break_min', 'open_30m_low_break_min',
    'open_30m_twap_dist', 'open_30m_vol_ratio',
    # Block C
    'full_day_return', 'day_range_pct', 'clv', 'linreg_slope', 'linreg_r2',
    'hh_count_15m', 'll_count_15m', 'max_twap_excursion',
    # Block D
    'realized_vol', 'intraday_atr_5m', 'range_pct_vs20d',
    'range_pct_before_11am', 'range_pct_after_130pm',
    'largest_5m_candle', 'log_vol_expansion', 'vol_clustering',
    'center_of_mass_return_time',
    # Block E
    'pct_min_above_twap', 'pct_min_below_twap', 'twap_cross_count',
    'longest_above_twap', 'longest_below_twap', 'close_dist_twap', 'twap_dist_std',
    # Block F
    'flip_count_15m', 'inside_bar_pct_15m', 'median_body_pct_15m',
    'avg_adverse_excursion', 'max_adverse_excursion',
    'overlap_ratio_15m', 'dominant_direction_strength',
    # Block G (zero for index — retained for schema)
    'total_vol_pct20', 'first_hour_vol_pct', 'vol_skew_ampm',
    'vol_acceleration', 'vol_wtd_momentum',
]


def print_audit_report(df: pd.DataFrame) -> None:
    """Print pre-clustering audit statistics."""
    feat_cols = [c for c in FEATURE_COLS if c in df.columns]
    feat_df = df[feat_cols].copy()

    print("\n" + "=" * 70)
    print("PRE-CLUSTERING AUDIT REPORT")
    print("=" * 70)

    # 1. Row count
    print(f"\nTotal rows: {len(df)}")
    by_year = df.groupby(df.index.year).size()
    print("By year:", dict(by_year))

    # 2. Missing values
    nan_counts = feat_df.isnull().sum()
    nan_cols = nan_counts[nan_counts > 0]
    if nan_cols.empty:
        print("\nNo NaN values in feature columns (except expected rolling warmup).")
    else:
        print(f"\nNaN counts per feature (first 5 may be rolling warmup):")
        print(nan_cols.to_string())

    # 3. Degenerate columns (std ≈ 0 — will be dropped before clustering)
    stds = feat_df.std()
    degen = stds[stds < 1e-6]
    print(f"\nDegenerate features (std < 1e-6, will be dropped before clustering):")
    if degen.empty:
        print("  None")
    else:
        print("  " + ", ".join(degen.index.tolist()))

    # 4. Distribution summary
    print("\nFeature distribution summary (non-zero std features only):")
    stats = feat_df.loc[:, stds > 1e-6].describe(percentiles=[0.01, 0.25, 0.5, 0.75, 0.99]).T
    skewness = feat_df.loc[:, stds > 1e-6].skew()
    kurtosis = feat_df.loc[:, stds > 1e-6].kurt()
    stats['skew'] = skewness
    stats['kurt'] = kurtosis
    flagged = stats[(stats['skew'].abs() > 3) | (stats['kurt'] > 10)]
    if not flagged.empty:
        print("  !! Features with skew > 3 or kurtosis > 10 (winsorize before clustering):")
        print(flagged[['mean', 'std', 'skew', 'kurt']].to_string())
    else:
        print("  All features within normal distribution shape (skew ≤ 3, kurt ≤ 10)")

    # 5. High-correlation pairs
    print("\nHighly correlated feature pairs (|corr| > 0.90):")
    corr = feat_df.loc[:, stds > 1e-6].corr().abs()
    upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
    high_pairs = [(r, c, upper.loc[r, c]) for r in upper.index for c in upper.columns
                  if pd.notna(upper.loc[r, c]) and upper.loc[r, c] > 0.90]
    if high_pairs:
        for r, c, v in sorted(high_pairs, key=lambda x: -x[2]):
            print(f"  {r}  <->  {c}:  {v:.3f}")
    else:
        print("  None above 0.90 threshold")

    # 6. Regime drift check (2023–2024 vs 2025–2026)
    print("\nRegime drift check -- feature means (2023-24 vs 2025-26):")
    df_early = feat_df[feat_df.index.year <= 2024]
    df_late  = feat_df[feat_df.index.year >= 2025]
    if not df_early.empty and not df_late.empty:
        drift = (df_late.mean() - df_early.mean()).abs() / (df_early.std() + 1e-8)
        large_drift = drift[drift > 1.0].sort_values(ascending=False)
        if large_drift.empty:
            print("  No features with mean shift > 1 std-dev between periods")
        else:
            print("  !! Features with mean shift > 1 std-dev (may need regime-normalized treatment):")
            print(large_drift.to_string())
    else:
        print("  Not enough data across both regimes to compare")

    # 7. Auditor spot checks
    print("\nAuditor spot checks:")

    if 'linreg_r2' in df.columns:
        top5_trend = df.nlargest(5, 'linreg_r2')[['linreg_r2', 'full_day_return', 'day_range_pct']]
        print("\n  Top 5 highest linreg_r2 (should be strong trend days):")
        print(top5_trend.to_string())

    if 'day_range_pct' in df.columns:
        bot5_range = df.nsmallest(5, 'day_range_pct')[['day_range_pct', 'clv', 'vol_clustering']]
        print("\n  Top 5 lowest day_range_pct (should be compression/inside days):")
        print(bot5_range.to_string())

    if 'center_of_mass_return_time' in df.columns:
        top5_late = df.nlargest(5, 'center_of_mass_return_time')[
            ['center_of_mass_return_time', 'range_pct_after_130pm', 'full_day_return']
        ]
        print("\n  Top 5 highest center_of_mass_return_time (should be late-expansion days):")
        print(top5_late.to_string())

    if 'twap_cross_count' in df.columns:
        top5_chop = df.nlargest(5, 'twap_cross_count')[
            ['twap_cross_count', 'flip_count_15m', 'clv', 'day_range_pct']
        ]
        print("\n  Top 5 highest twap_cross_count (should be choppy/rotational days):")
        print(top5_chop.to_string())

    print("\n" + "=" * 70)
    print("Audit complete. Review flagged features before clustering.")
    print("=" * 70 + "\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Generate day-type features from Nifty 50 1m data")
    p.add_argument('--symbol', default=DEFAULT_SYMBOL,
                   help=f'Instrument key (default: {DEFAULT_SYMBOL})')
    p.add_argument('--start', type=str, default=None,
                   help='Start date YYYY-MM-DD (default: earliest available)')
    p.add_argument('--end', type=str, default=None,
                   help='End date YYYY-MM-DD (default: latest available)')
    p.add_argument('--year', type=int, default=None,
                   help='Generate only this year (overrides --start/--end)')
    p.add_argument('--output-dir', type=str, default=str(DEFAULT_OUTPUT),
                   help=f'Output directory (default: {DEFAULT_OUTPUT})')
    p.add_argument('--no-audit', action='store_true',
                   help='Skip printing the audit report')
    return p.parse_args()


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)

    # Detect available date range
    earliest, latest = available_date_range(DATA_ROOT)
    logger.info(f"Available 1m data: {earliest} → {latest}")

    if args.year:
        start = date(args.year, 1, 1)
        end   = date(args.year, 12, 31)
    else:
        start = date.fromisoformat(args.start) if args.start else earliest
        end   = date.fromisoformat(args.end)   if args.end   else latest

    start = max(start, earliest)
    end   = min(end,   latest)

    df = generate_features(
        symbol=args.symbol,
        start=start,
        end=end,
        output_dir=output_dir,
    )

    if df.empty:
        logger.error("No features generated.")
        sys.exit(1)

    if not args.no_audit:
        print_audit_report(df)

    logger.info("Done.")


if __name__ == '__main__':
    main()
