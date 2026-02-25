"""
Day-Type Classifier Trainer
============================
Trains supervised models to predict end-of-day cluster label from partial
intraday features at 3 checkpoints (10am, 11am, 13pm).

Pipeline per checkpoint:
  1. Load intraday_features_{cp}.csv
  2. Drop rows with NaN target or NaN features
  3. Winsorize heavy-tailed predictors (1%/99%)
  4. StandardScaler
  5. Train/Val/Holdout split (2023-24 train, 2025 val, 2026 holdout)
  6. Logistic Regression (baseline — interpretable)
  7. LightGBM (if available — accuracy ceiling)
  8. Evaluate: accuracy, per-class accuracy, confidence calibration
  9. Save models + scaler to models/daytype/

Accuracy expectations (not targets — DO NOT overfit toward these):
  10:00 AM -> 55–65%
  11:00 AM -> 65–72%
  13:00 PM -> 75–85%

Usage:
  python scripts/train_daytype_classifier.py
  python scripts/train_daytype_classifier.py --checkpoint 11am
  python scripts/train_daytype_classifier.py --no-lgbm   # logistic only
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats.mstats import winsorize
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
)
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings('ignore')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FEATURE_DIR = ROOT / "data" / "features" / "day_type"
MODEL_DIR   = ROOT / "models" / "daytype"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

CHECKPOINTS = ['10am', '11am', '13pm']

# Block A features (gap / prev-day context).
# Excluding these produced the logistic_13pm_prod model (+1.2% val, +6.7% holdout).
# Use --no-block-a to reproduce that ablation.
BLOCK_A_FEATURES = [
    'gap_pct', 'gap_dir', 'gap_size_pct60',
    'prev_day_return', 'prev_day_range', 'prev_day_clv', 'prev_day_slope',
]

# Features to winsorize at 1%/99% before scaling
WINSORIZE_COLS = [
    'partial_realized_vol',
    'partial_atr_5m',
    'partial_range_pct',
    'partial_log_vol_expansion',
    'open_30m_range',
    'prev_day_range',
]

# Columns to drop from feature matrix (metadata, target, leakers)
DROP_FROM_X = {
    'cluster_id',           # target label
    'cluster_label',        # text label if present
    'checkpoint_bar',       # constant within checkpoint
    # Block B ratio uses partial range — already captured in partial_range_pct
    # Keep open_30m_range_ratio_partial (it's a valid predictor)
}

# Cluster label names (for reporting)
CLUSTER_NAMES = {
    0: 'BearTrend',
    1: 'BullTrend',
    2: 'Choppy',
}


# ── Data loading + preprocessing ──────────────────────────────────────────────

def load_checkpoint_data(cp: str) -> pd.DataFrame:
    path = FEATURE_DIR / f"intraday_features_{cp}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing: {path}\n"
            f"Run: python scripts/build_intraday_features.py first"
        )
    df = pd.read_csv(path, index_col='date', parse_dates=True)
    return df.sort_index()


def split_data(df: pd.DataFrame,
               train_thru: int = 2024) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split by year: train=<= train_thru, val=train_thru+1, holdout>=train_thru+2."""
    train = df[df.index.year <= train_thru]
    val   = df[df.index.year == train_thru + 1]
    hold  = df[df.index.year >= train_thru + 2]
    return train, val, hold


