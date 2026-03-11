#!/usr/bin/env python3
"""
AM Checkpoint Edge Diagnostic
==============================
Evaluates whether the 10am / 11am day-type logistic model produces
a tradeable directional edge on the CHECKPOINT -> 13:00 return.

For each checkpoint model:
  1. Load trained logistic model + scaler
  2. Generate predictions + confidence for all days
  3. Compute return from checkpoint bar open to 12:59 close
  4. Condition on:
       - Predicted BullTrend (high-conf  >= 0.60) -> long
       - Predicted BullTrend (med-conf   [0.50, 0.60)) -> long
       - Predicted BearTrend (high-conf  >= 0.60) -> short
       - Predicted BearTrend (med-conf   [0.50, 0.60)) -> short
  5. Report: N, WR, avg_win, avg_loss, E/trade, t-stat, MaxDD
  6. Walk-forward: Train 2023-24 / Test 2025-26

Decision gate:
  - High-conf E/trade >= 0.10% AND t >= 2.5 stable in 2025-26 -> Path A open
  - E/trade 0.05% with t < 1.5 -> Close AM

Entry convention:
  10am model: entry at open of 10:00 bar (bar index 45 in AM dataframe)
  11am model: entry at open of 11:00 bar (bar index 105 in AM dataframe)
  Exit: close of 12:59 bar (last AM bar, index 224)

Inputs:
  models/daytype/logistic_10am/  (model.pkl, scaler.pkl, metadata.json)
  models/daytype/logistic_11am/
  data/features/day_type/intraday_features_10am.csv
  data/features/day_type/intraday_features_11am.csv
  data/market_data/nse/candles/1m/{date}.duckdb

Output:
  data/features/day_type/am_checkpoint_edge.json
  console report
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FEAT_DIR   = ROOT / "data" / "features" / "day_type"
MODEL_DIR  = ROOT / "models" / "daytype"
CANDLE_DIR = ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
SYMBOL     = "NSE_INDEX|Nifty 50"
OUT_JSON   = FEAT_DIR / "am_checkpoint_edge.json"

RTC       = 0.04   # round-trip cost %
HIGH_CONF = 0.60   # >= this -> "high" confidence tier
MED_CONF  = 0.50   # >= this and < HIGH_CONF -> "med" confidence tier

CLUSTER_NAMES = {0: "BearTrend", 1: "BullTrend", 2: "Choppy"}
DIRECTION     = {"BullTrend": 1, "BearTrend": -1}

# Entry bar index within the AM-only dataframe (index 0 = 9:15 bar)
# 9:15 + 45min = 10:00  -> bar 45
# 9:15 + 105min = 11:00 -> bar 105
ENTRY_BAR = {"10am": 45, "11am": 105}


# ── Data loading ─────────────────────────────────────────────────────────────

def load_1m(d: str) -> pd.DataFrame:
    db = CANDLE_DIR / f"{d}.duckdb"
    if not db.exists():
        return pd.DataFrame()
    try:
        con = duckdb.connect(str(db), read_only=True)
        df  = con.execute(
            "SELECT timestamp, open, high, low, close FROM candles "
            f"WHERE symbol = '{SYMBOL}' ORDER BY timestamp"
        ).df()
        con.close()
        if df.empty:
            return pd.DataFrame()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df.sort_values("timestamp").reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


def compute_checkpoint_return(df_1m: pd.DataFrame, entry_bar: int) -> dict | None:
    """
    Compute the raw (unsigned) market return from the checkpoint to 12:59 close.

    Entry:  open of bar at `entry_bar` index in AM-session slice
    Exit:   close of last AM bar (12:59 close)

    Also computes max_up and max_down (from entry, exclusive of entry bar itself)
    for informational reference.
    """
    am_mask = df_1m["timestamp"].dt.hour < 13
    am_df   = df_1m[am_mask].reset_index(drop=True)

    # Need at least entry_bar+1 bars (to have an entry) plus some remaining bars
    if len(am_df) < entry_bar + 20:
        return None

    entry_price = float(am_df["open"].iloc[entry_bar])
    if entry_price <= 0:
        return None

    exit_price = float(am_df["close"].iloc[-1])   # 12:59 close

    # Post-entry bars: bars AFTER the entry bar (entry_bar+1 onwards)
    post = am_df.iloc[entry_bar + 1:].reset_index(drop=True)

    if post.empty:
        return None

    highs = post["high"].values.astype(float)
    lows  = post["low"].values.astype(float)

    raw_return = (exit_price - entry_price) / entry_price * 100.0
    max_up     = (highs.max() - entry_price) / entry_price * 100.0
    max_down   = (entry_price - lows.min())  / entry_price * 100.0

    return {
        "entry_price": entry_price,
        "exit_price":  exit_price,
        "raw_return":  round(raw_return, 4),
        "max_up":      round(max_up, 4),
        "max_down":    round(max_down, 4),
    }


# ── Model loading & prediction ────────────────────────────────────────────────

def load_model(checkpoint: str):
    """Returns (model, scaler, feature_names)."""
    d = MODEL_DIR / f"logistic_{checkpoint}"
    with open(d / "model.pkl", "rb") as f:
        model = pickle.load(f)
    with open(d / "scaler.pkl", "rb") as f:
        scaler = pickle.load(f)
    with open(d / "metadata.json") as f:
        meta = json.load(f)
    return model, scaler, meta["feature_names"]


def build_predictions(checkpoint: str) -> pd.DataFrame:
    """
    Loads the feature CSV, runs the model, and returns a DataFrame:
      date | pred_class | pred_label | confidence | conf_tier
    """
    model, scaler, feature_names = load_model(checkpoint)

    feat_path = FEAT_DIR / f"intraday_features_{checkpoint}.csv"
    df = pd.read_csv(feat_path)

    # Ensure all expected feature columns exist (impute missing as 0)
    for col in feature_names:
        if col not in df.columns:
            df[col] = 0.0

    X = df[feature_names].fillna(0.0)
    X_scaled  = scaler.transform(X)
    probs     = model.predict_proba(X_scaled)
    pred_cls  = np.argmax(probs, axis=1).astype(int)
    confidence = probs[np.arange(len(probs)), pred_cls]

    def conf_tier(p: float) -> str:
        if p >= HIGH_CONF:
            return "high"
        if p >= MED_CONF:
            return "med"
        return "low"

    out = df[["date"]].copy()
    out["pred_class"]  = pred_cls
    out["pred_label"]  = [CLUSTER_NAMES[c] for c in pred_cls]
    out["confidence"]  = confidence.round(4)
    out["conf_tier"]   = [conf_tier(p) for p in confidence]
    return out


# ── Statistics ────────────────────────────────────────────────────────────────

def dist_stats(arr: np.ndarray) -> dict:
    if len(arr) == 0:
        return {"n": 0}
    wins   = arr[arr > 0]
    losses = arr[arr <= 0]
    se     = arr.std(ddof=1) / len(arr) ** 0.5
    tval   = float(arr.mean() / se) if se > 0 else 0.0
    pval   = float(stats.ttest_1samp(arr, 0).pvalue) / 2   # one-tailed

    # MaxDD on cumulative equity curve
    eq   = np.cumsum(arr)
    peak = np.maximum.accumulate(eq)
    dd   = eq - peak

    return {
        "n":        int(len(arr)),
        "wr":       round(float(len(wins) / len(arr)), 4),
        "avg_win":  round(float(wins.mean()),   4) if len(wins)   else 0.0,
        "avg_loss": round(float(losses.mean()), 4) if len(losses) else 0.0,
        "e_trade":  round(float(arr.mean()),    4),
        "std":      round(float(arr.std(ddof=1)), 4),
        "t_stat":   round(tval, 3),
        "p_val":    round(pval, 4),
        "max_dd":   round(float(dd.min()), 4),
    }


# ── Per-checkpoint pipeline ───────────────────────────────────────────────────

def run_checkpoint(checkpoint: str) -> dict:
    print(f"\n{'='*65}")
    print(f"  CHECKPOINT: {checkpoint.upper()}")
    print(f"{'='*65}")

    entry_bar = ENTRY_BAR[checkpoint]
    preds     = build_predictions(checkpoint)
    preds["date"] = pd.to_datetime(preds["date"])

    # Load 1m returns for all prediction dates
    rows      = []
    all_dates = sorted(preds["date"].dt.strftime("%Y-%m-%d").unique())
    print(f"  Loading 1m data for {len(all_dates)} days...", flush=True)

    for d in all_dates:
        df_1m = load_1m(d)
        if df_1m.empty:
            continue
        ret = compute_checkpoint_return(df_1m, entry_bar)
        if ret is None:
            continue
        rows.append({"date": d, **ret})

    ret_df = pd.DataFrame(rows)
    ret_df["date"] = pd.to_datetime(ret_df["date"])

    merged        = preds.merge(ret_df, on="date", how="inner")
    merged["year"] = merged["date"].dt.year
    print(f"  Merged rows: {len(merged)}")

    # Confidence distribution
    total = len(merged)
    for tier in ["high", "med", "low"]:
        cnt = (merged["conf_tier"] == tier).sum()
        print(f"  Conf tier {tier:4s}: {cnt:3d} ({cnt/total:.1%})")

    results = {}

    for label, tier in [
        ("BullTrend", "high"),
        ("BullTrend", "med"),
        ("BearTrend", "high"),
        ("BearTrend", "med"),
    ]:
        seg_key   = f"{label}_{tier}"
        direction = DIRECTION[label]
        mask      = (merged["pred_label"] == label) & (merged["conf_tier"] == tier)
        seg       = merged[mask].copy()

        # Directional P&L net of round-trip cost
        seg = seg.copy()
        seg["trade_ret"] = seg["raw_return"] * direction - RTC

        def analyze(subset: pd.DataFrame) -> dict:
            if subset.empty:
                return {"n": 0}
            return dist_stats(subset["trade_ret"].values)

        all_stats   = analyze(seg)
        train_stats = analyze(seg[seg["year"].isin([2023, 2024])])
        test_stats  = analyze(seg[seg["year"].isin([2025, 2026])])

        results[seg_key] = {
            "all":   all_stats,
            "train": train_stats,
            "test":  test_stats,
        }

        # Console print
        print(f"\n  [{label} | {tier.upper()} CONF]")
        for split_name, st in [
            ("ALL",         all_stats),
            ("TRAIN 23-24", train_stats),
            ("TEST  25-26", test_stats),
        ]:
            if st.get("n", 0) == 0:
                print(f"    {split_name:12s}: N=0")
                continue
            print(
                f"    {split_name:12s}: "
                f"N={st['n']:3d}  "
                f"WR={st['wr']:.1%}  "
                f"E={st['e_trade']:+.3f}%  "
                f"t={st['t_stat']:+.2f}  "
                f"MaxDD={st['max_dd']:.3f}%  "
                f"avg_W={st['avg_win']:+.3f}%  "
                f"avg_L={st['avg_loss']:+.3f}%"
            )

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    output = {}

    for ckpt in ["10am", "11am"]:
        output[ckpt] = run_checkpoint(ckpt)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved -> {OUT_JSON}")

    # ── Decision gate ──────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print("  DECISION GATE (High-conf directional, TEST 2025-26)")
    print(f"{'='*65}")
    for ckpt in ["10am", "11am"]:
        for label in ["BullTrend", "BearTrend"]:
            seg  = output[ckpt].get(f"{label}_high", {})
            test = seg.get("test", {})
            e    = test.get("e_trade", 0.0)
            t    = test.get("t_stat",  0.0)
            n    = test.get("n",       0)
            if e >= 0.10 and t >= 2.5:
                verdict = "OPEN  -- Path A viable"
            elif e >= 0.05 and t >= 1.5:
                verdict = "BORDERLINE -- needs more data"
            else:
                verdict = "CLOSE -- insufficient edge"
            print(f"  {ckpt}  {label:10s} high: E={e:+.3f}%  t={t:+.2f}  N={n:3d}  -> {verdict}")

    print("\nDone.")


if __name__ == "__main__":
    main()
