import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
import joblib

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "features" / "day_type"

def main():
    # 1. Load all years
    dfs = []
    for year in [2023, 2024, 2025, 2026]:
        p = DATA_DIR / f"stocks_fast_{year}.csv"
        if p.exists():
            dfs.append(pd.read_csv(p))
    
    df = pd.concat(dfs).reset_index(drop=True)
    print(f"Total Observations: {len(df)}")

    # 2. Features for Clustering (Full Day Ground Truth)
    # We want to find BullTrend, BearTrend, Choppy
    cluster_cols = ['clv', 'linreg_r2', 'day_range']
    X = df[cluster_cols].values
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # 3. K-Means (k=3)
    km = KMeans(n_clusters=3, random_state=42, n_init=10)
    df['cluster'] = km.fit_predict(X_scaled)
    
    # 4. Label Assignment
    # We need to map clusters to Bull (High CLV), Bear (Low CLV), Choppy (Low R2)
    centroids = df.groupby('cluster')[cluster_cols].mean()
    print("\nCluster Centroids:")
    print(centroids)
    
    # Assign names
    bull_cluster = centroids['clv'].idxmax()
    bear_cluster = centroids['clv'].idxmin()
    choppy_cluster = [i for i in range(3) if i not in [bull_cluster, bear_cluster]][0]
    
    mapping = {bull_cluster: "BullTrend", bear_cluster: "BearTrend", choppy_cluster: "Choppy"}
    df['day_type'] = df['cluster'].map(mapping)
    
    # 5. Save Labels
    df.to_csv(DATA_DIR / "stocks_universal_labels.csv", index=False)
    print(f"\nSaved universal labels to {DATA_DIR / 'stocks_universal_labels.csv'}")
    
    # Save Model for future use
    model_dir = ROOT / "models" / "daytype" / "universal_stocks"
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(km, model_dir / "kmeans_3.joblib")
    joblib.dump(scaler, model_dir / "scaler.joblib")

if __name__ == "__main__":
    main()
