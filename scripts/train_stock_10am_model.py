"""
Train Stock Day-Type Classifier at 10:00 AM Checkpoint
=======================================================
Retrains the same 3-feature logistic regression model used by
stock_daytype_paper.py, but computed at bar 45 (~9:59 AM close /
10:00 AM signal) instead of bar 225 (13:00 PM).

Features (identical formula, earlier window):
  e_ret       = (close[44] - open[0]) / open[0]
  e_range     = (max_high[:45] - min_low[:45]) / open[0]
  e_close_loc = (close[44] - min_low[:45]) / (max_high[:45] - min_low[:45])

Target: day-type cluster from stocks_universal_labels.csv
  0 = BearTrend | 1 = BullTrend | 2 = Choppy

Output: models/daytype/stock_1000am/
  model.joblib   — sklearn LogisticRegression
  scaler.joblib  — StandardScaler (fit on train)
  features.joblib — list of feature names

Usage:
  python scripts/train_stock_10am_model.py
  python scripts/train_stock_10am_model.py --train-thru 2025
"""

from __future__ import annotations

import argparse
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
MODEL_DIR  = ROOT / "models" / "daytype" / "stock_1000am"

CHECKPOINT_BARS = 45          # bars to use (indices 0-44, 9:15-9:59)
FEATURE_NAMES   = ["e_ret", "e_range", "e_close_loc"]
CLUSTER_NAMES   = {0: "BearTrend", 1: "BullTrend", 2: "Choppy"}


# ── Feature computation ───────────────────────────────────────────────────────

def compute_10am_features(bars: pd.DataFrame) -> dict | None:
    """
    Compute 3 features from the first CHECKPOINT_BARS 1m bars.
    bars: DataFrame with columns [timestamp, open, high, low, close], sorted asc.
    Returns None if insufficient data.
    """
    if len(bars) < CHECKPOINT_BARS:
        return None

    sub        = bars.iloc[:CHECKPOINT_BARS]
    session_open = float(sub["open"].iloc[0])
    last_close   = float(sub["close"].iloc[-1])     # close of bar 44 (~9:59/10:00)
    max_h        = float(sub["high"].max())
    min_l        = float(sub["low"].min())

    if session_open == 0:
        return None

    e_ret       = (last_close - session_open) / session_open
    e_range     = (max_h - min_l) / session_open
    hl_span     = max_h - min_l
    e_close_loc = (last_close - min_l) / hl_span if hl_span > 1e-9 else 0.5

    return {"e_ret": e_ret, "e_range": e_range, "e_close_loc": e_close_loc}


# ── Data extraction ───────────────────────────────────────────────────────────

def extract_features_from_candles(symbols: list[str]) -> pd.DataFrame:
    """
    Walk every available 1m DuckDB file and compute 10am features per symbol/date.
    """
    db_files = sorted(CANDLE_DIR.glob("*.duckdb"))
    if not db_files:
        raise FileNotFoundError(f"No 1m candle DB files found in {CANDLE_DIR}")

    print(f"  Scanning {len(db_files)} DuckDB files ({db_files[0].stem} to {db_files[-1].stem})")
    sym_placeholder = ",".join(["?"] * len(symbols))
    query = (
        f"SELECT symbol, timestamp, open, high, low, close "
        f"FROM candles WHERE symbol IN ({sym_placeholder}) ORDER BY timestamp"
    )

    rows = []
    for db_path in db_files:
        date_str = db_path.stem
        try:
            con = duckdb.connect(str(db_path), read_only=True)
            df  = con.execute(query, symbols).df()
            con.close()
        except Exception as exc:
            continue

        if df.empty:
            continue

        df["timestamp"] = pd.to_datetime(df["timestamp"])

        for sym, grp in df.groupby("symbol"):
            grp = grp.sort_values("timestamp").reset_index(drop=True)
            feats = compute_10am_features(grp)
            if feats is None:
                continue
            feats["date"]   = date_str
            feats["symbol"] = sym
            rows.append(feats)

    if not rows:
        raise ValueError("No valid feature rows extracted. Check candle data.")

    feat_df = pd.DataFrame(rows)
    feat_df["date"] = pd.to_datetime(feat_df["date"])
    return feat_df


# ── Training ──────────────────────────────────────────────────────────────────