def prepare_xy(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Extract X (features) and y (cluster_id) from checkpoint DataFrame."""
    y = df['cluster_id'].dropna().astype(int)
    df = df.loc[y.index]           # align
    drop_cols = [c for c in DROP_FROM_X if c in df.columns]
    X = df.drop(columns=drop_cols).select_dtypes(include='number')
    X = X.dropna()
    y = y.loc[X.index]
    return X, y


def apply_winsorization(X: pd.DataFrame) -> pd.DataFrame:
    if X.empty:
        return X.copy()
    X = X.copy()
    for col in WINSORIZE_COLS:
        if col in X.columns and len(X) >= 10:   # need at least 10 rows for 1% limits
            X[col] = winsorize(X[col].values, limits=[0.01, 0.01])
    return X


# ── Model training ────────────────────────────────────────────────────────────

def train_logistic(X_train: np.ndarray, y_train: np.ndarray) -> LogisticRegression:
    """
    Multinomial logistic regression — interpretable baseline.
    C=1.0 (default regularization), solver=lbfgs, max_iter=1000.
    """
    clf = LogisticRegression(
        solver='lbfgs',
        C=1.0,
        max_iter=1000,
        random_state=42,
        class_weight='balanced',
    )
    clf.fit(X_train, y_train)
    return clf


def train_lgbm(X_train: np.ndarray, y_train: np.ndarray,
               X_val: np.ndarray, y_val: np.ndarray):
    """LightGBM classifier — accuracy ceiling, optional."""
    try:
        import lightgbm as lgb
    except ImportError:
        return None

    model = lgb.LGBMClassifier(
        n_estimators=300,
        learning_rate=0.05,
        max_depth=4,
        num_leaves=15,
        min_child_samples=10,
        subsample=0.8,
        colsample_bytree=0.8,
        class_weight='balanced',
        random_state=42,
        verbose=-1,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(period=-1)],
    )
    return model


# ── Evaluation ────────────────────────────────────────────────────────────────

def evaluate(model, X: np.ndarray, y: np.ndarray, label: str,
             feature_names: list[str] | None = None) -> dict:
    """Full evaluation: accuracy, per-class, confusion, confidence."""
    y_pred  = model.predict(X)
    y_proba = model.predict_proba(X)  # shape (n, k)

    acc = accuracy_score(y, y_pred)
    per_class = {
        CLUSTER_NAMES.get(c, str(c)): accuracy_score(y[y == c], y_pred[y == c])
        for c in np.unique(y) if (y == c).sum() > 0
    }

    # Confidence distribution
    max_proba = y_proba.max(axis=1)
    high_conf   = (max_proba >= 0.70).mean()
    medium_conf = ((max_proba >= 0.55) & (max_proba < 0.70)).mean()
    low_conf    = (max_proba < 0.55).mean()

    # Accuracy conditional on confidence tier
    acc_high   = accuracy_score(y[max_proba >= 0.70], y_pred[max_proba >= 0.70]) \
                 if (max_proba >= 0.70).sum() > 0 else np.nan
    acc_medium = accuracy_score(
        y[(max_proba >= 0.55) & (max_proba < 0.70)],
        y_pred[(max_proba >= 0.55) & (max_proba < 0.70)]
    ) if ((max_proba >= 0.55) & (max_proba < 0.70)).sum() > 0 else np.nan

    result = {
        'split':         label,
        'n':             len(y),
        'accuracy':      round(acc, 4),
        'per_class':     {k: round(v, 4) for k, v in per_class.items()},
        'high_conf_pct': round(high_conf, 3),
        'med_conf_pct':  round(medium_conf, 3),
        'low_conf_pct':  round(low_conf, 3),
        'acc_high_conf': round(acc_high, 4) if not np.isnan(acc_high) else None,
        'acc_med_conf':  round(acc_medium, 4) if not np.isnan(acc_medium) else None,
    }
    return result


def print_evaluation(result: dict) -> None:
    print(f"  [{result['split']}] n={result['n']}")
    print(f"    Overall accuracy: {result['accuracy']:.1%}")
    for cls, acc in result['per_class'].items():
        print(f"      {cls}: {acc:.1%}")
    print(f"    Confidence distribution:")
    print(f"      High (>=0.70): {result['high_conf_pct']:.1%}  -> acc {result['acc_high_conf']:.1%}" if result['acc_high_conf'] else
          f"      High (>=0.70): {result['high_conf_pct']:.1%}")
    print(f"      Med  (0.55-0.70): {result['med_conf_pct']:.1%}  -> acc {result['acc_med_conf']:.1%}" if result['acc_med_conf'] else
          f"      Med  (0.55-0.70): {result['med_conf_pct']:.1%}")
    print(f"      Low  (<0.55): {result['low_conf_pct']:.1%}")


def print_logistic_coefficients(model: LogisticRegression,
                                  feature_names: list[str]) -> None:
    """Print top coefficient magnitudes per class for interpretability."""
    classes = model.classes_
    coef = model.coef_   # shape (n_classes, n_features)
    print("\n  Top logistic coefficients per class (abs magnitude):")
    for i, cls in enumerate(classes):
        cls_name = CLUSTER_NAMES.get(cls, str(cls))
        top_idx = np.argsort(np.abs(coef[i]))[-5:][::-1]
        pairs = [(feature_names[j], coef[i][j]) for j in top_idx]
        terms = ', '.join(f"{n}({v:+.3f})" for n, v in pairs)
        print(f"    {cls_name}: {terms}")


# ── Persistence ───────────────────────────────────────────────────────────────

def save_model_artifacts(cp: str, model_name: str, model, scaler: StandardScaler,
                         feature_names: list[str], results: list[dict],
                         override_dir_name: str | None = None,
                         block_a_excluded: bool = False,
                         train_thru: int = 2024,
                         version: str = 'v1.0') -> None:
    """Save model + scaler + metadata as numpy/json artifacts."""
    import pickle

    dir_name = override_dir_name if override_dir_name else f"{model_name}_{cp}"
    base = MODEL_DIR / dir_name
    base.mkdir(parents=True, exist_ok=True)

    # Save model
    with open(base / "model.pkl", 'wb') as f:
        pickle.dump(model, f)

    # Save scaler
    with open(base / "scaler.pkl", 'wb') as f:
        pickle.dump(scaler, f)

    # Save metadata
    meta = {
        'checkpoint':       cp,
        'model':            model_name,
        'feature_names':    feature_names,
        'cluster_names':    CLUSTER_NAMES,
        'results':          results,
        'block_a_excluded': block_a_excluded,
        'train_thru':       train_thru,
        'version':          version,
    }
    with open(base / "metadata.json", 'w') as f:
        json.dump(meta, f, indent=2)

    print(f"\n  Saved to: {base}/")


# ── Main ──────────────────────────────────────────────────────────────────────

def run_checkpoint(cp: str, use_lgbm: bool,
                   train_thru: int = 2024,
                   no_block_a: bool = False,
                   model_name_override: str | None = None) -> None:
    print(f"\n{'='*60}")
    print(f"CHECKPOINT: {cp}  (train_thru={train_thru}  no_block_a={no_block_a})")
    print(f"{'='*60}")

    # Load
    df = load_checkpoint_data(cp)
    train_df, val_df, hold_df = split_data(df, train_thru=train_thru)
    print(f"  Split — train: {len(train_df)}, val: {len(val_df)}, holdout: {len(hold_df)}")

    # Prepare X, y
    X_train_raw, y_train = prepare_xy(train_df)
    X_val_raw,   y_val   = prepare_xy(val_df)
    X_hold_raw,  y_hold  = prepare_xy(hold_df)

    # Optionally drop Block A features
    if no_block_a:
        drop_a = [c for c in BLOCK_A_FEATURES if c in X_train_raw.columns]
        if drop_a:
            print(f"  Dropping Block A features ({len(drop_a)}): {drop_a}")
        X_train_raw = X_train_raw.drop(columns=drop_a, errors='ignore')
        X_val_raw   = X_val_raw.drop(columns=drop_a, errors='ignore')
        X_hold_raw  = X_hold_raw.drop(columns=drop_a, errors='ignore')

    # Align feature columns (val/hold may have NaN after rolling warmup)
    feat_cols = X_train_raw.columns.tolist()
    X_val_raw  = X_val_raw.reindex(columns=feat_cols, fill_value=0.0)
    X_hold_raw = X_hold_raw.reindex(columns=feat_cols, fill_value=0.0)

    # Winsorize (fit on train only)
    X_train_w = apply_winsorization(X_train_raw)
    X_val_w   = apply_winsorization(X_val_raw)
    X_hold_w  = apply_winsorization(X_hold_raw)

    # Scale (fit on train only)
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train_w.values)
    X_val_s   = scaler.transform(X_val_w.values) if len(X_val_w) > 0 else np.empty((0, X_train_s.shape[1]))
    X_hold_s  = scaler.transform(X_hold_w.values) if len(X_hold_w) > 0 else np.empty((0, X_train_s.shape[1]))

    feature_names = feat_cols
    print(f"  Features: {len(feature_names)}")
    print(f"  Class distribution (train): {dict(zip(*np.unique(y_train, return_counts=True)))}")

    # Derive version string and model version
    version = f"v2.0-train_thru{train_thru}" if train_thru != 2024 else "v1.0"

    # ── Logistic Regression ───────────────────────────────────────────────────
    print(f"\n--- Logistic Regression ---")
    lr = train_logistic(X_train_s, y_train.values)

    lr_results = []
    for split_name, X_s, y_s in [
        ('Train', X_train_s, y_train),
        ('Val',   X_val_s,   y_val),
        ('Hold',  X_hold_s,  y_hold),
    ]:
        if len(y_s) == 0:
            continue
        res = evaluate(lr, X_s, y_s.values, split_name, feature_names)
        print_evaluation(res)
        lr_results.append(res)

    print_logistic_coefficients(lr, feature_names)

    lr_dir = model_name_override if model_name_override else None
    save_model_artifacts(cp, 'logistic', lr, scaler, feature_names, lr_results,
                         override_dir_name=lr_dir,
                         block_a_excluded=no_block_a,
                         train_thru=train_thru,
                         version=version)

    # ── LightGBM ──────────────────────────────────────────────────────────────
    if use_lgbm:
        print(f"\n--- LightGBM ---")
        lgbm_model = train_lgbm(X_train_s, y_train.values, X_val_s, y_val.values)
        if lgbm_model is None:
            print("  LightGBM not available (pip install lightgbm)")
        else:
            lgbm_results = []
            for split_name, X_s, y_s in [
                ('Train', X_train_s, y_train),
                ('Val',   X_val_s,   y_val),
                ('Hold',  X_hold_s,  y_hold),
            ]:
                if len(y_s) == 0:
                    continue
                res = evaluate(lgbm_model, X_s, y_s.values, split_name, feature_names)
                print_evaluation(res)
                lgbm_results.append(res)

            # Feature importances
            print("\n  Top 10 LightGBM feature importances:")
            fi = lgbm_model.feature_importances_
            top_idx = np.argsort(fi)[-10:][::-1]
            for j in top_idx:
                print(f"    {feature_names[j]}: {fi[j]:.0f}")

            lgbm_dir = (model_name_override.replace('logistic', 'lgbm')
                        if model_name_override else None)
            save_model_artifacts(cp, 'lgbm', lgbm_model, scaler, feature_names, lgbm_results,
                                 override_dir_name=lgbm_dir,
                                 block_a_excluded=no_block_a,
                                 train_thru=train_thru,
                                 version=version)


def main():
    parser = argparse.ArgumentParser(description="Train day-type classifiers")
    parser.add_argument('--checkpoint', choices=['10am', '11am', '13pm', 'all'],
                        default='all', help='Which checkpoint(s) to train')
    parser.add_argument('--no-lgbm', action='store_true',
                        help='Skip LightGBM, run logistic only')
    parser.add_argument('--train-thru', type=int, default=2024,
                        help='Last year included in training set (default: 2024). '
                             'E.g. --train-thru 2025 adds 2025 to train, uses 2026 as val.')
    parser.add_argument('--no-block-a', action='store_true',
                        help='Exclude Block A features (gap/prev-day) from feature matrix. '
                             'Reproduces the logistic_13pm_prod ablation (+1.2%% val accuracy).')
    parser.add_argument('--model-name', type=str, default=None,
                        help='Override output directory name (e.g. logistic_v2_13pm_prod). '
                             'Default: logistic_{cp} or lgbm_{cp}.')
    args = parser.parse_args()

    print("=" * 60)
    print("DAY-TYPE CLASSIFIER TRAINER")
    print("=" * 60)
    val_year  = args.train_thru + 1
    hold_from = args.train_thru + 2
    print(f"Split: 2023-{args.train_thru} TRAIN | {val_year} VAL | {hold_from}+ HOLDOUT")
    print(f"Block A: {'EXCLUDED' if args.no_block_a else 'included'}")
    print("Models: Logistic Regression" + ("" if args.no_lgbm else " + LightGBM"))
    if args.model_name:
        print(f"Output dir override: {args.model_name}")

    checkpoints = CHECKPOINTS if args.checkpoint == 'all' else [args.checkpoint]

    for cp in checkpoints:
        try:
            run_checkpoint(cp,
                           use_lgbm=not args.no_lgbm,
                           train_thru=args.train_thru,
                           no_block_a=args.no_block_a,
                           model_name_override=args.model_name)
        except FileNotFoundError as e:
            print(f"\nERROR: {e}")
            sys.exit(1)

    print("\n" + "=" * 60)
    print("DONE. Models saved to models/daytype/")
    print("Next: python scripts/evaluate_intraday_prediction.py")
    print("=" * 60)


if __name__ == '__main__':
    main()
