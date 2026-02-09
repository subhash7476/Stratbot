import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import classification_report, precision_score
import os
import numpy as np

def train_pixityAI_meta_model(data_path: str, model_save_path: str):
    """
    Trains a Meta-Model with TimeSeriesSplit.
    Optimizes for Expected Value (Realized R).
    """
    if not os.path.exists(data_path):
        print(f"Data not found at {data_path}")
        return

    df = pd.read_csv(data_path)
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp')
    
    features = ["vwap_dist", "ema_slope", "atr_pct", "adx", "hour", "minute", "vol_z"]
    # Ensure all features exist in DF
    features = [f for f in features if f in df.columns]
    
    df['target'] = (df['label'] == 1).astype(int)
    
    X = df[features]
    y = df['target']
    
    tscv = TimeSeriesSplit(n_splits=5)
    
    best_model = None
    best_score = -np.inf
    
    for train_index, test_index in tscv.split(X):
        X_train, X_test = X.iloc[train_index], X.iloc[test_index]
        y_train, y_test = y.iloc[train_index], y.iloc[test_index]
        
        model = RandomForestClassifier(n_estimators=100, max_depth=7, random_state=42)
        model.fit(X_train, y_train)
        
        # Calculate Expected Value on Test Set
        probs = model.predict_proba(X_test)[:, 1]
        test_df = df.iloc[test_index].copy()
        test_df['prob'] = probs
        
        # Simulate taking trades > 0.6 prob
        trades = test_df[test_df['prob'] > 0.6]
        if len(trades) > 0:
            # Expected Value = Mean Realized R
            ev = trades['realized_R'].mean() if 'realized_R' in trades.columns else precision_score(y_test, model.predict(X_test))
            
            if ev > best_score:
                best_score = ev
                best_model = model

    if best_model:
        os.makedirs(os.path.dirname(model_save_path), exist_ok=True)
        joblib.dump(best_model, model_save_path)
        print(f"Best Model Saved. Score (test): {best_score:.4f}")
    else:
        # Fallback to simple training if best_model not found
        model = RandomForestClassifier(n_estimators=100, max_depth=7, random_state=42)
        model.fit(X, y)
        os.makedirs(os.path.dirname(model_save_path), exist_ok=True)
        joblib.dump(model, model_save_path)
        print("Model saved using fallback (all data).")

if __name__ == "__main__":
    print("PixityAI Trainer ready.")
