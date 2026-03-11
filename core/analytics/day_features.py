"""
Day-Type Feature Library
========================
Computes 53 engineered features per trading day from 1-minute Nifty 50 data.
Used for unsupervised day-type clustering.

Feature blocks:
  A: Gap & Previous Context     (8 features)
  B: Opening Structure          (9 features)
  C: Intraday Trend             (8 features)
  D: Volatility Structure       (9 features)
  E: VWAP/TWAP Microstructure   (7 features)
  F: Intraday Rotation          (7 features)
  G: Volume Structure           (5 features, always 0 for index)

All computations are causal (no lookahead). Percentile features use trailing
rolling windows only. CLV uses epsilon guard. Volume block is retained for
schema consistency but zeroed for index data.
"""

from __future__ import annotations

import math
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

# ── Constants ──────────────────────────────────────────────────────────────────

EPSILON = 1e-8          # General near-zero guard
CLV_THRESHOLD = 0.2     # |CLV| gate for adverse excursion features
AM_CUTOFF_BAR = 105     # Bar index for 11:00 AM (9:15 + 105 mins)
PM_START_BAR = 135      # Bar index for 13:30 (9:15 + 135 mins) — wait, 1:30 PM
# Actually: 9:15 + 135 = 150 mins → 11:45. Let's compute properly:
# 11:00 AM = 9:15 + 105 min → bar index 105 (0-indexed bars 0-374)
# 13:30 PM = 9:15 + 255 min → bar index 255
AM_END_BAR = 105        # 11:00 AM cutoff (exclusive)
PM_START_BAR = 255      # 13:30 PM cutoff (inclusive)
FIRST_HOUR_END_BAR = 60 # 10:15 AM cutoff (exclusive)
EXPECTED_BARS = 375     # 9:15 to 15:29 inclusive


def _range_epsilon(high: float, low: float, open_: float) -> float:
    """Safe range denominator — prevents CLV explosion on compression days."""
    return max(high - low, EPSILON * open_)


def _rolling_pct_rank(series: pd.Series, window: int) -> pd.Series:
    """
    Trailing percentile rank: rank of last value within trailing window,
    normalized by (window_size - 1). Range [0, 1]. No lookahead.
    """
    def rank_last(x):
        if len(x) < 2:
            return np.nan
        return (x[:-1] < x[-1]).sum() / (len(x) - 1)

    return series.rolling(window, min_periods=2).apply(rank_last, raw=True)


# ── Block A: Gap & Previous Context ───────────────────────────────────────────

def block_a_gap_context(row_idx: int, daily_df: pd.DataFrame) -> Dict[str, float]:
    """
    Compute gap & previous-day context features.
    Requires the full daily_df (all days processed so far) to look back.
    Called after all per-session features are collected.
    """
    if row_idx == 0:
        return {
            'gap_pct': np.nan,
            'gap_dir': np.nan,
            'gap_size_pct60': np.nan,
            'prev_day_return': np.nan,
            'prev_day_range': np.nan,
            'prev_day_clv': np.nan,
            'prev_day_slope': np.nan,
            'prev_day_vol_pct': np.nan,
        }

    cur = daily_df.iloc[row_idx]
    prv = daily_df.iloc[row_idx - 1]

    gap_pct = (cur['open'] - prv['close']) / prv['close'] * 100
    gap_dir = float(np.sign(gap_pct))

    prev_range_denom = max(prv['high'] - prv['low'], EPSILON * prv['open'])
    prev_day_range = (prv['high'] - prv['low']) / prv['open']
    prev_day_clv = (prv['close'] - prv['low'] - (prv['high'] - prv['close'])) / prev_range_denom
    prev_day_return = prv['close'] / prv['open'] - 1

    # Slope normalized by prev day range (scale-invariant)
    prev_day_slope = prv['linreg_slope_raw'] / prev_day_range if prev_day_range > EPSILON else 0.0

    return {
        'gap_pct': gap_pct,
        'gap_dir': gap_dir,
        'gap_size_pct60': np.nan,       # filled via rolling after all rows built
        'prev_day_return': prev_day_return,
        'prev_day_range': prev_day_range,
        'prev_day_clv': prev_day_clv,
        'prev_day_slope': prev_day_slope,
        'prev_day_vol_pct': np.nan,     # filled via rolling after all rows built
    }