def train(train_thru: int = 2024) -> None:
    print("=" * 60)
    print("STOCK 10:00 AM DAY-TYPE MODEL TRAINER")
    print("=" * 60)

    # ── Load labels ───────────────────────────────────────────────────────────
    print("\n[1/5] Loading cluster labels...")
    if not LABEL_CSV.exists():
        raise FileNotFoundError(f"Labels CSV not found: {LABEL_CSV}")

    labels = pd.read_csv(LABEL_CSV, usecols=["date", "symbol", "cluster", "day_type"])
    labels["date"] = pd.to_datetime(labels["date"])
    symbols = labels["symbol"].unique().tolist()
    print(f"  {len(labels):,} label rows | {len(symbols)} symbols | "
          f"date range: {labels['date'].min().date()} to {labels['date'].max().date()}")

    # ── Extract 10am features ─────────────────────────────────────────────────
    print("\n[2/5] Extracting 10:00 AM features from 1m candles...")
    feat_df = extract_features_from_candles(symbols)
    print(f"  Extracted {len(feat_df):,} rows from {feat_df['date'].nunique()} dates")

    # ── Join features with labels ─────────────────────────────────────────────
    print("\n[3/5] Joining features with cluster labels...")
    merged = feat_df.merge(labels[["date", "symbol", "cluster", "day_type"]],
                           on=["date", "symbol"], how="inner")
    merged = merged.dropna(subset=["cluster"] + FEATURE_NAMES)
    merged["cluster"] = merged["cluster"].astype(int)
    print(f"  Merged: {len(merged):,} rows")

    class_counts = merged["day_type"].value_counts()
    print(f"  Class balance:\n{class_counts.to_string()}")

    # ── Train / Val / Holdout split ───────────────────────────────────────────
    train_df = merged[merged["date"].dt.year <= train_thru]
    val_df   = merged[merged["date"].dt.year == train_thru + 1]
    hold_df  = merged[merged["date"].dt.year >= train_thru + 2]
    print(f"\n[4/5] Splits: Train={len(train_df):,}  Val={len(val_df):,}  Hold={len(hold_df):,}")
    print(f"       (train_thru={train_thru}, val={train_thru+1}, hold={train_thru+2}+)")

    if len(train_df) < 50:
        raise ValueError(
            f"Too few training rows ({len(train_df)}). "
            f"Check that 1m candle data covers dates up to {train_thru}."
        )

    X_train = train_df[FEATURE_NAMES].values
    y_train = train_df["cluster"].values
    X_val   = val_df[FEATURE_NAMES].values   if len(val_df)  > 0 else np.empty((0, 3))
    y_val   = val_df["cluster"].values       if len(val_df)  > 0 else np.array([])
    X_hold  = hold_df[FEATURE_NAMES].values  if len(hold_df) > 0 else np.empty((0, 3))
    y_hold  = hold_df["cluster"].values      if len(hold_df) > 0 else np.array([])

    # ── Fit scaler on train only ──────────────────────────────────────────────
    scaler    = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_val_s   = scaler.transform(X_val)  if len(X_val)  > 0 else X_val
    X_hold_s  = scaler.transform(X_hold) if len(X_hold) > 0 else X_hold

    # ── Train logistic regression ─────────────────────────────────────────────
    model = LogisticRegression(
        solver="lbfgs",
        C=1.0,
        max_iter=1000,
        random_state=42,
        class_weight="balanced",
    )
    model.fit(X_train_s, y_train)

    # ── Evaluate ──────────────────────────────────────────────────────────────
    print("\n--- Accuracy by split ---")
    for label, X_s, y_s in [
        ("Train", X_train_s, y_train),
        ("Val",   X_val_s,   y_val),
        ("Hold",  X_hold_s,  y_hold),
    ]:
        if len(y_s) == 0:
            print(f"  [{label}]  no data")
            continue

        y_pred    = model.predict(X_s)
        acc       = accuracy_score(y_s, y_pred)
        y_proba   = model.predict_proba(X_s)
        max_p     = y_proba.max(axis=1)
        high_pct  = (max_p >= 0.70).mean()
        med_pct   = ((max_p >= 0.55) & (max_p < 0.70)).mean()

        # Per-class accuracy
        per_cls = {}
        for cls, name in CLUSTER_NAMES.items():
            mask = y_s == cls
            if mask.sum() > 0:
                per_cls[name] = accuracy_score(y_s[mask], y_pred[mask])

        print(f"  [{label}]  n={len(y_s):,}  overall={acc:.1%}  "
              f"high_conf={high_pct:.1%}  med_conf={med_pct:.1%}")
        for name, a in per_cls.items():
            print(f"           {name}: {a:.1%}")

    # ── Print logistic coefficients ───────────────────────────────────────────
    print("\n  Top coefficients per class:")
    for i, cls_id in enumerate(model.classes_):
        name = CLUSTER_NAMES.get(cls_id, str(cls_id))
        coef = model.coef_[i]
        pairs = sorted(zip(FEATURE_NAMES, coef), key=lambda x: abs(x[1]), reverse=True)
        terms = ", ".join(f"{f}({v:+.3f})" for f, v in pairs)
        print(f"    {name}: {terms}")

    # ── Save artifacts ────────────────────────────────────────────────────────
    print(f"\n[5/5] Saving to {MODEL_DIR}/")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model,         MODEL_DIR / "model.joblib")
    joblib.dump(scaler,        MODEL_DIR / "scaler.joblib")
    joblib.dump(FEATURE_NAMES, MODEL_DIR / "features.joblib")
    print("  Saved: model.joblib, scaler.joblib, features.joblib")

    print("\n" + "=" * 60)
    print("DONE. Next: restart unified_runner.py to apply new model.")
    print("  CHECKPOINT_BARS = 45  →  entry ~10:01 AM")
    print("  Model dir: models/daytype/stock_1000am/")
    print("=" * 60)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Train stock day-type classifier at 10:00 AM checkpoint"
    )
    parser.add_argument(
        "--train-thru", type=int, default=2024,
        help="Last year in training set (default: 2024). "
             "Val = train_thru+1, Holdout = train_thru+2+."
    )
    args = parser.parse_args()
    train(train_thru=args.train_thru)


if __name__ == "__main__":
    main()
