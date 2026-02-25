import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
import joblib

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "features" / "day_type" / "stocks_universal_labels.csv"

def main():
    df = pd.read_csv(DATA_PATH)
    df['date'] = pd.to_datetime(df['date'])
    
    # 1. Split Train (2023-2025) and Test (2026)
    train_df = df[df['date'].dt.year <= 2025].dropna()
    test_df = df[df['date'].dt.year == 2026].dropna()
    
    X_cols = ['c_ret', 'c_range', 'c_close_loc']
    y_col = 'day_type'
    
    X_train = train_df[X_cols]
    y_train = train_df[y_col]
    X_test = test_df[X_cols]
    y_test = test_df[y_col]
    
    print(f"Training on {len(X_train)} samples...")
    
    # 2. Train Model (Logistic Regression)
    model = LogisticRegression(max_iter=1000)
    model.fit(X_train, y_train)
    
    # 3. Evaluate
    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    
    print("\nMODEL PERFORMANCE (9:30 AM Checkpoint):")
    print(f"Accuracy: {acc:.2%}")
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred))
    
    # 4. Save Model
    model_dir = ROOT / "models" / "daytype" / "stock_930am"
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_dir / "model.joblib")
    joblib.dump(X_cols, model_dir / "features.joblib")
    
    # Save test results for backtest
    test_df['pred_label'] = y_pred
    test_df.to_csv(ROOT / "data" / "features" / "day_type" / "stock_930am_test_results.csv", index=False)

if __name__ == "__main__":
    main()
