"""
Causal Stock Pipeline (Zero Look-ahead)
=======================================
1. Cluster 2023-2025 data to define regimes.
2. Train 9:30 AM Classifier on 2023-2025.
3. Predict 2026 regimes and trade performance (Out-of-Sample).
"""

import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
import joblib

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "features" / "day_type"

def main():
    # 1. Load Data
    dfs = []
    for year in [2023, 2024, 2025, 2026]:
        p = DATA_DIR / f"stocks_fast_{year}.csv"
        if p.exists():
            dfs.append(pd.read_csv(p))
    
    df_all = pd.concat(dfs).reset_index(drop=True)
    df_all['date'] = pd.to_datetime(df_all['date'])
    
    # 2. CAUSAL CLUSTERING (Train on 2023-2025 ONLY)
    train_mask = (df_all['date'].dt.year <= 2025)
    test_mask = (df_all['date'].dt.year == 2026)
    
    df_train = df_all[train_mask].copy()
    df_test = df_all[test_mask].copy()
    
    cluster_cols = ['clv', 'linreg_r2', 'day_range']
    X_cluster_train = df_train[cluster_cols].values
    
    scaler_cluster = StandardScaler()
    X_cluster_train_scaled = scaler_cluster.fit_transform(X_cluster_train)
    
    km = KMeans(n_clusters=3, random_state=42, n_init=10)
    df_train['cluster'] = km.fit_predict(X_cluster_train_scaled)
    
    # Map clusters to names using 2023-2025 centroids
    centroids = df_train.groupby('cluster')[cluster_cols].mean()
    bull_cluster = centroids['clv'].idxmax()
    bear_cluster = centroids['clv'].idxmin()
    choppy_cluster = [i for i in range(3) if i not in [bull_cluster, bear_cluster]][0]
    
    mapping = {bull_cluster: "BullTrend", bear_cluster: "BearTrend", choppy_cluster: "Choppy"}
    df_train['day_type'] = df_train['cluster'].map(mapping)
    
    # Apply clustering model to 2026 (Out-of-Sample Labels)
    X_cluster_test_scaled = scaler_cluster.transform(df_test[cluster_cols].values)
    df_test['cluster'] = km.predict(X_cluster_test_scaled)
    df_test['day_type'] = df_test['cluster'].map(mapping)
    
    print(f"Causal Clustering Complete. Train N={len(df_train)}, Test N={len(df_test)}")

    # 3. CAUSAL CLASSIFIER TRAINING (Train on 2023-2025 ONLY)
    # Using full context: Stock + Market + Gap
    X_cols = ['c_ret', 'c_range', 'c_close_loc', 'gap_pct', 'mkt_pct_pos_930', 'mkt_avg_ret_930']
    X_train = df_train[X_cols].fillna(0)
    y_train = df_train['day_type']
    
    from sklearn.ensemble import RandomForestClassifier
    classifier = RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42)
    classifier.fit(X_train, y_train)
    
    # 4. OUT-OF-SAMPLE EVALUATION (2026)
    X_test = df_test[X_cols].fillna(0)
    y_test = df_test['day_type']
    
    df_test['pred_label'] = classifier.predict(X_test)
    acc = accuracy_score(y_test, df_test['pred_label'])
    
    importances = pd.Series(classifier.feature_importances_, index=X_cols).sort_values(ascending=False)
    print("\nFeature Importances:")
    print(importances)
    
    print(f"\n9:30 AM Out-of-Sample Accuracy (2026): {acc:.2%}")
    
    # 5. BACKTEST (9:30 AM - 13:30 PM)
    cost = 0.0004
    df_test['trade_ret'] = 0.0
    
    # Long if predicted Bull
    bull_mask = (df_test['pred_label'] == "BullTrend")
    df_test.loc[bull_mask, 'trade_ret'] = df_test.loc[bull_mask, 'target_ret'] - cost
    
    # Short if predicted Bear
    bear_mask = (df_test['pred_label'] == "BearTrend")
    df_test.loc[bear_mask, 'trade_ret'] = -df_test.loc[bear_mask, 'target_ret'] - cost
    
    trades = df_test[df_test['pred_label'].isin(["BullTrend", "BearTrend"])]
    
    print("\n2026 H1 BACKTEST RESULTS (9:30 - 13:30):")
    print("-" * 60)
    if not trades.empty:
        mean_ret = trades['trade_ret'].mean()
        win_rate = (trades['trade_ret'] > 0).mean()
        t_stat = (mean_ret / (trades['trade_ret'].std() / np.sqrt(len(trades))))
        print(f"Total Trades: {len(trades)}")
        print(f"Mean Return:  {mean_ret:>+7.4%}")
        print(f"Win Rate:     {win_rate:>7.1%}")
        print(f"t-stat:       {t_stat:>7.2f}")
    else:
        print("No trades taken.")
    print("-" * 60)

if __name__ == "__main__":
    main()
