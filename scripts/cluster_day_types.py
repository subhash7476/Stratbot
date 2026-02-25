"""
Day-Type Clustering Pipeline
============================
Transforms the 746-day feature matrix into a stable market day taxonomy.

Pipeline:
  1. Load CSVs from data/features/day_type/
  2. Prune degenerate / redundant columns
  3. Drop NaN rows (rolling warmup)
  4. Winsorize heavy-tailed features (1%/99%)
  5. StandardScaler (z-score all)
  6. PCA (retain 87% cumulative variance)
  7. KMeans k=3..8 with 4 diagnostics
  8. Stability test (10 seeds + subperiod centroid similarity)
  9. Post-cluster diagnostics (centroid profile + ANOVA)
  10. Save outputs to data/features/day_type/

Usage:
    python scripts/cluster_day_types.py
    python scripts/cluster_day_types.py --pca-threshold 0.90
    python scripts/cluster_day_types.py --k 5          # force specific k
    python scripts/cluster_day_types.py --downweight-vol 0.8
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats.mstats import winsorize
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist
from scipy.stats import f_oneway
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import (
    adjusted_rand_score,
    calinski_harabasz_score,
    davies_bouldin_score,
    silhouette_score,
)
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR   = ROOT / "data" / "features" / "day_type"
OUTPUT_DIR = DATA_DIR   # same location


# ── Column definitions ────────────────────────────────────────────────────────

DROP_COLS = [
    # G-block: all zero (volume=0 for index)
    'open_30m_vol_ratio', 'total_vol_pct20', 'first_hour_vol_pct',
    'vol_skew_ampm', 'vol_acceleration', 'vol_wtd_momentum',
    # Perfect redundancy
    'pct_min_below_twap',      # corr=1.000 with pct_min_above_twap
    # Near-duplicate volatility
    'realized_vol',            # corr=0.988 with intraday_atr_5m
    # TWAP-distance redundancy
    'open_30m_twap_dist',      # corr=0.93-0.97 with open_30m_ret
    # Near-constant on index
    'overlap_ratio_15m',       # std=0.008, mean=0.999 — no discriminative power
    'median_body_pct_15m',     # std=0.0004 — near zero everywhere
    # Metadata
    'symbol', '_source', 'vwap_mode', 'is_twap_proxy',
    'total_bars', 'missing_pct', 'data_quality_score',
]

WINSORIZE_COLS = [
    'log_vol_expansion',  # kurt=316, skew=14
    'intraday_atr_5m',    # kurt=71
    'day_range_pct',      # kurt=57
    'prev_day_range',     # kurt=57
]

VOLATILITY_FEATURES = [
    'intraday_atr_5m', 'day_range_pct', 'log_vol_expansion',
    'range_pct_before_11am', 'range_pct_after_130pm',
]

DIAGNOSTIC_FEATURES = [
    'day_range_pct', 'linreg_r2', 'clv',
    'pct_min_above_twap', 'flip_count_15m',
    'center_of_mass_return_time', 'range_pct_before_11am', 'range_pct_after_130pm',
    'gap_pct', 'avg_adverse_excursion', 'intraday_atr_5m',
    'hh_count_15m', 'll_count_15m', 'twap_cross_count',
]


# ── Data loading ──────────────────────────────────────────────────────────────

def load_features() -> pd.DataFrame:
    dfs = []
    for year in [2023, 2024, 2025, 2026]:
        path = DATA_DIR / f"nifty_day_features_{year}.csv"
        if path.exists():
            df = pd.read_csv(path, index_col='date', parse_dates=True)
            dfs.append(df)
    if not dfs:
        raise FileNotFoundError(f"No feature CSVs found in {DATA_DIR}")
    return pd.concat(dfs).sort_index()


# ── Pre-processing ────────────────────────────────────────────────────────────

def prune_columns(df: pd.DataFrame) -> pd.DataFrame:
    drop = [c for c in DROP_COLS if c in df.columns]
    df = df.drop(columns=drop)
    # Also drop any remaining zero-std columns
    num = df.select_dtypes(include='number')
    zero_std = [c for c in num.columns if num[c].std() < 1e-6]
    df = df.drop(columns=[c for c in zero_std if c in df.columns])
    return df


def apply_winsorization(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in WINSORIZE_COLS:
        if col in df.columns:
            df[col] = winsorize(df[col].values, limits=[0.01, 0.01])
    return df


def prepare_feature_matrix(df: pd.DataFrame, vol_weight: float = 1.0) -> tuple[np.ndarray, list[str], pd.DataFrame]:
    """
    Returns (X_scaled, feature_names, df_clean).
    df_clean has NaN rows dropped and is aligned with X_scaled.
    """
    # Keep only numeric columns, preserve DatetimeIndex
    df_num = df.select_dtypes(include='number')
    df_num = df_num.dropna()
    # Ensure DatetimeIndex is preserved
    if not isinstance(df_num.index, pd.DatetimeIndex):
        df_num.index = pd.to_datetime(df_num.index)
    feature_names = list(df_num.columns)

    # Optional volatility downweight
    if vol_weight != 1.0:
        for col in VOLATILITY_FEATURES:
            if col in df_num.columns:
                df_num[col] = df_num[col] * vol_weight

    X = df_num.values
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    return X_scaled, feature_names, df_num, scaler


def apply_pca(X_scaled: np.ndarray, threshold: float = 0.87) -> tuple[np.ndarray, PCA, int]:
    pca = PCA()
    X_pca_full = pca.fit_transform(X_scaled)
    cumvar = pca.explained_variance_ratio_.cumsum()
    n_components = int(np.argmax(cumvar >= threshold)) + 1
    X_pca = X_pca_full[:, :n_components]
    return X_pca, pca, n_components


# ── Volatility dominance check ────────────────────────────────────────────────

def check_volatility_dominance(pca: PCA, feature_names: list[str], n_components: int = 5) -> float:
    """
    Returns fraction of total variance in first n_components PCs attributable
    to volatility-family features.
    """
    loadings = np.abs(pca.components_[:n_components])    # shape (n_components, n_features)
    evr = pca.explained_variance_ratio_[:n_components]

    vol_indices = [i for i, f in enumerate(feature_names) if f in VOLATILITY_FEATURES]
    if not vol_indices:
        return 0.0

    vol_contribution = 0.0
    total = 0.0
    for comp_idx in range(n_components):
        comp_var = evr[comp_idx]
        # Fraction of this component's loadings attributed to volatility features
        all_load_sq = loadings[comp_idx] ** 2
        vol_load_sq = all_load_sq[vol_indices].sum()
        frac = vol_load_sq / (all_load_sq.sum() + 1e-10)
        vol_contribution += comp_var * frac
        total += comp_var

    return vol_contribution / (total + 1e-10)


# ── k-selection ───────────────────────────────────────────────────────────────

def select_k(X_pca: np.ndarray, k_range=range(3, 9)) -> dict:
    results = {}
    print(f"\n{'k':>3}  {'Sil':>7}  {'CH':>8}  {'DB':>7}  {'MinClust':>9}")
    print("-" * 45)
    for k in k_range:
        km = KMeans(n_clusters=k, n_init=20, random_state=42).fit(X_pca)
        labels = km.labels_
        sil = silhouette_score(X_pca, labels)
        ch  = calinski_harabasz_score(X_pca, labels)
        db  = davies_bouldin_score(X_pca, labels)
        min_pct = np.bincount(labels).min() / len(labels)
        results[k] = {'sil': sil, 'ch': ch, 'db': db, 'min_pct': min_pct,
                      'labels': labels, 'model': km}
        print(f"{k:>3}  {sil:>7.4f}  {ch:>8.1f}  {db:>7.4f}  {min_pct:>8.1%}")
    return results


def auto_select_k(results: dict) -> int:
    """
    Pick k: highest silhouette among k values where no cluster < 5%.
    Prefer middle of a stable plateau if metrics don't strongly differentiate.
    """
    candidates = {k: v for k, v in results.items() if v['min_pct'] >= 0.05}
    if not candidates:
        # Relax to 3% if all fail
        candidates = {k: v for k, v in results.items() if v['min_pct'] >= 0.03}
    if not candidates:
        candidates = results

    best_k = max(candidates, key=lambda k: candidates[k]['sil'])
    return best_k


# ── Stability tests ───────────────────────────────────────────────────────────

def seed_stability(X_pca: np.ndarray, k: int, n_seeds: int = 10) -> float:
    labels_list = []
    for seed in range(n_seeds):
        km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(X_pca)
        labels_list.append(km.labels_)
    ari_scores = [adjusted_rand_score(labels_list[0], labels_list[i]) for i in range(1, n_seeds)]
    return float(np.mean(ari_scores))


def hungarian_match(centroids_a: np.ndarray, centroids_b: np.ndarray) -> float:
    """
    Compute mean cosine similarity between best-matched centroid pairs
    using Hungarian algorithm.
    """
    # Cosine distance matrix
    dist_matrix = cdist(centroids_a, centroids_b, metric='cosine')
    row_ind, col_ind = linear_sum_assignment(dist_matrix)
    matched_sim = 1.0 - dist_matrix[row_ind, col_ind]
    return float(matched_sim.mean())


def subperiod_stability(X_pca: np.ndarray, df_clean: pd.DataFrame, k: int) -> float:
    """
    Fit KMeans on early (2023-24) and late (2025-26) subperiods separately.
    Compute centroid similarity via Hungarian matching.
    """
    early_mask = (df_clean.index.year <= 2024)
    late_mask  = (df_clean.index.year >= 2025)

    if early_mask.sum() < k or late_mask.sum() < k:
        return np.nan

    km_early = KMeans(n_clusters=k, n_init=20, random_state=42).fit(X_pca[early_mask])
    km_late  = KMeans(n_clusters=k, n_init=20, random_state=42).fit(X_pca[late_mask])
    return hungarian_match(km_early.cluster_centers_, km_late.cluster_centers_)


# ── Post-cluster diagnostics ──────────────────────────────────────────────────

def cluster_profile(df_clean: pd.DataFrame, labels: np.ndarray, feature_names: list[str]) -> pd.DataFrame:
    df_out = df_clean.copy()
    df_out['cluster'] = labels

    diag_cols = [c for c in DIAGNOSTIC_FEATURES if c in df_out.columns]
    profile = df_out.groupby('cluster')[diag_cols].mean()

    # Year distribution
    year_dist = df_out.groupby(['cluster', df_out.index.year]).size().unstack(fill_value=0)
    year_pct  = year_dist.div(year_dist.sum(axis=1), axis=0)

    return profile, year_dist, year_pct, df_out


def anova_influence(df_clean: pd.DataFrame, labels: np.ndarray, feature_names: list[str]) -> pd.Series:
    df_out = df_clean.copy()
    df_out['cluster'] = labels
    k = len(np.unique(labels))

    f_stats = {}
    for col in feature_names:
        if col in df_out.columns:
            groups = [df_out.loc[df_out.cluster == c, col].dropna().values for c in range(k)]
            groups = [g for g in groups if len(g) > 1]
            if len(groups) >= 2:
                F, _ = f_oneway(*groups)
                f_stats[col] = F if not np.isnan(F) else 0.0

    return pd.Series(f_stats).sort_values(ascending=False)


def centroid_interpretation(profile: pd.DataFrame, global_means: pd.Series, global_stds: pd.Series) -> pd.DataFrame:
    """Return z-score of each cluster centroid relative to global distribution."""
    return (profile - global_means) / (global_stds + 1e-10)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Day-type clustering pipeline")
    parser.add_argument('--pca-threshold', type=float, default=0.87,
                        help='Cumulative variance threshold for PCA (default: 0.87)')
    parser.add_argument('--k', type=int, default=None,
                        help='Force specific k (default: auto-select)')
    parser.add_argument('--downweight-vol', type=float, default=1.0,
                        help='Multiply volatility features by this before scaling (default: 1.0)')
    args = parser.parse_args()

    print("=" * 60)
    print("DAY-TYPE CLUSTERING PIPELINE")
    print("=" * 60)

    # ── Load ──────────────────────────────────────────────────────
    print("\n[1] Loading feature CSVs...")
    df_raw = load_features()
    print(f"    Loaded: {len(df_raw)} rows, {df_raw.shape[1]} columns")

    # ── Prune ─────────────────────────────────────────────────────
    print("\n[2] Pruning degenerate/redundant columns...")
    df_pruned = prune_columns(df_raw)
    print(f"    Remaining: {df_pruned.shape[1]} columns after pruning")

    # ── Winsorize ─────────────────────────────────────────────────
    print("\n[3] Winsorizing heavy-tailed features...")
    df_wins = apply_winsorization(df_pruned)
    for col in WINSORIZE_COLS:
        if col in df_wins.columns:
            orig_max = df_pruned[col].max()
            new_max  = df_wins[col].max()
            print(f"    {col}: max {orig_max:.4f} -> {new_max:.4f}")

    # ── Scale + Prepare ───────────────────────────────────────────
    print("\n[4] StandardScaler (z-score all features)...")
    vol_weight = args.downweight_vol
    X_scaled, feature_names, df_clean, scaler = prepare_feature_matrix(df_wins, vol_weight)
    print(f"    Features: {len(feature_names)}, Rows: {X_scaled.shape[0]}")
    if vol_weight != 1.0:
        print(f"    Volatility features downweighted by {vol_weight}x")

    # ── PCA ───────────────────────────────────────────────────────
    print(f"\n[5] PCA (threshold: {args.pca_threshold:.0%} cumulative variance)...")
    X_pca, pca, n_components = apply_pca(X_scaled, args.pca_threshold)
    cumvar = pca.explained_variance_ratio_.cumsum()
    print(f"    Components retained: {n_components} ({cumvar[n_components-1]:.1%} variance)")

    # PCA variance by component
    print("\n    Top feature loadings per first 5 PCs:")
    for pc_i in range(min(5, n_components)):
        top_idx = np.argsort(np.abs(pca.components_[pc_i]))[-3:][::-1]
        top_feats = [(feature_names[i], pca.components_[pc_i][i]) for i in top_idx]
        feats_str = ', '.join(f"{f}({v:+.2f})" for f, v in top_feats)
        print(f"    PC{pc_i+1} ({pca.explained_variance_ratio_[pc_i]:.1%}): {feats_str}")

    # Volatility dominance check
    vol_dom = check_volatility_dominance(pca, feature_names, n_components=5)
    print(f"\n    Volatility family contribution to first 5 PCs: {vol_dom:.1%}")
    if vol_dom > 0.40:
        print("    !! WARNING: Volatility dominates (>40%). Consider --downweight-vol 0.8")
    else:
        print("    OK: Volatility contribution within acceptable range (<40%)")

    # Save PCA variance table
    pca_df = pd.DataFrame({
        'pc': range(1, len(pca.explained_variance_ratio_) + 1),
        'explained_var': pca.explained_variance_ratio_,
        'cumulative_var': cumvar,
    })
    pca_path = OUTPUT_DIR / "pca_variance.csv"
    pca_df.to_csv(pca_path, index=False)
    print(f"\n    PCA variance saved to {pca_path}")

    # ── k-Selection ───────────────────────────────────────────────
    print("\n[6] k-Selection (k=3..8):")
    k_results = select_k(X_pca)

    if args.k:
        k_best = args.k
        print(f"\n    k forced to {k_best}")
    else:
        k_best = auto_select_k(k_results)
        print(f"\n    Auto-selected k={k_best} (highest silhouette with min_cluster >= 5%)")

    labels = k_results[k_best]['labels']

    # ── Stability Tests ───────────────────────────────────────────
    print(f"\n[7] Stability Tests (k={k_best}):")
    ari_mean = seed_stability(X_pca, k_best)
    print(f"    Seed stability (mean ARI over 10 seeds): {ari_mean:.3f}", end="")
    print(" [PASS]" if ari_mean >= 0.60 else " [WEAK - consider simpler k]")

    subp_sim = subperiod_stability(X_pca, df_clean, k_best)
    if not np.isnan(subp_sim):
        print(f"    Subperiod centroid similarity (2023-24 vs 2025-26): {subp_sim:.3f}", end="")
        print(" [STABLE]" if subp_sim >= 0.70 else " [DRIFTING]")
    else:
        print("    Subperiod stability: insufficient data in one period")

    # ── Post-cluster Profile ──────────────────────────────────────
    print(f"\n[8] Cluster Profile (k={k_best}):")
    profile, year_dist, year_pct, df_labeled = cluster_profile(df_clean, labels, feature_names)

    cluster_sizes = np.bincount(labels)
    for c in range(k_best):
        pct = cluster_sizes[c] / len(labels)
        print(f"    Cluster {c}: {cluster_sizes[c]} days ({pct:.1%})")

    print("\n    Year distribution per cluster (% of cluster):")
    print(year_pct.to_string())

    print("\n    Cluster centroids (key diagnostic features):")
    diag_in_profile = [c for c in DIAGNOSTIC_FEATURES if c in profile.columns]
    print(profile[diag_in_profile].T.to_string())

    # ── ANOVA ─────────────────────────────────────────────────────
    print(f"\n[9] ANOVA influence (top 10 most discriminative features):")
    f_series = anova_influence(df_labeled, labels, feature_names)
    median_f = float(f_series.median())
    print(f"    Median F: {median_f:.1f}")
    print(f"    Top 10:")
    for feat, F in f_series.head(10).items():
        flag = "  !! check weight" if feat == 'center_of_mass_return_time' and F > 2 * median_f else ""
        print(f"      {feat}: F={F:.1f}{flag}")

    com_f = f_series.get('center_of_mass_return_time', 0)
    if com_f > 2 * median_f:
        print(f"\n    !! center_of_mass_return_time F={com_f:.1f} > 2x median ({2*median_f:.1f})")
        print("       Consider re-running with halved weight: add feature weighting and re-cluster")

    # ── Z-score Centroid Interpretation ──────────────────────────
    global_means = df_clean[diag_in_profile].mean()
    global_stds  = df_clean[diag_in_profile].std()
    centroid_z   = centroid_interpretation(profile[diag_in_profile], global_means, global_stds)
    print(f"\n    Centroid z-scores vs global mean (for label assignment):")
    print(centroid_z.T.to_string())

    # ── Save Outputs ──────────────────────────────────────────────
    print("\n[10] Saving outputs...")

    # Cluster labels CSV
    labels_df = pd.DataFrame({'cluster_id': labels, 'cluster_label': ''},
                             index=df_clean.index)
    labels_path = OUTPUT_DIR / "cluster_labels.csv"
    labels_df.to_csv(labels_path)
    print(f"     Labels saved:    {labels_path}")

    # Centroid CSV (original scale — profile is already in original scale)
    centroid_path = OUTPUT_DIR / "cluster_centroids.csv"
    profile.to_csv(centroid_path)
    print(f"     Centroids saved: {centroid_path}")

    # Summary text
    summary_lines = [
        "=" * 60,
        "CLUSTER SUMMARY",
        "=" * 60,
        f"Total rows: {len(df_clean)}",
        f"Features used: {len(feature_names)}",
        f"PCA components: {n_components} ({cumvar[n_components-1]:.1%} variance)",
        f"Selected k: {k_best}",
        f"Silhouette: {k_results[k_best]['sil']:.4f}",
        f"Davies-Bouldin: {k_results[k_best]['db']:.4f}",
        f"Calinski-Harabasz: {k_results[k_best]['ch']:.1f}",
        f"Min cluster size: {k_results[k_best]['min_pct']:.1%}",
        f"Seed stability (ARI): {ari_mean:.3f}",
        "",
        "Cluster sizes:",
    ]
    for c in range(k_best):
        summary_lines.append(f"  Cluster {c}: {cluster_sizes[c]} days ({cluster_sizes[c]/len(labels):.1%})")

    summary_lines += [
        "",
        "k selection scan:",
        f"  {'k':>3}  {'Sil':>7}  {'CH':>8}  {'DB':>7}  {'MinClust':>9}",
    ]
    for k, v in k_results.items():
        summary_lines.append(
            f"  {k:>3}  {v['sil']:>7.4f}  {v['ch']:>8.1f}  {v['db']:>7.4f}  {v['min_pct']:>8.1%}"
        )

    summary_lines += [
        "",
        "Top 15 ANOVA features (F-statistic):",
    ]
    for feat, F in f_series.head(15).items():
        summary_lines.append(f"  {feat}: {F:.1f}")

    summary_path = OUTPUT_DIR / "cluster_summary.txt"
    with open(summary_path, 'w') as f:
        f.write('\n'.join(summary_lines))
    print(f"     Summary saved:   {summary_path}")

    print("\n" + "=" * 60)
    print("DONE. Bring cluster_summary.txt + centroids to auditor for label assignment.")
    print("=" * 60)


if __name__ == '__main__':
    main()
