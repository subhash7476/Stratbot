#!/usr/bin/env python3
"""
Phase 4: Conditional PM Session Expectancy Engine
=================================================
Under each predicted structural state at 13:00, what is the
statistical distribution of 13:00-15:30 session behavior?

This is the bridge between structural classification and trading edge.
The key question: given the model's state prediction at 13pm, what
actually happens in the remaining 2h 30m of the session?

Approach:
  1. Load intraday_features_13pm.csv (all historical days)
  2. Apply logistic_13pm_prod to generate predictions
  3. For each day: load 1m bars, compute PM metrics
  4. Group by predicted_state + conf_tier, build distribution tables
  5. Stability check: year-by-year consistency

Output:
  data/features/day_type/pm_expectancy_raw.csv   -- per-day metrics + predictions
  data/features/day_type/pm_expectancy.json      -- grouped distribution tables

Usage:
  python scripts/run_pm_expectancy.py              # all days
  python scripts/run_pm_expectancy.py --split val  # 2025 val period only
  python scripts/run_pm_expectancy.py --min-conf 0.70  # locked predictions only
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FEATURE_DIR = ROOT / "data" / "features" / "day_type"
MODEL_DIR   = ROOT / "models" / "daytype"
CANDLE_DIR  = ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
SYMBOL      = "NSE_INDEX|Nifty 50"

DEFAULT_PROD_MODEL_DIR = MODEL_DIR / "logistic_13pm_prod"
CLUSTER_NAMES  = {0: "BearTrend", 1: "BullTrend", 2: "Choppy"}
CONF_HIGH      = 0.70
CONF_MED       = 0.55


# ── Model & feature loading ────────────────────────────────────────────────────

def load_production_model(model_dir: Path | None = None):
    """Load production model from model_dir (default: logistic_13pm_prod)."""
    prod_dir = Path(model_dir) if model_dir else DEFAULT_PROD_MODEL_DIR
    if not (prod_dir / "model.pkl").exists():
        raise FileNotFoundError(
            f"Production model not found: {prod_dir}\n"
            "Run: python scripts/train_daytype_classifier.py"
        )
    with open(prod_dir / "model.pkl", "rb") as f:
        model = pickle.load(f)
    with open(prod_dir / "scaler.pkl", "rb") as f:
        scaler = pickle.load(f)
    with open(prod_dir / "metadata.json", "r") as f:
        meta = json.load(f)
    print(f"Loaded: {prod_dir.name}  version={meta.get('version')}  "
          f"features={len(meta['feature_names'])}  block_a_excluded={meta.get('block_a_excluded')}")
    return model, scaler, meta


def load_13pm_features() -> pd.DataFrame:
    path = FEATURE_DIR / "intraday_features_13pm.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"Feature file not found: {path}\n"
            "Run: python scripts/build_intraday_features.py"
        )
    df = pd.read_csv(path, index_col="date", parse_dates=True).sort_index()
    print(f"Loaded 13pm features: {len(df)} days")
    return df


def load_1m_session(d) -> pd.DataFrame:
    """Load full 1m session for date d from DuckDB."""
    db_path = CANDLE_DIR / f"{d}.duckdb"
    if not db_path.exists():
        return pd.DataFrame()
    try:
        con = duckdb.connect(str(db_path), read_only=True)
        df = con.execute(
            "SELECT timestamp, open, high, low, close, volume "
            f"FROM candles WHERE symbol = '{SYMBOL}' ORDER BY timestamp"
        ).df()
        con.close()
        if df.empty:
            return pd.DataFrame()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df.sort_values("timestamp").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


# ── Prediction generation ──────────────────────────────────────────────────────

def generate_predictions(feat_df: pd.DataFrame, model, scaler, meta) -> pd.DataFrame:
    """Apply production model to all days in feat_df."""
    feature_names = meta["feature_names"]
    X = feat_df.reindex(columns=feature_names, fill_value=0.0).fillna(0.0).values
    X_scaled = scaler.transform(X)

    y_pred  = model.predict(X_scaled)
    y_proba = model.predict_proba(X_scaled)
    max_p   = y_proba.max(axis=1)

    rows = []
    for i, (d, actual_id) in enumerate(zip(feat_df.index, feat_df["cluster_id"])):
        pred_id   = int(y_pred[i])
        conf      = float(max_p[i])
        conf_tier = "high" if conf >= CONF_HIGH else ("med" if conf >= CONF_MED else "low")
        rows.append({
            "date":           d,
            "pred_cluster":   pred_id,
            "pred_label":     CLUSTER_NAMES.get(pred_id, str(pred_id)),
            "actual_cluster": int(actual_id) if pd.notna(actual_id) else -1,
            "actual_label":   CLUSTER_NAMES.get(int(actual_id), "?") if pd.notna(actual_id) else "?",
            "confidence":     round(conf, 4),
            "conf_tier":      conf_tier,
            "correct":        pred_id == int(actual_id) if pd.notna(actual_id) else None,
            "p_bear":         round(float(y_proba[i][0]), 4),
            "p_bull":         round(float(y_proba[i][1]), 4),
            "p_choppy":       round(float(y_proba[i][2]), 4),
        })
    pred_df = pd.DataFrame(rows).set_index("date")
    n_correct = pred_df["correct"].sum()
    n_total   = pred_df["correct"].notna().sum()
    print(f"Predictions generated: {len(pred_df)} days  accuracy={n_correct/n_total:.1%}  "
          f"high-conf={( pred_df['conf_tier']=='high').sum()} days")
    return pred_df


# ── PM metrics computation ─────────────────────────────────────────────────────

def compute_pm_metrics(df_1m: pd.DataFrame, d) -> dict | None:
    """
    Compute 13:00-15:30 session metrics from full 1m session DataFrame.
    Reference price = first 1m bar open at 13:00.
    """
    pm_mask = df_1m["timestamp"].dt.hour >= 13
    am_df   = df_1m[~pm_mask]
    pm_df   = df_1m[pm_mask].reset_index(drop=True)

    if len(pm_df) < 30:
        return None  # insufficient PM data

    ref_price = float(pm_df["open"].iloc[0])  # 13:00 open
    if ref_price <= 0:
        return None

    closes = pm_df["close"].values.astype(float)
    highs  = pm_df["high"].values.astype(float)
    lows   = pm_df["low"].values.astype(float)

    pm_final_close = float(closes[-1])
    pm_max_high    = float(highs.max())
    pm_min_low     = float(lows.min())

    # % moves anchored at 13:00 open
    pm_return   = (pm_final_close - ref_price) / ref_price * 100
    pm_max_gain = (pm_max_high    - ref_price) / ref_price * 100  # max upward excursion
    pm_max_loss = (pm_min_low     - ref_price) / ref_price * 100  # max downward (negative)
    pm_range    = (pm_max_high    - pm_min_low) / ref_price * 100

    # 1m realized vol in PM session
    ret1m  = pd.Series(closes).pct_change().dropna()
    pm_vol = float(ret1m.std() * 100) if len(ret1m) >= 2 else 0.0

    # Trend quality: 0 = pure choppy, 1 = perfectly directional
    pm_trend_strength = abs(pm_return) / max(pm_range, 0.01)

    # Close location in PM range [0=bottom, 1=top]
    if pm_range > 0.01:
        pm_close_loc = (pm_final_close - pm_min_low) / (pm_max_high - pm_min_low)
    else:
        pm_close_loc = 0.5

    # AM session reference
    am_high = float(am_df["high"].max()) if not am_df.empty else ref_price
    am_low  = float(am_df["low"].min())  if not am_df.empty else ref_price

    # PM break of AM extremes
    pm_new_day_high = int(pm_max_high > am_high)
    pm_new_day_low  = int(pm_min_low  < am_low)

    # Directional excursion analysis
    if pm_return >= 0:  # PM closed up
        pm_adverse   = abs(min(pm_max_loss,  0.0))  # how far below 13:00 did it dip
        pm_favorable = max(pm_max_gain, 0.0)        # max upward run
    else:               # PM closed down
        pm_adverse   = max(pm_max_gain,  0.0)       # how far it bounced against us
        pm_favorable = abs(min(pm_max_loss, 0.0))   # max downward run

    # Time (bar index) to daily extreme; 0 = first PM bar (13:00)
    pm_bar_to_high = int(np.argmax(highs))
    pm_bar_to_low  = int(np.argmin(lows))

    # TWAP close distance
    pm_twap        = float(pm_df["close"].mean())
    pm_close_vs_twap = (pm_final_close - pm_twap) / ref_price * 100

    return {
        "pm_return":         round(pm_return, 4),
        "pm_max_gain":       round(pm_max_gain, 4),
        "pm_max_loss":       round(pm_max_loss, 4),
        "pm_range":          round(pm_range, 4),
        "pm_vol":            round(pm_vol, 4),
        "pm_trend_strength": round(pm_trend_strength, 4),
        "pm_close_loc":      round(float(pm_close_loc), 4),
        "pm_close_vs_twap":  round(pm_close_vs_twap, 4),
        "pm_adverse":        round(pm_adverse, 4),
        "pm_favorable":      round(pm_favorable, 4),
        "pm_positive":       int(pm_return > 0),
        "pm_new_day_high":   pm_new_day_high,
        "pm_new_day_low":    pm_new_day_low,
        "pm_bar_to_high":    pm_bar_to_high,
        "pm_bar_to_low":     pm_bar_to_low,
        "n_pm_bars":         len(pm_df),
    }


# ── Distribution statistics ────────────────────────────────────────────────────

def dist_stats(series: pd.Series) -> dict:
    s = series.dropna()
    if len(s) == 0:
        return {}
    return {
        "n":    int(len(s)),
        "mean": round(float(s.mean()), 4),
        "std":  round(float(s.std()), 4),
        "p10":  round(float(s.quantile(0.10)), 4),
        "p25":  round(float(s.quantile(0.25)), 4),
        "p50":  round(float(s.quantile(0.50)), 4),
        "p75":  round(float(s.quantile(0.75)), 4),
        "p90":  round(float(s.quantile(0.90)), 4),
    }


def build_expectancy_block(grp: pd.DataFrame) -> dict:
    cont_metrics = [
        "pm_return", "pm_max_gain", "pm_max_loss", "pm_range",
        "pm_vol", "pm_trend_strength", "pm_close_loc", "pm_close_vs_twap",
        "pm_adverse", "pm_favorable", "pm_bar_to_high", "pm_bar_to_low",
    ]
    out = {m: dist_stats(grp[m]) for m in cont_metrics if m in grp.columns}
    out["pm_positive_rate"]     = round(float(grp["pm_positive"].mean()), 4)
    out["pm_new_day_high_rate"] = round(float(grp["pm_new_day_high"].mean()), 4)
    out["pm_new_day_low_rate"]  = round(float(grp["pm_new_day_low"].mean()), 4)
    corr = grp["correct"].dropna()
    out["prediction_accuracy"]  = round(float(corr.mean()), 4) if len(corr) > 0 else None
    out["n_days"]               = len(grp)
    return out


# ── Report printing ────────────────────────────────────────────────────────────

STATES = ["BearTrend", "BullTrend", "Choppy"]

def _fmt(v, pct=False) -> str:
    if v is None:
        return "   n/a"
    if pct:
        return f"{v:>+7.3f}%"
    return f"{v:>7.3f}"


def print_expectancy_table(summary: dict, section: str) -> None:
    print(f"\n{'='*75}")
    print(f"  PM EXPECTANCY  [{section.upper()}]")
    print(f"{'='*75}")

    for state in STATES:
        key = f"{section}|{state}"
        if key not in summary:
            continue
        blk = summary[key]
        n   = blk.get("n_days", "?")
        acc = blk.get("prediction_accuracy")
        acc_str = f"  pred_acc={acc:.1%}" if acc is not None else ""
        pos  = blk.get("pm_positive_rate", 0)
        ndh  = blk.get("pm_new_day_high_rate", 0)
        ndl  = blk.get("pm_new_day_low_rate", 0)

        print(f"\n  [{state}]  n={n}{acc_str}")
        print(f"  PM positive={pos:.1%}   New-day-high={ndh:.1%}   New-day-low={ndl:.1%}")

        hdr = f"  {'Metric':<24}  {'mean':>8}  {'p10':>8}  {'p25':>8}  {'p50':>8}  {'p75':>8}  {'p90':>8}"
        sep = f"  {'-'*24}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}"
        print(hdr)
        print(sep)

        show = [
            ("pm_return",         "PM return %",       True),
            ("pm_max_gain",       "Max gain %",        True),
            ("pm_max_loss",       "Max loss %",        True),
            ("pm_range",          "PM range %",        True),
            ("pm_vol",            "PM vol (1m std%)",  True),
            ("pm_trend_strength", "Trend strength",    False),
            ("pm_close_loc",      "Close location",    False),
            ("pm_adverse",        "Adverse excursion", True),
            ("pm_favorable",      "Favorable excurs.", True),
        ]
        for metric, label, is_pct in show:
            if metric not in blk or not blk[metric]:
                continue
            d = blk[metric]
            suf = "%" if is_pct else " "
            print(
                f"  {label:<24}  "
                f"{d.get('mean',0):>+7.3f}{suf}  "
                f"{d.get('p10',0):>+7.3f}{suf}  "
                f"{d.get('p25',0):>+7.3f}{suf}  "
                f"{d.get('p50',0):>+7.3f}{suf}  "
                f"{d.get('p75',0):>+7.3f}{suf}  "
                f"{d.get('p90',0):>+7.3f}{suf}"
            )


def print_year_stability(summary: dict, years: list) -> None:
    print(f"\n{'='*75}")
    print(f"  YEAR-BY-YEAR STABILITY  (pm_return: median | positive-rate)")
    print(f"{'='*75}")
    hdr = f"  {'Year':<6}" + "".join(f"  {s:<22}" for s in STATES)
    print(hdr)
    for yr in sorted(years):
        row = f"  {yr:<6}"
        for state in STATES:
            key = f"year={yr}|{state}"
            if key in summary and "pm_return" in summary[key]:
                d     = summary[key]
                med   = d["pm_return"]["p50"]
                pos   = summary[key].get("pm_positive_rate", 0)
                n     = d["pm_return"]["n"]
                row  += f"  {med:>+6.3f}%  pos={pos:.0%}  n={n:<3}  "
            else:
                row  += f"  {'N/A':<22}"
        print(row)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Phase 4: PM session expectancy engine")
    parser.add_argument("--split", choices=["train", "val", "hold", "all"], default="all",
                        help="Data split (default: all)")
    parser.add_argument("--min-conf", type=float, default=0.0,
                        help="Minimum confidence filter")
    parser.add_argument("--model-dir", type=str, default=None,
                        help="Path to model directory (default: models/daytype/logistic_13pm_prod). "
                             "Use to run predictions from a retrained v2 model without overwriting the default.")
    parser.add_argument("--out-raw", type=str, default=None,
                        help="Output path for pm_expectancy_raw.csv "
                             "(default: data/features/day_type/pm_expectancy_raw.csv). "
                             "Use to save v2 predictions alongside the original.")
    args = parser.parse_args()

    # 1. Load model + features
    model_dir = Path(args.model_dir) if args.model_dir else None
    model, scaler, meta = load_production_model(model_dir=model_dir)
    feat_df = load_13pm_features()

    # 2. Apply split mask
    if args.split == "train":
        feat_df = feat_df[feat_df.index.year <= 2024]
    elif args.split == "val":
        feat_df = feat_df[feat_df.index.year == 2025]
    elif args.split == "hold":
        feat_df = feat_df[feat_df.index.year >= 2026]
    print(f"Split '{args.split}': {len(feat_df)} days")

    # 3. Generate predictions
    pred_df = generate_predictions(feat_df, model, scaler, meta)

    # Apply min-conf filter
    if args.min_conf > 0:
        pred_df = pred_df[pred_df["confidence"] >= args.min_conf]
        print(f"After min-conf={args.min_conf}: {len(pred_df)} days")

    # 4. Load 1m bars and compute PM metrics
    print("\nLoading 1m bars and computing PM metrics...")
    records = []
    missing = 0
    for d, pred_row in pred_df.iterrows():
        df_1m = load_1m_session(d.date())
        if df_1m.empty:
            missing += 1
            continue
        pm = compute_pm_metrics(df_1m, d)
        if pm is None:
            missing += 1
            continue
        record = {
            "date":          str(d.date()),
            "year":          d.year,
            "pred_label":    pred_row["pred_label"],
            "actual_label":  pred_row["actual_label"],
            "confidence":    pred_row["confidence"],
            "conf_tier":     pred_row["conf_tier"],
            "correct":       pred_row["correct"],
        }
        record.update(pm)
        records.append(record)

    if not records:
        print("ERROR: No PM records computed. Check 1m data availability.")
        sys.exit(1)

    raw_df = pd.DataFrame(records)
    out_raw = Path(args.out_raw) if args.out_raw else FEATURE_DIR / "pm_expectancy_raw.csv"
    raw_df.to_csv(out_raw, index=False)
    n_skip = missing
    print(f"PM metrics: {len(raw_df)} days computed  |  {n_skip} skipped (missing data)")
    print(f"Raw saved -> {out_raw}")

    # 5. Build expectancy tables
    summary = {}

    # (a) All predictions by state
    for state in STATES:
        grp = raw_df[raw_df["pred_label"] == state]
        if len(grp) >= 3:
            summary[f"all|{state}"] = build_expectancy_block(grp)

    # (b) By conf_tier + state
    for tier in ["high", "med", "low"]:
        tier_df = raw_df[raw_df["conf_tier"] == tier]
        for state in STATES:
            grp = tier_df[tier_df["pred_label"] == state]
            if len(grp) >= 5:
                summary[f"conf_tier={tier}|{state}"] = build_expectancy_block(grp)

    # (c) Correct-only (sanity check — best-case edge)
    corr_df = raw_df[raw_df["correct"] == True]
    for state in STATES:
        grp = corr_df[corr_df["pred_label"] == state]
        if len(grp) >= 5:
            summary[f"correct|{state}"] = build_expectancy_block(grp)

    # (d) Year-by-year
    years = sorted(raw_df["year"].unique())
    for yr in years:
        yr_df = raw_df[raw_df["year"] == yr]
        for state in STATES:
            grp = yr_df[yr_df["pred_label"] == state]
            if len(grp) >= 3:
                summary[f"year={yr}|{state}"] = build_expectancy_block(grp)

    out_json = FEATURE_DIR / "pm_expectancy.json"
    with open(out_json, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Summary saved -> {out_json}")

    # 6. Print reports
    print_expectancy_table(summary, "all")
    if "conf_tier=high|BullTrend" in summary or "conf_tier=high|BearTrend" in summary:
        print_expectancy_table(summary, "conf_tier=high")
    print_year_stability(summary, years)

    # 7. Quick accuracy audit
    print(f"\n{'='*75}")
    print(f"  PREDICTION ACCURACY CONDITIONAL ON PM OUTCOME")
    print(f"{'='*75}")
    for state in STATES:
        key = f"all|{state}"
        if key not in summary:
            continue
        acc  = summary[key].get("prediction_accuracy")
        n    = summary[key].get("n_days")
        pos  = summary[key].get("pm_positive_rate")
        ndh  = summary[key].get("pm_new_day_high_rate")
        trend = summary[key].get("pm_trend_strength", {}).get("p50")
        print(f"\n  {state:<12}  n={n}  pred_acc={acc:.1%}  "
              f"pm_pos={pos:.1%}  new_day_hi={ndh:.1%}  "
              f"trend_strength(p50)={trend:.2f}")

    print(f"\nPhase 4 complete.")
    print(f"Key output: {out_json}")
    print(f"Next: Phase 5 — strategy design based on these expectancy profiles.")


if __name__ == "__main__":
    main()