def fill_block_a_rolling(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill trailing-window percentile features in Block A after all rows are built.
    Must be called on the complete DataFrame (all days).
    """
    df = df.copy()
    df['gap_size_pct60'] = _rolling_pct_rank(df['gap_pct'].abs(), window=60)
    df['prev_day_vol_pct'] = _rolling_pct_rank(df['realized_vol'], window=20)
    return df


# ── Block B: Opening Structure ─────────────────────────────────────────────────

def block_b_opening_structure(df1m: pd.DataFrame, twap: pd.Series,
                               full_day_range: float) -> Dict[str, float]:
    """
    Opening structure features from first 5/15/30 1m bars.
    full_day_range is passed in to compute open_30m_range_ratio safely.
    """
    open_price = df1m['open'].iloc[0]

    # First N bars (0-indexed)
    bar5  = df1m.iloc[4] if len(df1m) > 4 else df1m.iloc[-1]
    bar15 = df1m.iloc[14] if len(df1m) > 14 else df1m.iloc[-1]
    bar30 = df1m.iloc[29] if len(df1m) > 29 else df1m.iloc[-1]

    open_5m_ret  = (bar5['close']  - open_price) / open_price
    open_15m_ret = (bar15['close'] - open_price) / open_price
    open_30m_ret = (bar30['close'] - open_price) / open_price

    # First 30 bars range
    first30 = df1m.iloc[:30]
    open_30m_high = first30['high'].max()
    open_30m_low  = first30['low'].min()
    open_30m_range = (open_30m_high - open_30m_low) / open_price

    # Range ratio with epsilon guard
    range_denom = max(full_day_range, EPSILON * open_price)
    open_30m_range_ratio = (open_30m_high - open_30m_low) / range_denom

    # Time first 30m high/low broken (minutes_since_open, 0=bar0 = 9:15)
    close_series = df1m['close']
    high_breaks = np.where(close_series.values > open_30m_high)[0]
    low_breaks  = np.where(close_series.values < open_30m_low)[0]
    open_30m_high_break_min = int(high_breaks[0]) if len(high_breaks) > 0 else -1
    open_30m_low_break_min  = int(low_breaks[0])  if len(low_breaks) > 0 else -1

    # TWAP distance at bar 30
    twap_at_30 = twap.iloc[29] if len(twap) > 29 else twap.iloc[-1]
    open_30m_twap_dist = (twap_at_30 - open_price) / open_price * 100

    # Volume ratio (always 0.0 for index)
    total_vol = df1m['volume'].sum()
    open_30m_vol_ratio = (first30['volume'].sum() / total_vol) if total_vol > 0 else 0.0

    return {
        'open_5m_ret': open_5m_ret,
        'open_15m_ret': open_15m_ret,
        'open_30m_ret': open_30m_ret,
        'open_30m_range': open_30m_range,
        'open_30m_range_ratio': open_30m_range_ratio,
        'open_30m_high_break_min': open_30m_high_break_min,
        'open_30m_low_break_min': open_30m_low_break_min,
        'open_30m_twap_dist': open_30m_twap_dist,
        'open_30m_vol_ratio': open_30m_vol_ratio,
    }


# ── Block C: Intraday Trend ────────────────────────────────────────────────────

def block_c_intraday_trend(df1m: pd.DataFrame, df15m: pd.DataFrame,
                            twap: pd.Series) -> Dict[str, float]:
    """Intraday trend metrics."""
    open_price = df1m['open'].iloc[0]
    session_high = df1m['high'].max()
    session_low  = df1m['low'].min()
    session_close = df1m['close'].iloc[-1]

    full_day_return = (session_close - open_price) / open_price
    day_range_pct   = (session_high - session_low) / open_price
    day_range       = session_high - session_low  # raw, for normalization

    # CLV at session level with epsilon guard
    re = _range_epsilon(session_high, session_low, open_price)
    clv = (session_close - session_low - (session_high - session_close)) / re

    # Linear regression on 1m close series
    n = len(df1m)
    x = np.arange(n)
    y = df1m['close'].values
    if n >= 2:
        slope, intercept = np.polyfit(x, y, 1)
        y_pred = slope * x + intercept
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        linreg_r2    = 1.0 - ss_res / ss_tot if ss_tot > EPSILON else 0.0
        linreg_slope = slope / open_price          # normalized by open price
        linreg_slope_raw = slope                   # stored for Block A prev_day_slope
    else:
        linreg_r2 = 0.0
        linreg_slope = 0.0
        linreg_slope_raw = 0.0

    # Higher highs / lower lows in 15m bars (normalized by bar count)
    n15 = len(df15m)
    if n15 >= 2:
        hh_count = int((df15m['high'].values[1:] > df15m['high'].values[:-1]).sum())
        ll_count = int((df15m['low'].values[1:] < df15m['low'].values[:-1]).sum())
        hh_count_15m = hh_count / (n15 - 1)
        ll_count_15m = ll_count / (n15 - 1)
    else:
        hh_count_15m = 0.0
        ll_count_15m = 0.0

    # Max TWAP excursion normalized by day_range (shape-normalized)
    twap_dist = (df1m['close'] - twap).abs()
    range_denom = max(day_range, EPSILON * open_price)
    max_twap_excursion = twap_dist.max() / range_denom

    return {
        'full_day_return': full_day_return,
        'day_range_pct': day_range_pct,
        'day_range': day_range,           # internal, not a clustering feature
        'clv': clv,
        'linreg_slope': linreg_slope,
        'linreg_slope_raw': linreg_slope_raw,  # internal use for block A
        'linreg_r2': linreg_r2,
        'hh_count_15m': hh_count_15m,
        'll_count_15m': ll_count_15m,
        'max_twap_excursion': max_twap_excursion,
    }


# ── Block D: Volatility Structure ─────────────────────────────────────────────

def block_d_volatility_structure(df1m: pd.DataFrame, df5m: pd.DataFrame,
                                  day_range: float) -> Dict[str, float]:
    """Volatility structure features."""
    open_price = df1m['open'].iloc[0]
    ret1m = df1m['close'].pct_change().dropna()

    # D1: Realized vol (raw daily std, no annualization)
    realized_vol = float(ret1m.std()) if len(ret1m) >= 2 else 0.0

    # D2: Intraday ATR from 5m bars
    if len(df5m) >= 2:
        hi = df5m['high'].values
        lo = df5m['low'].values
        cl = df5m['close'].values
        tr1 = hi - lo
        tr2 = np.abs(hi[1:] - cl[:-1])
        tr3 = np.abs(lo[1:] - cl[:-1])
        tr  = np.concatenate([[tr1[0]], np.maximum(tr1[1:], np.maximum(tr2, tr3))])
        intraday_atr_5m = float(tr.mean()) / open_price
    else:
        intraday_atr_5m = 0.0

    # D3: range_pct_vs20d — filled via rolling after all rows built
    range_pct_vs20d = np.nan

    # D4: Range formed before 11am (bar index < 105) and D5: after 1:30pm (bar >= 255)
    range_denom = max(day_range, EPSILON * open_price)
    session_high = df1m['high'].max()
    session_low  = df1m['low'].min()

    am_bars = df1m.iloc[:AM_END_BAR]  # 9:15–10:59
    pm_bars = df1m.iloc[PM_START_BAR:]  # 13:30–15:29

    am_high = am_bars['high'].max() if len(am_bars) > 0 else session_high
    am_low  = am_bars['low'].min()  if len(am_bars) > 0 else session_low
    pm_high = pm_bars['high'].max() if len(pm_bars) > 0 else session_high
    pm_low  = pm_bars['low'].min()  if len(pm_bars) > 0 else session_low

    range_pct_before_11am = (am_high - am_low) / range_denom
    range_pct_after_130pm = (pm_high - pm_low) / range_denom

    # D6: Largest single 5m candle relative to day_range (shape-normalized)
    if len(df5m) > 0:
        five_m_ranges = (df5m['high'] - df5m['low']).values
        largest_5m_candle = float(five_m_ranges.max()) / range_denom
    else:
        largest_5m_candle = 0.0

    # D7: Log vol expansion ratio (AM vs PM volatility)
    am_ret = df1m['close'].iloc[:AM_END_BAR].pct_change().dropna()
    pm_ret = df1m['close'].iloc[AM_END_BAR:].pct_change().dropna()
    std_am = float(am_ret.std()) if len(am_ret) >= 2 else EPSILON
    std_pm = float(pm_ret.std()) if len(pm_ret) >= 2 else EPSILON
    log_vol_expansion = math.log(max(std_am, EPSILON) / max(std_pm, EPSILON))

    # D8: Volatility clustering — autocorrelation of |returns| at lag-1
    abs_ret = ret1m.abs()
    if len(abs_ret) >= 3:
        vol_clustering = float(abs_ret.autocorr(lag=1))
        vol_clustering = 0.0 if np.isnan(vol_clustering) else vol_clustering
    else:
        vol_clustering = 0.0

    # D9: Center of mass of returns (normalized [0,1], flat day → 0.5)
    n = len(df1m)
    indices = np.arange(n)
    abs_ret_full = df1m['close'].pct_change().abs().fillna(0).values
    sum_abs = abs_ret_full.sum()
    if sum_abs > EPSILON:
        com = float(np.dot(indices, abs_ret_full) / sum_abs) / (n - 1)
    else:
        com = 0.5
    center_of_mass_return_time = np.clip(com, 0.0, 1.0)

    return {
        'realized_vol': realized_vol,
        'intraday_atr_5m': intraday_atr_5m,
        'range_pct_vs20d': range_pct_vs20d,    # filled via rolling later
        'range_pct_before_11am': range_pct_before_11am,
        'range_pct_after_130pm': range_pct_after_130pm,
        'largest_5m_candle': largest_5m_candle,
        'log_vol_expansion': log_vol_expansion,
        'vol_clustering': vol_clustering,
        'center_of_mass_return_time': center_of_mass_return_time,
    }


def fill_block_d_rolling(df: pd.DataFrame) -> pd.DataFrame:
    """Fill trailing-window percentile features in Block D."""
    df = df.copy()
    df['range_pct_vs20d'] = _rolling_pct_rank(df['day_range_pct'], window=20)
    return df


# ── Block E: TWAP Microstructure ──────────────────────────────────────────────

def block_e_twap_microstructure(df1m: pd.DataFrame, twap: pd.Series) -> Dict[str, float]:
    """
    TWAP microstructure features. All index data uses TWAP (volume=0).
    vwap_mode is always 'twap' for NSE_INDEX|Nifty 50.
    """
    open_price = df1m['open'].iloc[0]
    close = df1m['close']
    diff = close - twap

    pct_above = float((diff > 0).mean())
    pct_below = float((diff < 0).mean())

    # Sign changes in (close - twap)
    signs = np.sign(diff.values)
    vwap_cross_count = int(np.sum(np.diff(signs) != 0))

    # Longest consecutive stretch above/below
    def longest_run(mask):
        if not mask.any():
            return 0
        runs, current = 0, 0
        for v in mask:
            if v:
                current += 1
                runs = max(runs, current)
            else:
                current = 0
        return runs

    longest_above = longest_run(diff.values > 0)
    longest_below = longest_run(diff.values < 0)

    close_dist_twap = (close.iloc[-1] - twap.iloc[-1]) / open_price * 100
    vwap_dist_std = float(diff.std()) if len(diff) >= 2 else 0.0

    return {
        'pct_min_above_twap': pct_above,
        'pct_min_below_twap': pct_below,
        'twap_cross_count': vwap_cross_count,
        'longest_above_twap': longest_above,
        'longest_below_twap': longest_below,
        'close_dist_twap': close_dist_twap,
        'twap_dist_std': vwap_dist_std,
    }


# ── Block F: Intraday Rotation ─────────────────────────────────────────────────

def block_f_rotation_metrics(df1m: pd.DataFrame, df15m: pd.DataFrame,
                              clv: float) -> Dict[str, float]:
    """Intraday rotation and choppiness metrics."""
    open_price = df1m['open'].iloc[0]
    session_high = df1m['high'].max()
    session_low  = df1m['low'].min()

    # F1: Direction flips in 15m returns
    ret15 = df15m['close'].pct_change().dropna()
    if len(ret15) >= 2:
        flip_count_15m = int((np.diff(np.sign(ret15.values)) != 0).sum())
    else:
        flip_count_15m = 0

    # F2: Inside bar fraction in 15m
    n15 = len(df15m)
    if n15 >= 2:
        inside = sum(
            1 for i in range(1, n15)
            if df15m['high'].iloc[i] <= df15m['high'].iloc[i - 1]
            and df15m['low'].iloc[i] >= df15m['low'].iloc[i - 1]
        )
        inside_bar_pct_15m = inside / (n15 - 1)
    else:
        inside_bar_pct_15m = 0.0

    # F3: Median body % in 15m
    bodies = (df15m['close'] - df15m['open']).abs() / open_price
    median_body_pct_15m = float(bodies.median()) if len(bodies) > 0 else 0.0

    # F4/F5: Adverse excursion (gated by |CLV| >= 0.2)
    dominant_direction_strength = abs(clv)
    if dominant_direction_strength >= CLV_THRESHOLD:
        if clv > 0:  # bullish day: measure excursions from high
            extreme = session_high
            excursions = (extreme - df15m['low']).clip(lower=0)
        else:         # bearish day: measure excursions from low
            extreme = session_low
            excursions = (df15m['high'] - extreme).clip(lower=0)
        range_norm = max(session_high - session_low, EPSILON * open_price)
        avg_adverse_excursion = float(excursions.mean()) / range_norm
        max_adverse_excursion = float(excursions.max()) / range_norm
    else:
        avg_adverse_excursion = 0.0
        max_adverse_excursion = 0.0

    # F6: Overlap ratio between consecutive 15m bars
    if n15 >= 2:
        overlaps = []
        for i in range(1, n15):
            hi_min = min(df15m['high'].iloc[i], df15m['high'].iloc[i - 1])
            lo_max = max(df15m['low'].iloc[i],  df15m['low'].iloc[i - 1])
            overlaps.append(1.0 if hi_min >= lo_max else 0.0)
        overlap_ratio_15m = float(np.mean(overlaps))
    else:
        overlap_ratio_15m = 0.0

    return {
        'flip_count_15m': flip_count_15m,
        'inside_bar_pct_15m': inside_bar_pct_15m,
        'median_body_pct_15m': median_body_pct_15m,
        'avg_adverse_excursion': avg_adverse_excursion,
        'max_adverse_excursion': max_adverse_excursion,
        'overlap_ratio_15m': overlap_ratio_15m,
        'dominant_direction_strength': dominant_direction_strength,
    }


# ── Block G: Volume Structure (always 0 for index) ────────────────────────────

def block_g_volume_structure(df1m: pd.DataFrame, df5m: pd.DataFrame) -> Dict[str, float]:
    """
    Volume structure features. Always 0 for NSE_INDEX|Nifty 50 (volume=0).
    Retained for schema consistency; hard-dropped before clustering.
    """
    total_vol = df1m['volume'].sum()
    if total_vol == 0:
        return {
            'total_vol_pct20': 0.0,
            'first_hour_vol_pct': 0.0,
            'vol_skew_ampm': 0.0,
            'vol_acceleration': 0.0,
            'vol_wtd_momentum': 0.0,
        }

    first_hour = df1m.iloc[:FIRST_HOUR_END_BAR]['volume'].sum()
    am_vol = df1m.iloc[:PM_START_BAR]['volume'].sum()
    pm_vol = df1m.iloc[PM_START_BAR:]['volume'].sum()

    ret1m = df1m['close'].pct_change().fillna(0)
    vol_wtd_mom = float((ret1m * df1m['volume']).sum() / total_vol)

    return {
        'total_vol_pct20': np.nan,       # filled via rolling after all rows built
        'first_hour_vol_pct': float(first_hour / total_vol),
        'vol_skew_ampm': float(am_vol / max(pm_vol, 1)),
        'vol_acceleration': float(pm_vol / max(am_vol, 1)),
        'vol_wtd_momentum': vol_wtd_mom,
    }


def fill_block_g_rolling(df: pd.DataFrame) -> pd.DataFrame:
    """Fill total_vol_pct20 if volume data exists (no-op for index)."""
    df = df.copy()
    if df['total_vol_pct20'].isna().all():
        df['total_vol_pct20'] = 0.0
    else:
        raw_vol = df['total_vol_raw']
        df['total_vol_pct20'] = _rolling_pct_rank(raw_vol, window=20)
    return df


# ── Session TWAP computation (volume=0 fallback) ──────────────────────────────

def compute_session_twap(df1m: pd.DataFrame) -> pd.Series:
    """
    Session TWAP: cumulative mean of HLC3.
    Used when volume=0 (Nifty index). Resets each session.
    """
    hlc3 = (df1m['high'] + df1m['low'] + df1m['close']) / 3
    total_vol = df1m['volume'].sum()

    if total_vol > 0:
        # True VWAP
        pv = hlc3 * df1m['volume']
        cum_pv  = pv.cumsum()
        cum_vol = df1m['volume'].cumsum()
        return cum_pv / cum_vol
    else:
        # TWAP fallback
        return hlc3.expanding().mean()


# ── Top-level per-day computation ─────────────────────────────────────────────

def compute_session_features(session_1m: pd.DataFrame) -> Dict[str, Any]:
    """
    Compute all per-session features for a single trading day.
    Returns a flat dict with all feature values + internal fields used for rolling.

    Block A gap features (requiring prev day context) are computed separately
    via fill_block_a_rolling() after all days are collected.
    """
    if session_1m.empty or len(session_1m) < 5:
        return {}

    # Ensure sorted by timestamp
    df = session_1m.copy()
    df = df.sort_values('timestamp').reset_index(drop=True)

    # Resample to 5m and 15m (local import to avoid circular)
    from core.analytics.resampler import resample_ohlcv
    df5m  = resample_ohlcv(df, '5m')
    df15m = resample_ohlcv(df, '15m')

    # Compute TWAP
    twap = compute_session_twap(df)

    open_price    = df['open'].iloc[0]
    session_high  = df['high'].max()
    session_low   = df['low'].min()
    full_day_range = session_high - session_low

    # Blocks B, C, D, E, F, G
    c = block_c_intraday_trend(df, df15m, twap)
    b = block_b_opening_structure(df, twap, full_day_range)
    d = block_d_volatility_structure(df, df5m, c['day_range'])
    e = block_e_twap_microstructure(df, twap)
    f = block_f_rotation_metrics(df, df15m, c['clv'])
    g = block_g_volume_structure(df, df5m)

    # Metadata
    expected = EXPECTED_BARS
    total_bars = len(df)
    missing_pct = max(0.0, (expected - total_bars) / expected)
    total_vol_raw = df['volume'].sum()

    features: Dict[str, Any] = {}
    features.update(b)
    features.update(c)
    features.update(d)
    features.update(e)
    features.update(f)
    features.update(g)

    # Session-level OHLC for Block A lookback
    features['open']          = open_price
    features['high']          = session_high
    features['low']           = session_low
    features['close']         = df['close'].iloc[-1]
    features['total_bars']    = total_bars
    features['missing_pct']   = missing_pct
    features['data_quality_score'] = 1.0 - missing_pct
    features['total_vol_raw'] = float(total_vol_raw)
    features['_source']       = 'index'
    features['vwap_mode']     = 'twap'
    features['is_twap_proxy'] = True

    return features


def finalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply all rolling/cross-day features after all sessions are processed.
    Removes internal-only columns not intended for clustering/output.
    """
    df = fill_block_a_rolling(df)
    df = fill_block_d_rolling(df)
    df = fill_block_g_rolling(df)

    # Drop internal-only columns
    internal_cols = ['linreg_slope_raw', 'day_range', 'total_vol_raw', 'open', 'high', 'low', 'close']
    df = df.drop(columns=[c for c in internal_cols if c in df.columns])

    return df
