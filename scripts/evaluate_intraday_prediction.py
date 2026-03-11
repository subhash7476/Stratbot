"""
Intraday Prediction Evaluator
==============================
Loads trained models for all 3 checkpoints and produces a unified
prediction quality report. Shows:

  1. Accuracy by checkpoint (10am / 11am / 13pm)
  2. Accuracy by confidence tier (high / medium / low)
  3. Accuracy by cluster (BearTrend / BullTrend / Choppy)
  4. Confusion matrices
  5. Prediction stability: does the model flip between checkpoints?
  6. Calibration check: is predicted probability reliable?
  7. Year-by-year accuracy (regime stability of classifier)

This is a diagnostic tool, not a training step.
Run AFTER train_daytype_classifier.py.

Usage:
  python scripts/evaluate_intraday_prediction.py
  python scripts/evaluate_intraday_prediction.py --model logistic
  python scripts/evaluate_intraday_prediction.py --split val   # val only
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FEATURE_DIR = ROOT / "data" / "features" / "day_type"
MODEL_DIR   = ROOT / "models" / "daytype"
OUTPUT_DIR  = FEATURE_DIR

CHECKPOINTS  = ['10am', '11am', '13pm']
CLUSTER_NAMES = {0: 'BearTrend', 1: 'BullTrend', 2: 'Choppy'}
CONF_HIGH   = 0.70
CONF_MED    = 0.55


# ── Model loading ─────────────────────────────────────────────────────────────

def load_model_artifacts(model_name: str, cp: str) -> tuple | None:
    base = MODEL_DIR / f"{model_name}_{cp}"
    if not (base / "model.pkl").exists():
        return None
    with open(base / "model.pkl", 'rb') as f:
        model = pickle.load(f)
    with open(base / "scaler.pkl", 'rb') as f:
        scaler = pickle.load(f)
    with open(base / "metadata.json", 'r') as f:
        meta = json.load(f)
    return model, scaler, meta


# ── Data loading ──────────────────────────────────────────────────────────────

def load_checkpoint_data(cp: str) -> pd.DataFrame:
    path = FEATURE_DIR / f"intraday_features_{cp}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing: {path}")
    return pd.read_csv(path, index_col='date', parse_dates=True).sort_index()


def prepare_X(df: pd.DataFrame, feature_names: list[str]) -> tuple[np.ndarray, pd.Series]:
    """Extract and align feature matrix, return y as Series."""
    y = df['cluster_id'].dropna().astype(int)
    df = df.loc[y.index]
    X = df.reindex(columns=feature_names, fill_value=0.0).fillna(0.0).values
    return X, y


def split_mask(df: pd.DataFrame, split: str) -> pd.Series:
    if split == 'train':
        return df.index.year <= 2024
    elif split == 'val':
        return df.index.year == 2025
    elif split == 'hold':
        return df.index.year >= 2026
    else:  # 'all'
        return pd.Series(True, index=df.index)


# ── Evaluation helpers ─────────────────────────────────────────────────────────

def accuracy_by_confidence(y_true: np.ndarray, y_pred: np.ndarray,
                            proba: np.ndarray) -> dict:
    max_p = proba.max(axis=1)
    out = {}
    for tier, lo, hi in [('high', CONF_HIGH, 1.01), ('med', CONF_MED, CONF_HIGH),
                          ('low', 0.0, CONF_MED)]:
        mask = (max_p >= lo) & (max_p < hi)
        n = mask.sum()
        if n > 0:
            out[tier] = {
                'n': int(n),
                'pct': round(float(n / len(y_true)), 3),
                'acc': round(float(accuracy_score(y_true[mask], y_pred[mask])), 4),
            }
        else:
            out[tier] = {'n': 0, 'pct': 0.0, 'acc': None}
    return out


def accuracy_by_cluster(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    out = {}
    for cls_id, cls_name in CLUSTER_NAMES.items():
        mask = y_true == cls_id
        n = mask.sum()
        if n > 0:
            out[cls_name] = {
                'n': int(n),
                'acc': round(float(accuracy_score(y_true[mask], y_pred[mask])), 4),
            }
    return out


def accuracy_by_year(y_true: np.ndarray, y_pred: np.ndarray,
                     dates: pd.DatetimeIndex) -> dict:
    out = {}
    for yr in sorted(dates.year.unique()):
        mask = dates.year == yr
        n = mask.sum()
        if n > 0:
            out[int(yr)] = {
                'n': int(n),
                'acc': round(float(accuracy_score(y_true[mask], y_pred[mask])), 4),
            }
    return out


def flip_rate(pred_a: np.ndarray, pred_b: np.ndarray) -> float:
    """Fraction of days where prediction changes between two checkpoints."""
    return float((pred_a != pred_b).mean())


def print_section(title: str) -> None:
    print(f"\n{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")


# ── Main report ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Evaluate intraday day-type predictions")
    parser.add_argument('--model', choices=['logistic', 'lgbm', 'both'], default='both')
    parser.add_argument('--split', choices=['train', 'val', 'hold', 'all'], default='val')
    args = parser.parse_args()

    model_names = ['logistic', 'lgbm'] if args.model == 'both' else [args.model]

    print("=" * 60)
    print("INTRADAY PREDICTION EVALUATION REPORT")
    print("=" * 60)
    print(f"  Split:  {args.split}")
    print(f"  Models: {', '.join(model_names)}")

    for model_name in model_names:
        print(f"\n{'='*60}")
        print(f"MODEL: {model_name.upper()}")
        print(f"{'='*60}")

        # ── Per-checkpoint accuracy table ──────────────────────────────────────
        print_section("Accuracy by Checkpoint")
        print(f"  {'Checkpoint':>10}  {'N':>5}  {'Acc':>7}  {'Bear':>7}  {'Bull':>7}  {'Chop':>7}")
        print(f"  {'─'*10}  {'─'*5}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}")

        all_predictions = {}   # {cp: (dates, y_true, y_pred, proba)}
        checkpoint_results = {}

        for cp in CHECKPOINTS:
            arts = load_model_artifacts(model_name, cp)
            if arts is None:
                print(f"  {cp:>10}  [model not found — run train_daytype_classifier.py]")
                continue

            model, scaler, meta = arts
            feat_names = meta['feature_names']

            try:
                df = load_checkpoint_data(cp)
            except FileNotFoundError:
                print(f"  {cp:>10}  [feature file missing]")
                continue

            # Apply split mask
            mask = split_mask(df, args.split)
            df_split = df[mask]

            X, y = prepare_X(df_split, feat_names)
            X_scaled = scaler.transform(X)

            y_pred  = model.predict(X_scaled)
            y_proba = model.predict_proba(X_scaled)
            dates   = y.index

            all_predictions[cp] = (dates, y.values, y_pred, y_proba)

            acc = accuracy_score(y.values, y_pred)
            per_cls = accuracy_by_cluster(y.values, y_pred)

            bear_acc = per_cls.get('BearTrend', {}).get('acc', np.nan)
            bull_acc = per_cls.get('BullTrend', {}).get('acc', np.nan)
            chop_acc = per_cls.get('Choppy',    {}).get('acc', np.nan)

            print(f"  {cp:>10}  {len(y):>5}  {acc:>6.1%}  {bear_acc:>6.1%}  {bull_acc:>6.1%}  {chop_acc:>6.1%}")
            checkpoint_results[cp] = {
                'acc': acc, 'per_cls': per_cls,
                'dates': dates, 'y_true': y.values, 'y_pred': y_pred, 'proba': y_proba,
            }

        # ── Confidence calibration ─────────────────────────────────────────────
        print_section("Accuracy by Confidence Tier")
        print(f"  {'CP':>5}  {'Tier':>6}  {'N':>5}  {'%Days':>6}  {'Acc':>7}")
        for cp, cr in checkpoint_results.items():
            conf_res = accuracy_by_confidence(cr['y_true'], cr['y_pred'], cr['proba'])
            for tier, info in conf_res.items():
                acc_str = f"{info['acc']:.1%}" if info['acc'] is not None else "  n/a"
                print(f"  {cp:>5}  {tier:>6}  {info['n']:>5}  {info['pct']:>5.1%}  {acc_str:>7}")

        # ── Year-by-year accuracy ──────────────────────────────────────────────
        if args.split == 'all':
            print_section("Accuracy by Year (classifier regime stability)")
            print(f"  {'CP':>5}  {'Year':>6}  {'N':>5}  {'Acc':>7}")
            for cp, cr in checkpoint_results.items():
                yr_res = accuracy_by_year(cr['y_true'], cr['y_pred'], cr['dates'])
                for yr, info in yr_res.items():
                    print(f"  {cp:>5}  {yr:>6}  {info['n']:>5}  {info['acc']:>6.1%}")

        # ── Prediction stability (flip rate between checkpoints) ───────────────
        if len(all_predictions) >= 2:
            print_section("Prediction Stability (flip rate between checkpoints)")
            cp_list = [cp for cp in CHECKPOINTS if cp in all_predictions]
            for i in range(len(cp_list) - 1):
                cp_a, cp_b = cp_list[i], cp_list[i + 1]
                dates_a, _, pred_a, _ = all_predictions[cp_a]
                dates_b, _, pred_b, _ = all_predictions[cp_b]

                # Align on common dates
                common = dates_a.intersection(dates_b)
                if len(common) == 0:
                    continue
                pa = pred_a[np.isin(dates_a, common)]
                pb = pred_b[np.isin(dates_b, common)]
                fr = flip_rate(pa, pb)
                print(f"  {cp_a} → {cp_b}: {fr:.1%} of days change prediction")

        # ── Confusion matrices ─────────────────────────────────────────────────
        print_section("Confusion Matrices (rows=actual, cols=predicted)")
        cls_labels = [CLUSTER_NAMES[i] for i in sorted(CLUSTER_NAMES)]
        for cp, cr in checkpoint_results.items():
            if len(cr['y_true']) == 0:
                continue
            cm = confusion_matrix(cr['y_true'], cr['y_pred'])
            print(f"\n  {cp} — accuracy {cr['acc']:.1%}")
            header = f"  {'':>12}  " + "  ".join(f"{l:>9}" for l in cls_labels)
            print(header)
            for i, row_label in enumerate(cls_labels):
                if i < len(cm):
                    row_str = "  ".join(f"{v:>9d}" for v in cm[i])
                    print(f"  {row_label:>12}  {row_str}")

    # ── Save combined prediction file for live engine ─────────────────────────
    print_section("Saving combined predictions CSV")

    # Build a unified prediction DataFrame (best model = logistic as default)
    best_model = 'logistic' if 'logistic' in model_names else model_names[0]
    combined_rows = []

    for cp in CHECKPOINTS:
        arts = load_model_artifacts(best_model, cp)
        if arts is None:
            continue
        model, scaler, meta = arts
        feat_names = meta['feature_names']
        try:
            df_all = load_checkpoint_data(cp)
        except FileNotFoundError:
            continue

        X_all, y_all = prepare_X(df_all, feat_names)
        X_s = scaler.transform(X_all)
        y_pred_all  = model.predict(X_s)
        y_proba_all = model.predict_proba(X_s)

        for i, (d, yt) in enumerate(zip(y_all.index, y_all.values)):
            max_p = y_proba_all[i].max()
            pred  = int(y_pred_all[i])
            conf_tier = 'high' if max_p >= CONF_HIGH else ('med' if max_p >= CONF_MED else 'low')
            combined_rows.append({
                'date':             d,
                'checkpoint':       cp,
                'actual_cluster':   int(yt),
                'pred_cluster':     pred,
                'pred_label':       CLUSTER_NAMES.get(pred, str(pred)),
                'confidence':       round(float(max_p), 4),
                'conf_tier':        conf_tier,
                'correct':          int(yt) == pred,
                'p_bear':           round(float(y_proba_all[i][0]), 4),
                'p_bull':           round(float(y_proba_all[i][1]), 4),
                'p_choppy':         round(float(y_proba_all[i][2]), 4),
            })

    if combined_rows:
        out_df = pd.DataFrame(combined_rows).sort_values(['date', 'checkpoint'])
        out_path = OUTPUT_DIR / "daytype_predictions.csv"
        out_df.to_csv(out_path, index=False)
        print(f"  Saved: {out_path} ({len(out_df)} rows)")
        print(f"  Columns: {list(out_df.columns)}")

    print("\n" + "=" * 60)
    print("DONE. Review accuracy vs expected ranges:")
    print("  10am: 55-65%  |  11am: 65-72%  |  13pm: 75-85%")
    print("If materially above range: check for leakage.")
    print("If materially below range: review feature construction.")
    print("=" * 60)


if __name__ == '__main__':
    main()
