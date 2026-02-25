"""
Intraday Partial Feature Builder
=================================
Computes partial-day features at 3 checkpoints for each historical day:
  - 10:00 AM  (bar index 45,  45 minutes of data)
  - 11:00 AM  (bar index 105, 105 minutes of data)
  - 13:00 PM  (bar index 225, 225 minutes of data)

For each checkpoint, only features computable from bars seen so far are included.
Full-day features (full_day_return, full linreg_r2, etc.) are EXCLUDED —
they are the targets, not predictors.

Block A context features (gap, prev-day) are available from open — included.
G-block (volume) always 0 for index — excluded.

Output:
  data/features/day_type/intraday_features_10am.csv
  data/features/day_type/intraday_features_11am.csv
  data/features/day_type/intraday_features_13pm.csv

Usage:
  python scripts/build_intraday_features.py
  python scripts/build_intraday_features.py --symbol "NSE_INDEX|Nifty 50"
  python scripts/build_intraday_features.py --start 2023-01-01 --end 2026-02-13
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.analytics.resampler import resample_ohlcv
from core.analytics.day_features import (
    EPSILON,
    AM_END_BAR,
    CLV_THRESHOLD,
    _range_epsilon,
    compute_session_twap,
    _rolling_pct_rank,
)

CANDLE_DIR_1M = ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
FEATURE_DIR   = ROOT / "data" / "features" / "day_type"
EOD_FEATURE_DIR = FEATURE_DIR  # source of full-day features + cluster labels

NF_SYMBOL = 'NSE_INDEX|Nifty 50'
BN_SYMBOL  = 'NSE_INDEX|Nifty Bank'

# ── Checkpoint definitions ─────────────────────────────────────────────────────
# Bar index = minutes since 9:15 open (0-indexed)
# 10:00 AM = 9:15 + 45 min  → bar 45
# 11:00 AM = 9:15 + 105 min → bar 105
# 13:00 PM = 9:15 + 225 min → bar 225

CHECKPOINTS = {
    '10am': 45,
    '11am': 105,
    '13pm': 225,
}

# Features that are full-day only — never available at a checkpoint
# (These become the Y labels, not X features)
FULL_DAY_ONLY = {
    'full_day_return',       # requires session close
    'day_range_pct',         # requires session high/low
    'clv',                   # requires session close/high/low
    'linreg_r2',             # requires full session regression
    'linreg_slope',          # requires full session regression
    'hh_count_15m',          # requires full day 15m bars
    'll_count_15m',          # requires full day 15m bars
    'max_twap_excursion',    # requires full session TWAP
    'realized_vol',          # requires full session returns
    'intraday_atr_5m',       # requires full session 5m ATR
    'range_pct_after_130pm', # only defined after 1:30 PM
    'log_vol_expansion',     # AM vs PM std — requires PM data
    'vol_clustering',        # autocorr of full session returns
    'center_of_mass_return_time',  # weighted center of full session
    'pct_min_above_twap',    # fraction of full session above TWAP
    'pct_min_below_twap',    # fraction of full session below TWAP
    'twap_cross_count',      # full session TWAP crossings
    'longest_above_twap',    # full session TWAP runs
    'longest_below_twap',    # full session TWAP runs
    'close_dist_twap',       # requires session close
    'twap_dist_std',         # std over full session
    'flip_count_15m',        # requires full day 15m returns
    'inside_bar_pct_15m',    # requires full day 15m bars
    'avg_adverse_excursion', # requires full session
    'max_adverse_excursion', # requires full session
    'overlap_ratio_15m',     # requires full day 15m bars
    'dominant_direction_strength',  # |CLV| — requires session close
    'largest_5m_candle',     # max of full day 5m ranges
}

# Features computable only after 1:30 PM (not at 10am/11am)
AFTER_130PM_ONLY = {
    'range_pct_after_130pm',
}


# ── Data loading ───────────────────────────────────────────────────────────────

def load_session_truncated(d: date, symbol: str, n_bars: int) -> pd.DataFrame | None:
    """
    Load 1m session bars for a date, truncated to first n_bars.
    Returns None if file missing or insufficient bars.
    """
    db_path = CANDLE_DIR_1M / f"{d.isoformat()}.duckdb"
    if not db_path.exists():
        return None

    try:
        con = duckdb.connect(str(db_path), read_only=True)
        df = con.execute(
            "SELECT * FROM candles WHERE symbol = ? ORDER BY timestamp",
            [symbol]
        ).df()
        con.close()
    except Exception:
        return None

    if df.empty:
        return None

    # Filter to session hours: 9:15 (555 min) to 15:29 (929 min)
    hour_min = df['timestamp'].dt.hour * 60 + df['timestamp'].dt.minute
    df = df[(hour_min >= 555) & (hour_min <= 929)].reset_index(drop=True)

    if len(df) < n_bars:
        return None  # not enough data for this checkpoint

    # Truncate to checkpoint
    return df.iloc[:n_bars].copy()


# ── Checkpoint feature computation ────────────────────────────────────────────

def compute_checkpoint_features(df_trunc: pd.DataFrame, n_bars: int) -> dict:
    """
    Compute all features that can be determined from df_trunc (first n_bars).
    Returns a flat dict. Full-session features are excluded.
    """
    df = df_trunc.copy().reset_index(drop=True)
    open_price = df['open'].iloc[0]
    n = len(df)

    # Resample to 5m and 15m from truncated data
    df5m  = resample_ohlcv(df, '5m')
    df15m = resample_ohlcv(df, '15m')

    # TWAP up to checkpoint
    twap = compute_session_twap(df)

    # Partial session extremes
    partial_high = df['high'].max()
    partial_low  = df['low'].min()
    partial_close = df['close'].iloc[-1]
    partial_range = partial_high - partial_low
    range_denom = max(partial_range, EPSILON * open_price)

    feats = {}

    # ── Block B: Opening structure (fully available at 10am/11am/13pm) ─────────
    bar5  = df.iloc[4]  if n > 4  else df.iloc[-1]
    bar15 = df.iloc[14] if n > 14 else df.iloc[-1]
    bar30 = df.iloc[29] if n > 29 else df.iloc[-1]

    feats['open_5m_ret']  = (bar5['close']  - open_price) / open_price
    feats['open_15m_ret'] = (bar15['close'] - open_price) / open_price
    feats['open_30m_ret'] = (bar30['close'] - open_price) / open_price

    first30 = df.iloc[:30]
    open_30m_high = first30['high'].max()
    open_30m_low  = first30['low'].min()
    feats['open_30m_range'] = (open_30m_high - open_30m_low) / open_price

    # open_30m_range_ratio: ratio to FULL day range — not available at checkpoints
    # Use partial range as proxy instead (with flag)
    feats['open_30m_range_ratio_partial'] = (open_30m_high - open_30m_low) / range_denom

    # Time-of-break features
    close_series = df['close'].values
    high_breaks = np.where(close_series > open_30m_high)[0]
    low_breaks  = np.where(close_series < open_30m_low)[0]
    feats['open_30m_high_break_min'] = int(high_breaks[0]) if len(high_breaks) > 0 else -1
    feats['open_30m_low_break_min']  = int(low_breaks[0])  if len(low_breaks)  > 0 else -1

    # TWAP dist at bar 30
    twap_at_30 = twap.iloc[29] if len(twap) > 29 else twap.iloc[-1]
    feats['open_30m_twap_dist'] = (twap_at_30 - open_price) / open_price * 100

    # ── Block C: Partial trend ─────────────────────────────────────────────────
    # Return so far
    feats['partial_return'] = (partial_close - open_price) / open_price
    feats['partial_range_pct'] = partial_range / open_price

    # Partial CLV
    re = _range_epsilon(partial_high, partial_low, open_price)
    feats['partial_clv'] = (partial_close - partial_low - (partial_high - partial_close)) / re

    # Partial linreg on closes seen so far
    if n >= 2:
        x = np.arange(n)
        y = df['close'].values
        slope, intercept = np.polyfit(x, y, 1)
        y_pred = slope * x + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        feats['partial_linreg_r2']    = 1.0 - ss_res / ss_tot if ss_tot > EPSILON else 0.0
        feats['partial_linreg_slope'] = slope / open_price
    else:
        feats['partial_linreg_r2']    = 0.0
        feats['partial_linreg_slope'] = 0.0

    # Partial HH/LL in 15m bars so far
    n15 = len(df15m)
    if n15 >= 2:
        hh = int((df15m['high'].values[1:] > df15m['high'].values[:-1]).sum())
        ll = int((df15m['low'].values[1:]  < df15m['low'].values[:-1]).sum())
        feats['partial_hh_count_15m'] = hh / (n15 - 1)
        feats['partial_ll_count_15m'] = ll / (n15 - 1)
    else:
        feats['partial_hh_count_15m'] = 0.0
        feats['partial_ll_count_15m'] = 0.0

    # ── Block D: Partial volatility ────────────────────────────────────────────
    ret1m = df['close'].pct_change().dropna()
    feats['partial_realized_vol'] = float(ret1m.std()) if len(ret1m) >= 2 else 0.0

    # Partial ATR from 5m bars
    if len(df5m) >= 2:
        hi = df5m['high'].values
        lo = df5m['low'].values
        cl = df5m['close'].values
        tr1 = hi - lo
        tr2 = np.abs(hi[1:] - cl[:-1])
        tr3 = np.abs(lo[1:] - cl[:-1])
        tr  = np.concatenate([[tr1[0]], np.maximum(tr1[1:], np.maximum(tr2, tr3))])
        feats['partial_atr_5m'] = float(tr.mean()) / open_price
    else:
        feats['partial_atr_5m'] = 0.0

    # AM range (before 11am, bar < 105) — fully available at 11am and 13pm
    am_bars = df.iloc[:min(AM_END_BAR, n)]
    am_high = am_bars['high'].max()
    am_low  = am_bars['low'].min()
    feats['range_pct_before_11am_partial'] = (am_high - am_low) / range_denom

    # Partial log vol expansion: AM std vs std of remainder so far
    am_ret = df['close'].iloc[:min(AM_END_BAR, n)].pct_change().dropna()
    rest_ret = df['close'].iloc[AM_END_BAR:].pct_change().dropna()
    std_am   = float(am_ret.std())   if len(am_ret) >= 2   else EPSILON
    std_rest = float(rest_ret.std()) if len(rest_ret) >= 2 else EPSILON
    import math
    feats['partial_log_vol_expansion'] = math.log(
        max(std_am, EPSILON) / max(std_rest, EPSILON)
    ) if n_bars > AM_END_BAR else 0.0

    # Partial center of mass
    indices = np.arange(n)
    abs_ret_full = df['close'].pct_change().abs().fillna(0).values
    sum_abs = abs_ret_full.sum()
    if sum_abs > EPSILON:
        com = float(np.dot(indices, abs_ret_full) / sum_abs) / max(n - 1, 1)
    else:
        com = 0.5
    feats['partial_center_of_mass'] = float(np.clip(com, 0.0, 1.0))

    # ── Block E: Partial TWAP microstructure ──────────────────────────────────
    diff = df['close'] - twap

    feats['partial_pct_above_twap']  = float((diff > 0).mean())
    feats['partial_twap_cross_count'] = int((np.diff(np.sign(diff.values)) != 0).sum())

    def longest_run(mask):
        runs, current = 0, 0
        for v in mask:
            if v:
                current += 1
                runs = max(runs, current)
            else:
                current = 0
        return runs

    feats['partial_longest_above_twap'] = longest_run(diff.values > 0)
    feats['partial_longest_below_twap'] = longest_run(diff.values < 0)
    feats['partial_close_dist_twap']    = (partial_close - twap.iloc[-1]) / open_price * 100
    feats['partial_twap_dist_std']      = float(diff.std()) if len(diff) >= 2 else 0.0

    # ── Block F: Partial rotation ──────────────────────────────────────────────
    ret15 = df15m['close'].pct_change().dropna()
    if len(ret15) >= 2:
        feats['partial_flip_count_15m'] = int((np.diff(np.sign(ret15.values)) != 0).sum())
    else:
        feats['partial_flip_count_15m'] = 0

    partial_clv_val = feats['partial_clv']
    feats['partial_dominant_direction_strength'] = abs(partial_clv_val)
    if abs(partial_clv_val) >= CLV_THRESHOLD and n15 >= 2:
        if partial_clv_val > 0:
            excursions = (partial_high - df15m['low']).clip(lower=0)
        else:
            excursions = (df15m['high'] - partial_low).clip(lower=0)
        feats['partial_avg_adverse_excursion'] = float(excursions.mean()) / range_denom
    else:
        feats['partial_avg_adverse_excursion'] = 0.0

    # ── Derived momentum / structure signals ──────────────────────────────────
    # Useful composite features for the classifier
    # Trend consistency: linreg_r2 * sign(slope)
    feats['partial_trend_score'] = (
        feats['partial_linreg_r2'] * np.sign(feats['partial_linreg_slope'])
    )
    # Net HH minus LL (directional conviction)
    feats['partial_hh_minus_ll'] = (
        feats['partial_hh_count_15m'] - feats['partial_ll_count_15m']
    )
    # TWAP adherence: pct above - 0.5 (positive = bullish lean)
    feats['partial_twap_lean'] = feats['partial_pct_above_twap'] - 0.5

    # Bars into session at checkpoint (useful for normalization)
    feats['checkpoint_bar'] = n_bars

    return feats


# ── Block H: BankNifty/Nifty intermarket features ─────────────────────────────

def compute_block_h_intermarket(
    df_nf: pd.DataFrame,
    df_bn: pd.DataFrame | None,
    n_bars: int,
) -> dict:
    """
    Block H: BankNifty vs Nifty relative-strength features.
    Both DataFrames must be truncated to n_bars before calling.
    Returns zeros if df_bn is None or has < 5 bars (graceful degradation).
    Volume is 0 for both indices — only price-based features are used.
    """
    zero = {
        'bn_nf_open_5m_spread':          0.0,
        'bn_nf_open_30m_spread':         0.0,
        'bn_nf_partial_return_spread':   0.0,
        'bn_nf_correlation_5m':          0.0,
        'bn_nf_range_ratio':             1.0,
        'bn_leads_nifty':                0.0,
    }
    if df_bn is None or df_bn.empty or len(df_bn) < 5:
        return zero

    nf_open = float(df_nf['open'].iloc[0])
    bn_open = float(df_bn['open'].iloc[0])

    n = min(len(df_nf), len(df_bn))
    df_nf_s = df_nf.iloc[:n].reset_index(drop=True)
    df_bn_s = df_bn.iloc[:n].reset_index(drop=True)

    nf_5m  = (float(df_nf_s['close'].iloc[4])  - nf_open) / nf_open if n > 4  else 0.0
    bn_5m  = (float(df_bn_s['close'].iloc[4])  - bn_open) / bn_open if n > 4  else 0.0
    nf_30m = (float(df_nf_s['close'].iloc[29]) - nf_open) / nf_open if n > 29 else nf_5m
    bn_30m = (float(df_bn_s['close'].iloc[29]) - bn_open) / bn_open if n > 29 else bn_5m

    nf_partial = (float(df_nf_s['close'].iloc[-1]) - nf_open) / nf_open
    bn_partial = (float(df_bn_s['close'].iloc[-1]) - bn_open) / bn_open

    # 1m return correlation in first 30 bars (measures synchrony)
    n_corr = min(n, 30)
    corr = 0.0
    if n_corr >= 5:
        nf_ret = df_nf_s['close'].iloc[:n_corr].pct_change().dropna()
        bn_ret = df_bn_s['close'].iloc[:n_corr].pct_change().dropna()
        min_len = min(len(nf_ret), len(bn_ret))
        if min_len >= 3 and nf_ret.std() > EPSILON and bn_ret.std() > EPSILON:
            corr = float(np.corrcoef(nf_ret.values[:min_len], bn_ret.values[:min_len])[0, 1])
            corr = 0.0 if np.isnan(corr) else float(np.clip(corr, -1.0, 1.0))

    nf_range = (float(df_nf_s['high'].max()) - float(df_nf_s['low'].min())) / max(nf_open, EPSILON)
    bn_range = (float(df_bn_s['high'].max()) - float(df_bn_s['low'].min())) / max(bn_open, EPSILON)

    return {
        'bn_nf_open_5m_spread':        bn_5m - nf_5m,
        'bn_nf_open_30m_spread':       bn_30m - nf_30m,
        'bn_nf_partial_return_spread': bn_partial - nf_partial,
        'bn_nf_correlation_5m':        corr,
        'bn_nf_range_ratio':           bn_range / max(nf_range, EPSILON),
        'bn_leads_nifty':              float(np.sign(bn_5m) == np.sign(nf_partial)),
    }


# ── Cross-day rolling features ─────────────────────────────────────────────────

def add_rolling_context(df: pd.DataFrame, eod_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add Block A features (gap, prev-day context) and rolling percentile ranks.
    These are available from the market open — valid at all checkpoints.
    Block A features are taken directly from the EOD feature CSV (already computed).
    """
    # Merge Block A from EOD features
    block_a_cols = [
        'gap_pct', 'gap_dir', 'gap_size_pct60',
        'prev_day_return', 'prev_day_range',
        'prev_day_clv', 'prev_day_slope', 'prev_day_vol_pct',
    ]
    avail = [c for c in block_a_cols if c in eod_df.columns]
    df = df.join(eod_df[avail], how='left')

    # Rolling percentile rank of partial_realized_vol (20d trailing)
    df['partial_vol_pct20'] = _rolling_pct_rank(df['partial_realized_vol'], window=20)
    # Rolling percentile rank of partial_range_pct (20d trailing)
    df['partial_range_pct20'] = _rolling_pct_rank(df['partial_range_pct'], window=20)

    return df


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build intraday partial feature sets")
    parser.add_argument('--symbol', default='NSE_INDEX|Nifty 50')
    parser.add_argument('--start',  default='2023-01-01')
    parser.add_argument('--end',    default='2026-02-25')
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start)
    end_date   = date.fromisoformat(args.end)
    symbol     = args.symbol

    print("=" * 60)
    print("INTRADAY PARTIAL FEATURE BUILDER")
    print("=" * 60)
    print(f"Symbol:     {symbol}")
    print(f"Date range: {start_date} -> {end_date}")
    print(f"Checkpoints: {list(CHECKPOINTS.keys())}")

    # Load EOD features (all years)
    print("\n[1] Loading EOD features + cluster labels...")
    eod_dfs = []
    for year in [2023, 2024, 2025, 2026]:
        p = FEATURE_DIR / f"nifty_day_features_{year}.csv"
        if p.exists():
            eod_dfs.append(pd.read_csv(p, index_col='date', parse_dates=True))
    if not eod_dfs:
        raise FileNotFoundError("No EOD feature CSVs found")
    eod_df = pd.concat(eod_dfs).sort_index()

    labels_df = pd.read_csv(FEATURE_DIR / "cluster_labels.csv", index_col='date', parse_dates=True)
    eod_df = eod_df.join(labels_df[['cluster_id']], how='left')
    print(f"    EOD rows: {len(eod_df)}, cluster labels joined: {eod_df['cluster_id'].notna().sum()}")

    # Enumerate all trading days
    all_days = sorted(
        d for d in (start_date + timedelta(n) for n in range((end_date - start_date).days + 1))
        if (CANDLE_DIR_1M / f"{d.isoformat()}.duckdb").exists()
    )
    print(f"    Trading day files found: {len(all_days)}")

    # ── Build per-checkpoint feature sets ─────────────────────────────────────
    results = {cp: {} for cp in CHECKPOINTS}
    skipped = {cp: 0 for cp in CHECKPOINTS}

    for cp_name, cp_bar in CHECKPOINTS.items():
        print(f"\n[2] Building features at checkpoint: {cp_name} (bar {cp_bar})...")
        for d in all_days:
            d_ts = pd.Timestamp(d)   # always compare Timestamp → Timestamp
            if d_ts not in eod_df.index:
                skipped[cp_name] += 1
                continue
            if pd.isna(eod_df.loc[d_ts, 'cluster_id']):
                skipped[cp_name] += 1
                continue

            df_trunc = load_session_truncated(d, symbol, cp_bar)
            if df_trunc is None:
                skipped[cp_name] += 1
                continue

            try:
                feats = compute_checkpoint_features(df_trunc, cp_bar)
            except Exception as exc:
                print(f"    WARN: {d} failed at {cp_name}: {exc}")
                skipped[cp_name] += 1
                continue

            # Block H: BankNifty intermarket features (graceful if BN data absent)
            df_bn_trunc = load_session_truncated(d, BN_SYMBOL, cp_bar)
            feats.update(compute_block_h_intermarket(df_trunc, df_bn_trunc, cp_bar))

            feats['cluster_id'] = int(eod_df.loc[d_ts, 'cluster_id'])
            results[cp_name][d] = feats

        n_ok = len(results[cp_name])
        print(f"    Collected: {n_ok} days, skipped: {skipped[cp_name]}")

    # ── Save outputs ──────────────────────────────────────────────────────────
    print("\n[3] Adding rolling context features and saving...")
    FEATURE_DIR.mkdir(parents=True, exist_ok=True)

    for cp_name in CHECKPOINTS:
        if not results[cp_name]:
            print(f"    {cp_name}: no data, skipping")
            continue

        df_cp = pd.DataFrame.from_dict(results[cp_name], orient='index')
        df_cp.index.name = 'date'
        df_cp = df_cp.sort_index()

        # Add block A + rolling percentile context
        df_cp = add_rolling_context(df_cp, eod_df)

        out_path = FEATURE_DIR / f"intraday_features_{cp_name}.csv"
        df_cp.to_csv(out_path)
        print(f"    Saved {cp_name}: {out_path} ({len(df_cp)} rows, {df_cp.shape[1]} cols)")

        # Quick audit: feature count by category
        partial_cols = [c for c in df_cp.columns if c.startswith('partial_')]
        open_cols    = [c for c in df_cp.columns if c.startswith('open_')]
        context_cols = [c for c in df_cp.columns if c.startswith(('gap_', 'prev_day_'))]
        bn_cols      = [c for c in df_cp.columns if c.startswith('bn_')]
        print(f"      partial_* features: {len(partial_cols)}")
        print(f"      open_*   features: {len(open_cols)}")
        print(f"      context  features: {len(context_cols)}")
        print(f"      bn_*     features: {len(bn_cols)}  (Block H intermarket)")
        print(f"      label column: cluster_id")
        na_pct = df_cp.isna().mean().max()
        print(f"      max NaN fraction:  {na_pct:.1%}")

    print("\n" + "=" * 60)
    print("DONE. Intraday features ready for train_daytype_classifier.py")
    print("=" * 60)


if __name__ == '__main__':
    main()
