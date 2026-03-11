"""
Research Script: Enhanced Stock Day-Type Classifier at 9:30 AM
============================================================
Tests if adding Relative Strength and VWAP proximity improves 
accuracy at the early 9:30 AM (15-min) checkpoint.

Features:
  - e_ret, e_range, e_close_loc (Original)
  - rel_strength (vs Nifty 50)
  - dist_vwap (Normalized distance from VWAP)

Output: models/daytype/stock_930am_enhanced/
"""

import sys
import warnings
from pathlib import Path
import duckdb
import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings('ignore')

ROOT       = Path(__file__).resolve().parent.parent
CANDLE_DIR = ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
LABEL_CSV  = ROOT / "data" / "features" / "day_type" / "stocks_universal_labels.csv"
MODEL_DIR  = ROOT / "models" / "daytype" / "stock_930am_enhanced"

CHECKPOINT_BARS = 15          # 9:15 to 9:30 (15 bars)
FEATURE_NAMES   = ["e_ret", "e_range", "e_close_loc", "rel_strength", "dist_vwap"]
CLUSTER_NAMES   = {0: "BearTrend", 1: "BullTrend", 2: "Choppy"}
NF_SYMBOL       = 'NSE_INDEX|Nifty 50'

def compute_enhanced_features(bars: pd.DataFrame, nf_bars: pd.DataFrame) -> dict | None:
    if len(bars) < CHECKPOINT_BARS or len(nf_bars) < CHECKPOINT_BARS:
        return None

    sub = bars.iloc[:CHECKPOINT_BARS]
    nf_sub = nf_bars.iloc[:CHECKPOINT_BARS]
    
    # 1. Price Structure
    s_open = float(sub["open"].iloc[0])
    s_curr = float(sub["close"].iloc[-1])
    s_high = float(sub["high"].max())
    s_low  = float(sub["low"].min())
    
    if s_open == 0: return None
    
    e_ret = (s_curr - s_open) / s_open
    e_range = (s_high - s_low) / s_open
    hl_span = s_high - s_low
    e_close_loc = (s_curr - s_low) / hl_span if hl_span > 1e-9 else 0.5
    
    # 2. Relative Strength
    nf_open = float(nf_sub["open"].iloc[0])
    nf_curr = float(nf_sub["close"].iloc[-1])
    nf_ret = (nf_curr - nf_open) / nf_open if nf_open != 0 else 0
    rel_strength = e_ret - nf_ret
    
    # 3. VWAP
    # VWAP = sum(Price * Vol) / sum(Vol)
    sub = sub.copy()
    sub['pv'] = sub['close'] * sub['volume']
    cum_pv = sub['pv'].sum()
    cum_v  = sub['volume'].sum()
    vwap = cum_pv / cum_v if cum_v > 0 else s_curr
    dist_vwap = (s_curr - vwap) / s_curr
    
    return {
        "e_ret": e_ret, 
        "e_range": e_range, 
        "e_close_loc": e_close_loc,
        "rel_strength": rel_strength,
        "dist_vwap": dist_vwap
    }

def extract_data(symbols: list[str]):
    db_files = sorted(CANDLE_DIR.glob("*.duckdb"))
    # Speed up research: only process 2025 and 2026 data
    db_files = [f for f in db_files if "2025" in f.stem or "2026" in f.stem]
    if not db_files:
        print("No 2025-2026 files found, using all available files...")
        db_files = sorted(CANDLE_DIR.glob("*.duckdb"))

    rows = []
    
    # Filter symbols to keep it fast for research
    all_syms = symbols + [NF_SYMBOL]
    sym_placeholder = ",".join(["?"] * len(all_syms))
    query = f"SELECT symbol, timestamp, open, high, low, close, volume FROM candles WHERE symbol IN ({sym_placeholder}) ORDER BY timestamp"

    for db_path in db_files:
        try:
            con = duckdb.connect(str(db_path), read_only=True)
            df  = con.execute(query, all_syms).df()
            con.close()
        except: continue
        
        if df.empty: continue
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        
        # Separate Nifty
        nf_df = df[df["symbol"] == NF_SYMBOL].sort_values("timestamp").reset_index(drop=True)
        if nf_df.empty: continue
        
        # Process stocks
        for sym, grp in df[df["symbol"] != NF_SYMBOL].groupby("symbol"):
            grp = grp.sort_values("timestamp").reset_index(drop=True)
            feats = compute_enhanced_features(grp, nf_df)
            if feats:
                feats["date"] = db_path.stem
                feats["symbol"] = sym
                rows.append(feats)
                
    return pd.DataFrame(rows)

def train_and_eval(train_thru=2025):
    print(f"Loading labels from {LABEL_CSV.name}...")
    labels = pd.read_csv(LABEL_CSV)
    labels["date"] = pd.to_datetime(labels["date"])
    symbols = labels["symbol"].unique().tolist()
    
    print(f"Extracting enhanced 9:30 AM features for {len(symbols)} symbols...")
    feat_df = extract_data(symbols)
    feat_df["date"] = pd.to_datetime(feat_df["date"])
    
    merged = feat_df.merge(labels[["date", "symbol", "cluster", "day_type"]], on=["date", "symbol"])
    merged = merged.dropna()
    
    train_df = merged[merged["date"].dt.year <= train_thru]
    test_df  = merged[merged["date"].dt.year > train_thru]
    
    print(f"Split: Train={len(train_df)}, Test={len(test_df)}")
    
    X_train = train_df[FEATURE_NAMES].values
    y_train = train_df["cluster"].values
    X_test  = test_df[FEATURE_NAMES].values
    y_test  = test_df["cluster"].values
    
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s  = scaler.transform(X_test)
    
    model = LogisticRegression(class_weight="balanced", random_state=42)
    model.fit(X_train_s, y_train)
    
    y_pred = model.predict(X_test_s)
    print("\nENHANCED 9:30 AM PERFORMANCE:")
    print(f"Accuracy: {accuracy_score(y_test, y_pred):.2%}")
    print(classification_report(y_test, y_pred, target_names=["Bear", "Bull", "Chop"]))
    
    # Save artifacts
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_DIR / "model.joblib")
    joblib.dump(scaler, MODEL_DIR / "scaler.joblib")
    joblib.dump(FEATURE_NAMES, MODEL_DIR / "features.joblib")
    print(f"\nArtifacts saved to {MODEL_DIR}")

if __name__ == "__main__":
    train_and_eval()
