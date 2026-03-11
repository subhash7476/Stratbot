"""
Breadth-Type Clustering Pipeline
================================
Transforms the Nifty 50 breadth feature matrix into a stable market regime taxonomy.

Pipeline:
  1. Load breadth CSVs from data/features/day_type/
  2. Drop NaN rows
  3. StandardScaler (z-score all)
  4. PCA (retain 90% cumulative variance)
  5. KMeans k=3..8 with diagnostics
  6. Stability test (10 seeds + subperiod centroid similarity)
  7. Save outputs to data/features/day_type/
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
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

DATA_DIR = ROOT / "data" / "features" / "day_type"
OUTPUT_DIR = DATA_DIR

# ── Feature definition ────────────────────────────────────────────────────────

BREADTH_FEATURES = [
    'pct_positive', 'pct_above_vwap', 'adv_dec_ratio', 'median_return',
    'cross_sectional_std', 'pct_breaking_open_high', 'pct_breaking_open_low',
    'avg_return_top10', 'avg_return_bottom10', 'cross_sectional_skew'
]

# ── Data loading ──────────────────────────────────────────────────────────────

def load_breadth_features() -> pd.DataFrame:
    dfs = []
    for year in [2023, 2024, 2025, 2026]:
        path = DATA_DIR / f"nifty50_breadth_{year}.csv"
        if path.exists():
            df = pd.read_csv(path, index_col='date', parse_dates=True)
            dfs.append(df)
    if not dfs:
        raise FileNotFoundError(f"No breadth feature CSVs found in {DATA_DIR}")
    return pd.concat(dfs).sort_index()

# ── Pre-processing ────────────────────────────────────────────────────────────

def prepare_feature_matrix(df: pd.DataFrame) -> tuple[np.ndarray, list[str], pd.DataFrame, StandardScaler]:
    # Use only the specified breadth features
    available_features = [f for f in BREADTH_FEATURES if f in df.columns]
    df_num = df[available_features].dropna()
    
    # Handle infinities if any (e.g. from adv_dec_ratio)
    df_num = df_num.replace([np.inf, -np.inf], np.nan).dropna()
    
    feature_names = list(df_num.columns)
    X = df_num.values
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    return X_scaled, feature_names, df_num, scaler

def apply_pca(X_scaled: np.ndarray, threshold: float = 0.90) -> tuple[np.ndarray, PCA, int]:
    pca = PCA()
    X_pca_full = pca.fit_transform(X_scaled)
    cumvar = pca.explained_variance_ratio_.cumsum()
    n_components = int(np.argmax(cumvar >= threshold)) + 1
    X_pca = X_pca_full[:, :n_components]
    return X_pca, pca, n_components

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
    candidates = {k: v for k, v in results.items() if v['min_pct'] >= 0.05}
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
    dist_matrix = cdist(centroids_a, centroids_b, metric='cosine')
    row_ind, col_ind = linear_sum_assignment(dist_matrix)
    matched_sim = 1.0 - dist_matrix[row_ind, col_ind]
    return float(matched_sim.mean())

def subperiod_stability(X_pca: np.ndarray, df_clean: pd.DataFrame, k: int) -> float:
    early_mask = (df_clean.index.year <= 2024)
    late_mask  = (df_clean.index.year >= 2025)
    if early_mask.sum() < k or late_mask.sum() < k:
        return np.nan
    km_early = KMeans(n_clusters=k, n_init=20, random_state=42).fit(X_pca[early_mask])
    km_late  = KMeans(n_clusters=k, n_init=20, random_state=42).fit(X_pca[late_mask])
    return hungarian_match(km_early.cluster_centers_, km_late.cluster_centers_)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Breadth-type clustering pipeline")
    parser.add_argument('--pca-threshold', type=float, default=0.90)
    parser.add_argument('--k', type=int, default=None)
    args = parser.parse_args()

    print("=" * 60)
    print("BREADTH-TYPE CLUSTERING PIPELINE")
    print("=" * 60)

    # 1. Load
    df_raw = load_breadth_features()
    print(f"\n[1] Loaded: {len(df_raw)} rows")

    # 2. Prepare
    X_scaled, feature_names, df_clean, scaler = prepare_feature_matrix(df_raw)
    print(f"[2] Features: {len(feature_names)}, Rows: {X_scaled.shape[0]}")

    # 3. PCA
    X_pca, pca, n_components = apply_pca(X_scaled, args.pca_threshold)
    print(f"[3] PCA Components: {n_components} ({pca.explained_variance_ratio_.cumsum()[n_components-1]:.1%} variance)")

    # 4. k-Selection
    k_results = select_k(X_pca)
    k_best = args.k if args.k else auto_select_k(k_results)
    print(f"\n[4] Selected k={k_best}")

    # 5. Stability
    ari_mean = seed_stability(X_pca, k_best)
    subp_sim = subperiod_stability(X_pca, df_clean, k_best)
    print(f"[5] Seed ARI: {ari_mean:.3f}")
    print(f"    Subperiod Similarity: {subp_sim:.3f}")

    # 6. Save
    labels = k_results[k_best]['labels']
    labels_df = pd.DataFrame({'breadth_cluster': labels}, index=df_clean.index)
    labels_df.to_csv(OUTPUT_DIR / "breadth_cluster_labels.csv")
    
    profile = df_clean.copy()
    profile['breadth_cluster'] = labels
    centroids = profile.groupby('breadth_cluster').mean()
    centroids.to_csv(OUTPUT_DIR / "breadth_cluster_centroids.csv")

    # Summary
    summary = [
        f"Rows: {len(df_clean)}",
        f"k: {k_best}",
        f"ARI: {ari_mean:.3f}",
        f"Subperiod Sim: {subp_sim:.3f}",
        f"PCA components: {n_components}"
    ]
    with open(OUTPUT_DIR / "breadth_cluster_summary.txt", 'w') as f:
        f.write('\n'.join(summary))
    
    print(f"\n[6] Outputs saved to {OUTPUT_DIR}")
    print("=" * 60)

if __name__ == '__main__':
    main()
