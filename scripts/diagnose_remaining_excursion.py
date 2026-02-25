#!/usr/bin/env python3
"""
Remaining Excursion Diagnostic -- Phase 5 Pre-flight
=====================================================
Answers the load-bearing question before running any further backtests:

  "What is the distribution of available price move FROM 13:02 to the
   ultimate PM extreme, for high-confidence Bull and Bear prediction days?"

If the extreme was already made before 13:02 (p25 of first extreme = 2min),
those days will show NEGATIVE or ZERO remaining excursion -- correctly capturing
the structural problem with a 13:02 entry.

Two sub-questions:
  A) Total remaining excursion from 13:02 to ultimate PM extreme
     -- ceiling of what any 13:02 entry strategy can capture
  B) Conditional: given extreme is still AHEAD at 13:02, remaining excursion
     -- excursion available on "good" days (first_extreme_bar >= 2)

Outputs:
  console report with full distribution tables
  data/features/day_type/remaining_excursion.json

Usage:
  python scripts/diagnose_remaining_excursion.py
  python scripts/diagnose_remaining_excursion.py --min-conf 0.80
  python scripts/diagnose_remaining_excursion.py --state bear
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FEATURE_DIR = ROOT / "data" / "features" / "day_type"
CANDLE_DIR  = ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
SYMBOL      = "NSE_INDEX|Nifty 50"

RAW_CSV  = FEATURE_DIR / "pm_expectancy_raw.csv"
OUT_JSON = FEATURE_DIR / "remaining_excursion.json"

# Thresholds to check viability (as % of entry price)
TARGET_THRESHOLDS = [0.08, 0.10, 0.12, 0.15, 0.18, 0.22, 0.25, 0.30]

# Round-trip cost (must be recovered by any trade)
ROUND_TRIP_COST = 0.04  # %

# Entry bar (0-indexed from 13:00 open = bar 0)
ENTRY_BAR = 2   # 13:02 open


# ── Data loading ───────────────────────────────────────────────────────────────

def load_raw(min_conf: float, state_filter: str) -> pd.DataFrame:
    if not RAW_CSV.exists():
        raise FileNotFoundError(
            f"Missing: {RAW_CSV}\nRun: python scripts/run_pm_expectancy.py"
        )
    df = pd.read_csv(RAW_CSV, parse_dates=["date"])
    df = df[df["conf_tier"] == "high"].copy()
    if min_conf > 0:
        df = df[df["confidence"] >= min_conf]
    if state_filter == "bull":
        df = df[df["pred_label"] == "BullTrend"]
    elif state_filter == "bear":
        df = df[df["pred_label"] == "BearTrend"]
    else:
        df = df[df["pred_label"].isin(["BullTrend", "BearTrend"])]
    return df.reset_index(drop=True)


def load_1m(d) -> pd.DataFrame:
    db = CANDLE_DIR / f"{d}.duckdb"
    if not db.exists():
        return pd.DataFrame()
    try:
        con = duckdb.connect(str(db), read_only=True)
        df = con.execute(
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


# ── Per-day excursion calculation ──────────────────────────────────────────────

def compute_excursion(df_1m: pd.DataFrame, direction: str) -> dict | None:
    """
    For one day, compute available excursion from 13:02 to ultimate PM extreme.

    Returns dict with:
      entry_price        : open of bar ENTRY_BAR (13:02)
      first_extreme_bar  : first PM bar where new-day extreme prints (-1 if none)
      extreme_already_at_entry : True if extreme made before ENTRY_BAR
      remaining_excursion_pct  : (ultimate_future_extreme - entry) / entry * sign
                                  using highs/lows STRICTLY from bar ENTRY_BAR onwards
      ultimate_from_entry_pct  : same as above (direction-adjusted)
      ultimate_full_pm_pct     : ultimate extreme from FULL PM session (from 13:00 ref)
      am_high / am_low         : AM session extremes
    """
    pm_mask = df_1m["timestamp"].dt.hour >= 13
    am_df   = df_1m[~pm_mask]
    pm_df   = df_1m[pm_mask].reset_index(drop=True)

    if len(pm_df) < max(30, ENTRY_BAR + 5) or am_df.empty:
        return None

    ref_1300 = float(pm_df["open"].iloc[0])
    if ref_1300 <= 0:
        return None

    # Entry price = open of bar ENTRY_BAR
    if len(pm_df) <= ENTRY_BAR:
        return None
    entry_price = float(pm_df["open"].iloc[ENTRY_BAR])
    if entry_price <= 0:
        return None

    am_high = float(am_df["high"].max())
    am_low  = float(am_df["low"].min())

    highs_full = pm_df["high"].values.astype(float)
    lows_full  = pm_df["low"].values.astype(float)

    # --- First new-day extreme (full PM session)
    if direction == "bull":
        new_extreme_mask = highs_full > am_high
    else:
        new_extreme_mask = lows_full < am_low

    new_extreme_made = bool(new_extreme_mask.any())
    if new_extreme_made:
        first_extreme_bar = int(np.argmax(new_extreme_mask))
    else:
        first_extreme_bar = -1

    extreme_already_at_entry = new_extreme_made and (first_extreme_bar < ENTRY_BAR)

    # --- Slices from ENTRY_BAR onwards
    highs_future = highs_full[ENTRY_BAR:]
    lows_future  = lows_full[ENTRY_BAR:]

    if len(highs_future) == 0:
        return None

    if direction == "bull":
        # Ultimate PM high from entry onwards
        ultimate_future = float(highs_future.max())
        # Full PM ultimate (from 13:00)
        ultimate_full_pm = float(highs_full.max())
        # Remaining excursion (positive = favorable)
        remaining_exc = (ultimate_future - entry_price) / entry_price * 100.0
        full_pm_exc   = (ultimate_full_pm - ref_1300) / ref_1300 * 100.0
    else:  # bear
        ultimate_future  = float(lows_future.min())
        ultimate_full_pm = float(lows_full.min())
        remaining_exc    = (entry_price - ultimate_future) / entry_price * 100.0
        full_pm_exc      = (ref_1300 - ultimate_full_pm) / ref_1300 * 100.0

    return {
        "entry_price":             entry_price,
        "first_extreme_bar":       first_extreme_bar,
        "new_extreme_made":        new_extreme_made,
        "extreme_already_at_entry": extreme_already_at_entry,
        "remaining_excursion_pct": round(remaining_exc, 4),
        "full_pm_extreme_pct":     round(full_pm_exc, 4),
        "am_high":                 am_high,
        "am_low":                  am_low,
    }


# ── Statistics helpers ─────────────────────────────────────────────────────────

def dist_stats(arr: np.ndarray) -> dict:
    return {
        "n":    int(len(arr)),
        "mean": round(float(np.mean(arr)), 4),
        "std":  round(float(np.std(arr)), 4),
        "p10":  round(float(np.percentile(arr, 10)), 4),
        "p25":  round(float(np.percentile(arr, 25)), 4),
        "p50":  round(float(np.percentile(arr, 50)), 4),
        "p75":  round(float(np.percentile(arr, 75)), 4),
        "p90":  round(float(np.percentile(arr, 90)), 4),
    }


def viability_table(arr: np.ndarray, thresholds: list[float]) -> dict:
    out = {}
    for t in thresholds:
        pct = float((arr >= t).mean())
        n   = int((arr >= t).sum())
        out[f"gte_{int(t*100):03d}bp"] = {"pct": round(pct, 4), "n": n}
    return out


# ── Per-state analysis ─────────────────────────────────────────────────────────

def analyse_state(rows: pd.DataFrame, direction: str, label: str) -> dict:
    excursions_all     = []   # all days (including early-extreme)
    excursions_ahead   = []   # only days where extreme still ahead at entry
    excursions_gone    = []   # days where extreme already printed before entry
    excursions_no_ext  = []   # days where no new extreme ever made in PM

    n_loaded = 0
    n_skipped = 0

    for _, row in rows.iterrows():
        d = row["date"]
        if hasattr(d, "date"):
            d = d.date()
        df_1m = load_1m(d)
        if df_1m.empty:
            n_skipped += 1
            continue

        result = compute_excursion(df_1m, direction)
        if result is None:
            n_skipped += 1
            continue

        n_loaded += 1
        exc = result["remaining_excursion_pct"]

        excursions_all.append(exc)

        if not result["new_extreme_made"]:
            excursions_no_ext.append(exc)
        elif result["extreme_already_at_entry"]:
            excursions_gone.append(exc)
        else:
            excursions_ahead.append(exc)

    print(f"\n  Loaded: {n_loaded}  Skipped: {n_skipped}")

    arr_all   = np.array(excursions_all)
    arr_ahead = np.array(excursions_ahead) if excursions_ahead else np.array([])
    arr_gone  = np.array(excursions_gone)  if excursions_gone  else np.array([])
    arr_noext = np.array(excursions_no_ext)if excursions_no_ext else np.array([])

    out = {
        "label":        label,
        "direction":    direction,
        "n_total":      n_loaded,
        "n_extreme_ahead_at_entry":   len(arr_ahead),
        "n_extreme_gone_before_entry": len(arr_gone),
        "n_no_extreme_in_pm":         len(arr_noext),
        "pct_extreme_ahead": round(len(arr_ahead) / max(1, n_loaded), 4),
        "pct_extreme_gone":  round(len(arr_gone)  / max(1, n_loaded), 4),
        "pct_no_extreme":    round(len(arr_noext) / max(1, n_loaded), 4),
    }

    if len(arr_all) > 0:
        out["all_days_dist"]  = dist_stats(arr_all)
        out["all_viability"]  = viability_table(arr_all, TARGET_THRESHOLDS)

    if len(arr_ahead) > 0:
        out["ahead_days_dist"] = dist_stats(arr_ahead)
        out["ahead_viability"] = viability_table(arr_ahead, TARGET_THRESHOLDS)

    if len(arr_gone) > 0:
        out["gone_days_dist"]  = dist_stats(arr_gone)

    return out


# ── Console report ─────────────────────────────────────────────────────────────

def print_section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_dist(label: str, d: dict) -> None:
    print(f"  {label}:")
    print(f"    n={d['n']:4d}  mean={d['mean']:6.3f}%  std={d['std']:6.3f}%")
    print(f"    p10={d['p10']:6.3f}%  p25={d['p25']:6.3f}%  p50={d['p50']:6.3f}%"
          f"  p75={d['p75']:6.3f}%  p90={d['p90']:6.3f}%")


def print_viability(label: str, v: dict) -> None:
    print(f"\n  {label} -- % of days with remaining excursion >= threshold:")
    print(f"  {'Threshold':>12}  {'% Days':>8}  {'N Days':>7}  {'Net of fees (0.04%)':>20}")
    print(f"  {'-'*12}  {'-'*8}  {'-'*7}  {'-'*20}")
    for key, info in v.items():
        bp_str = key.replace("gte_", "").replace("bp", "")
        thresh_pct = int(bp_str) / 100.0
        net = thresh_pct - ROUND_TRIP_COST
        viable = "YES" if net > 0 else "---"
        print(f"  {thresh_pct:>11.2f}%  {info['pct']:>7.1%}  {info['n']:>7d}  "
              f"{net:>+8.3f}% net ({viable})")


def print_state_report(result: dict) -> None:
    label = result["label"]
    n     = result["n_total"]
    pct_ahead = result["pct_extreme_ahead"]
    pct_gone  = result["pct_extreme_gone"]
    pct_noext = result["pct_no_extreme"]

    print(f"\n  Days loaded:          {n}")
    print(f"  Extreme still AHEAD at 13:02:  {result['n_extreme_ahead_at_entry']:3d}  ({pct_ahead:.1%})")
    print(f"  Extreme already GONE before 13:02: {result['n_extreme_gone_before_entry']:3d}  ({pct_gone:.1%})")
    print(f"  No new extreme in PM:  {result['n_no_extreme_in_pm']:3d}  ({pct_noext:.1%})")

    if "all_days_dist" in result:
        print()
        print_dist("All days -- remaining excursion from 13:02 to ultimate PM extreme", result["all_days_dist"])
        print_viability("All days", result["all_viability"])

    if "ahead_days_dist" in result:
        print()
        print_dist("Extreme-ahead days ONLY -- remaining excursion", result["ahead_days_dist"])
        print_viability("Extreme-ahead days", result["ahead_viability"])

    if "gone_days_dist" in result:
        print()
        print_dist("Early-extreme days (extreme before 13:02)", result["gone_days_dist"])
        print(f"  NOTE: These days show remaining excursion AFTER the extreme has passed.")
        print(f"  Positive = market still has runway (extreme not the full PM range).")
        print(f"  Negative = market reversed after early extreme (entering at 13:02 means")
        print(f"             you arrive AFTER the move and face pure mean-reversion.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Diagnose remaining excursion from 13:02 to ultimate PM extreme"
    )
    parser.add_argument("--min-conf", type=float, default=0.70)
    parser.add_argument("--state", choices=["bull", "bear", "both"], default="both")
    args = parser.parse_args()

    print("=" * 60)
    print("REMAINING EXCURSION DIAGNOSTIC -- Phase 5 Pre-flight")
    print("=" * 60)
    print(f"  Entry bar:  {ENTRY_BAR} (13:0{ENTRY_BAR} open)")
    print(f"  Min conf:   {args.min_conf:.0%}")
    print(f"  State:      {args.state}")
    print(f"  Round-trip cost: {ROUND_TRIP_COST:.2f}%")
    print()
    print("  Remaining excursion = (ultimate_PM_extreme_from_entry - entry_price)")
    print("                        / entry_price * 100  [direction-adjusted]")
    print("  Uses ONLY bars >= bar 2 (13:02) for the extreme calculation.")
    print("  Days where extreme printed at bar 0-1 will show negative or low values.")

    raw_df = load_raw(args.min_conf, args.state)
    print(f"\n  Total high-conf days after filter: {len(raw_df)}")

    results = {}

    if args.state in ("bull", "both"):
        bull_rows = raw_df[raw_df["pred_label"] == "BullTrend"].copy()
        print_section(f"BULLTREND  (n={len(bull_rows)} days predicted)")
        bull_result = analyse_state(bull_rows, "bull", "BullTrend")
        print_state_report(bull_result)
        results["BullTrend"] = bull_result

    if args.state in ("bear", "both"):
        bear_rows = raw_df[raw_df["pred_label"] == "BearTrend"].copy()
        print_section(f"BEARTREND  (n={len(bear_rows)} days predicted)")
        bear_result = analyse_state(bear_rows, "bear", "BearTrend")
        print_state_report(bear_result)
        results["BearTrend"] = bear_result

    # ── Architecture verdict ───────────────────────────────────────────────────
    print_section("ARCHITECTURE VERDICT")

    for state_label, res in results.items():
        if "all_viability" not in res:
            continue
        v_all   = res["all_viability"]
        v_ahead = res.get("ahead_viability", {})

        # Key thresholds
        pct_012_all   = v_all.get("gte_012bp", {}).get("pct", 0)
        pct_015_all   = v_all.get("gte_015bp", {}).get("pct", 0)
        pct_018_all   = v_all.get("gte_018bp", {}).get("pct", 0)
        pct_012_ahead = v_ahead.get("gte_012bp", {}).get("pct", 0) if v_ahead else 0
        pct_015_ahead = v_ahead.get("gte_015bp", {}).get("pct", 0) if v_ahead else 0

        p50_all = res["all_days_dist"]["p50"] if "all_days_dist" in res else 0
        p50_ahead = res["ahead_days_dist"]["p50"] if "ahead_days_dist" in res else 0

        print(f"\n  {state_label}:")
        print(f"    p50 remaining excursion (all days):    {p50_all:+.3f}%")
        print(f"    p50 remaining excursion (ahead days):  {p50_ahead:+.3f}%")
        print(f"    % days with >=0.12% remaining (all):   {pct_012_all:.1%}")
        print(f"    % days with >=0.15% remaining (all):   {pct_015_all:.1%}")
        print(f"    % days with >=0.18% remaining (all):   {pct_018_all:.1%}")
        print(f"    % days with >=0.12% remaining (ahead): {pct_012_ahead:.1%}")
        print(f"    % days with >=0.15% remaining (ahead): {pct_015_ahead:.1%}")

        pct_ahead_total = res["pct_extreme_ahead"]
        # For strategy to be viable: need enough days with >=target remaining
        # If p50 (all days) < 0.12%, 13:02 entry is mathematically dead for futures
        if p50_all < 0.08:
            verdict = "DEAD -- p50 remaining < 0.08%. 13:02 entry cannot capture futures moves."
        elif p50_all < 0.12:
            verdict = "MARGINAL -- p50 remaining 0.08-0.12%. Tight targets only (0.08%). High dependency on early entry."
        elif pct_ahead_total < 0.50:
            verdict = "BORDERLINE -- p50 viable but <50% of days have extreme still ahead. Option 1 (13:00 entry) critical."
        elif pct_015_all >= 0.45:
            verdict = "VIABLE -- meaningful % of days can reach 0.15%+ from 13:02. Test tight targets."
        else:
            verdict = "WEAK -- excursion exists but insufficient % of days reach useful targets. Reconsider architecture."

        print(f"    VERDICT: {verdict}")

    # ── Key question ──────────────────────────────────────────────────────────
    print(f"\n  KEY QUESTION: Is architecture salvageable with futures?")
    print(f"  If p50 remaining excursion (all days) < 0.12% -> options edge only")
    print(f"  If p50 remaining excursion (ahead days) >= 0.15% -> Option 1 test warranted")
    print(f"  If pct_extreme_ahead < 45% -> problem is structural, not entry timing")

    # ── Save JSON ──────────────────────────────────────────────────────────────
    # Strip non-serialisable keys before saving
    save_results = {}
    for k, v in results.items():
        save_results[k] = {key: val for key, val in v.items()
                           if key not in ("label", "direction")}

    with open(OUT_JSON, "w") as f:
        json.dump(save_results, f, indent=2)
    print(f"\n  Saved: {OUT_JSON}")

    print("\n" + "=" * 60)
    print("DONE.")
    print("=" * 60)


if __name__ == "__main__":
    main()
