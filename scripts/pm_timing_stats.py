#!/usr/bin/env python3
"""
PM Timing Analysis — Phase 4.5
================================
For high-confidence Bull and Bear predictions at 13:00, characterise the
intra-PM path structure. These statistics are load-bearing for Phase 5
strategy architecture: they determine whether immediate entry, pullback
entry, or delayed-confirmation entry is structurally sound.

Six output blocks:

  1. TIMING DISTRIBUTION     -- when does the first new-day extreme print?
  2. PRE-EXTREME ADVERSE     -- heat taken from 13:00 before the extreme
  3. STOP SURVIVABILITY      -- at each stop size, % of days that survive
                                 to the first new extreme without being stopped
  4. POST-EXTREME GIVEBACK   -- how much of the ultimate extreme is returned
                                 to close (the mean-reversion problem)
  5. ENTRY TIMING CURVE      -- opportunity remaining vs entry delay
                                 (empirical answer to: is 13pm the right entry?)
  6. FAILURE DAY PROFILE     -- on days where no new extreme prints, what happens?

Input:  data/features/day_type/pm_expectancy_raw.csv  (Phase 4 output)
        data/market_data/nse/candles/1m/{date}.duckdb
Output: data/features/day_type/pm_timing_stats.json
        console report

Usage:
  python scripts/pm_timing_stats.py
  python scripts/pm_timing_stats.py --state bull      # bull only
  python scripts/pm_timing_stats.py --state bear
  python scripts/pm_timing_stats.py --min-conf 0.75   # stricter conf filter
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

RAW_CSV     = FEATURE_DIR / "pm_expectancy_raw.csv"
OUT_JSON    = FEATURE_DIR / "pm_timing_stats.json"

# Stop sizes to test for survivability (as % of ref price)
STOP_SIZES = [0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30]

# Entry delay bars to test for opportunity curve (bars from 13:00 = minutes)
ENTRY_DELAY_BARS = [0, 5, 10, 15, 20, 30, 45, 60, 75, 90, 105, 120]


# ── Data loading ───────────────────────────────────────────────────────────────

def load_raw() -> pd.DataFrame:
    if not RAW_CSV.exists():
        raise FileNotFoundError(
            f"Missing: {RAW_CSV}\nRun: python scripts/run_pm_expectancy.py"
        )
    return pd.read_csv(RAW_CSV, parse_dates=["date"])


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


# ── Path analysis ──────────────────────────────────────────────────────────────

def analyse_path(df_1m: pd.DataFrame, direction: str) -> dict | None:
    """
    Compute intra-PM path statistics for one day.

    direction: 'bull' → tracking new day HIGH break
               'bear' → tracking new day LOW  break

    Returns dict of path stats, or None if insufficient data.
    """
    pm_mask = df_1m["timestamp"].dt.hour >= 13
    am_df   = df_1m[~pm_mask]
    pm_df   = df_1m[pm_mask].reset_index(drop=True)

    if len(pm_df) < 30 or am_df.empty:
        return None

    ref_price = float(pm_df["open"].iloc[0])   # 13:00 open
    if ref_price <= 0:
        return None

    am_high = float(am_df["high"].max())
    am_low  = float(am_df["low"].min())

    closes = pm_df["close"].values.astype(float)
    highs  = pm_df["high"].values.astype(float)
    lows   = pm_df["low"].values.astype(float)
    n_bars = len(pm_df)

    final_close = float(closes[-1])

    if direction == "bull":
        # First bar in PM where high > AM high (new day high break)
        new_extreme_mask = highs > am_high
        extreme_label    = "new_day_high"
        ultimate_extreme = float(highs.max())   # ultimate PM high
    else:  # bear
        new_extreme_mask = lows < am_low
        extreme_label    = "new_day_low"
        ultimate_extreme = float(lows.min())    # ultimate PM low

    new_extreme_made = bool(new_extreme_mask.any())

    if new_extreme_made:
        first_extreme_bar = int(np.argmax(new_extreme_mask))  # 0-indexed PM bar

        # Pre-extreme adverse: max adverse move from 13:00 open before first extreme
        window_lows  = lows[:first_extreme_bar + 1]
        window_highs = highs[:first_extreme_bar + 1]
        if direction == "bull":
            pre_adverse = max(0.0, ref_price - float(window_lows.min())) / ref_price * 100
        else:
            pre_adverse = max(0.0, float(window_highs.max()) - ref_price) / ref_price * 100

        # Post-extreme giveback: (ultimate extreme - final close) as % of ref
        # For bull: ultimate high - close (positive = gave back gains)
        # For bear: close - ultimate low (positive = gave back losses)
        if direction == "bull":
            giveback = (ultimate_extreme - final_close) / ref_price * 100
        else:
            giveback = (final_close - ultimate_extreme) / ref_price * 100
        giveback = max(0.0, giveback)   # negative = kept going, clip to 0

        # Minutes from 13:00 to first extreme
        first_extreme_min = first_extreme_bar  # bar index = minute offset (1m bars)

        # How much did market move FROM first extreme bar to ultimate extreme?
        if direction == "bull":
            continuation_after_first = (ultimate_extreme - float(highs[first_extreme_bar])) / ref_price * 100
        else:
            continuation_after_first = (float(lows[first_extreme_bar]) - ultimate_extreme) / ref_price * 100
        continuation_after_first = max(0.0, continuation_after_first)

        # Success days: failure_max_adverse is not applicable
        failure_max_adverse = 0.0

    else:
        # No new extreme — characterise the failure
        first_extreme_bar        = -1
        first_extreme_min        = -1
        pre_adverse              = 0.0
        continuation_after_first = 0.0
        giveback                 = 0.0

        # Max adverse move on failure days (the unhedged loss)
        if direction == "bull":
            failure_max_adverse = max(0.0, ref_price - float(lows.min())) / ref_price * 100
        else:
            failure_max_adverse = max(0.0, float(highs.max()) - ref_price) / ref_price * 100

    return {
        "new_extreme_made":         new_extreme_made,
        "first_extreme_bar":        first_extreme_bar,    # -1 if not made
        "first_extreme_min":        first_extreme_min,    # minutes after 13:00
        "pre_extreme_adverse_pct":  pre_adverse,          # % adverse before extreme
        "post_extreme_giveback_pct": giveback,            # % given back after extreme
        "continuation_pct":         continuation_after_first,
        "failure_max_adverse_pct":  failure_max_adverse,  # only if no extreme
        "n_pm_bars":                n_bars,
        "ultimate_extreme_pct":     abs(ultimate_extreme - ref_price) / ref_price * 100,
    }


# ── Aggregation ────────────────────────────────────────────────────────────────

def dist(series: pd.Series, pcts=(10, 25, 50, 75, 90)) -> dict:
    s = series.dropna()
    if len(s) == 0:
        return {}
    out = {"n": int(len(s)), "mean": round(float(s.mean()), 4),
           "std": round(float(s.std()), 4)}
    for p in pcts:
        out[f"p{p}"] = round(float(s.quantile(p / 100)), 4)
    return out


def survivability_table(pre_adverse: pd.Series) -> dict:
    """At each stop size, % of days where pre-extreme adverse <= stop (survives)."""
    out = {}
    for s in STOP_SIZES:
        pct_survive = float((pre_adverse <= s).mean())
        out[f"stop_{int(s*100)}bp"] = round(pct_survive, 4)
    return out


def entry_timing_curve(first_extreme_bars: pd.Series, n_total: int) -> dict:
    """
    At each potential entry delay T (bars from 13:00):
      opportunity_remaining = % of all days (incl. no-extreme days) where first extreme > T
      opportunity_of_extreme_days = % of extreme-made days where first extreme > T
    """
    out = {}
    extreme_made = first_extreme_bars[first_extreme_bars >= 0]
    for t in ENTRY_DELAY_BARS:
        # Of all days (including failure days)
        remaining_all = float((first_extreme_bars > t).sum()) / max(n_total, 1)
        # Of extreme-made days only
        remaining_ext = float((extreme_made > t).mean()) if len(extreme_made) > 0 else 0.0
        out[f"bar_{t:03d}"] = {
            "entry_minute_after_13pm": t,
            "opportunity_pct_all_days":      round(remaining_all, 4),
            "opportunity_pct_extreme_days":  round(remaining_ext, 4),
        }
    return out


# ── Report printing ────────────────────────────────────────────────────────────

def print_block(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def print_timing_report(label: str, records: list[dict], direction: str) -> None:
    df = pd.DataFrame(records)
    made   = df[df["new_extreme_made"] == True]
    failed = df[df["new_extreme_made"] == False]
    n_all  = len(df)
    n_made = len(made)

    print_block(f"{label.upper()} ({direction.upper()})  n={n_all}")
    print(f"  New-extreme rate: {n_made}/{n_all} = {n_made/n_all:.1%}")

    # 1. Timing distribution
    if n_made > 0:
        print(f"\n  [1] TIMING — bar index of first new extreme (0 = 13:00)")
        ft = made["first_extreme_min"]
        print(f"      mean={ft.mean():.1f} min   p10={ft.quantile(0.10):.0f}   "
              f"p25={ft.quantile(0.25):.0f}   p50={ft.quantile(0.50):.0f}   "
              f"p75={ft.quantile(0.75):.0f}   p90={ft.quantile(0.90):.0f}")

        windows = [(0,30,"0-30 min (13:00-13:30)"),
                   (30,60,"30-60 min (13:30-14:00)"),
                   (60,90,"60-90 min (14:00-14:30)"),
                   (90,150,"90+ min (14:30-15:30)")]
        for lo, hi, label_w in windows:
            n_w = int(((ft >= lo) & (ft < hi)).sum())
            print(f"      {label_w:<32}  {n_w:>4} days  ({n_w/n_made:.1%})")

        # 2. Pre-extreme adverse
        print(f"\n  [2] PRE-EXTREME ADVERSE EXCURSION (heat before first new extreme)")
        pa = made["pre_extreme_adverse_pct"]
        print(f"      mean={pa.mean():.3f}%  p25={pa.quantile(0.25):.3f}%  "
              f"p50={pa.quantile(0.50):.3f}%  p75={pa.quantile(0.75):.3f}%  "
              f"p90={pa.quantile(0.90):.3f}%")

        # 3. Stop survivability
        print(f"\n  [3] STOP SURVIVABILITY (% of extreme-made days where stop NOT hit before extreme)")
        for s in STOP_SIZES:
            pct = float((pa <= s).mean())
            bar = "#" * int(pct * 40)
            print(f"      stop {s*100:.0f}bp  {pct:.1%}  {bar}")

        # 4. Post-extreme giveback
        print(f"\n  [4] POST-EXTREME GIVEBACK (% of ultimate extreme returned to close)")
        pg = made["post_extreme_giveback_pct"]
        ult = made["ultimate_extreme_pct"]
        print(f"      ultimate extreme p50={ult.quantile(0.50):.3f}%  p75={ult.quantile(0.75):.3f}%")
        print(f"      giveback  mean={pg.mean():.3f}%  p25={pg.quantile(0.25):.3f}%  "
              f"p50={pg.quantile(0.50):.3f}%  p75={pg.quantile(0.75):.3f}%")
        pct_full_giveback = float((pg >= ult).mean())
        print(f"      % days that fully give back extreme: {pct_full_giveback:.1%}")

        # 5. Entry timing curve
        print(f"\n  [5] ENTRY TIMING CURVE (if you wait T minutes to enter after 13:00)")
        print(f"      {'Wait':>6}  {'Opp (all days)':>16}  {'Opp (extreme days)':>20}")
        first_ext_bars = made["first_extreme_bar"]
        all_bars = df["first_extreme_bar"]   # -1 for no-extreme days
        for t in ENTRY_DELAY_BARS:
            rem_all = float((all_bars > t).sum()) / n_all
            rem_ext = float((first_ext_bars > t).mean())
            print(f"      +{t:>4}m  {rem_all:>15.1%}  {rem_ext:>19.1%}")

    # 6. Failure day profile
    if len(failed) > 0:
        print(f"\n  [6] FAILURE DAYS (no new extreme printed)  n={len(failed)}")
        fa = failed["failure_max_adverse_pct"]
        print(f"      Max adverse on failure days: mean={fa.mean():.3f}%  "
              f"p50={fa.quantile(0.50):.3f}%  p75={fa.quantile(0.75):.3f}%")
        ret_col = "pm_return" if "pm_return" in df.columns else None
        if ret_col:
            fail_ret = failed[ret_col]
            print(f"      PM return on failure days: mean={fail_ret.mean():.3f}%  "
                  f"p50={fail_ret.quantile(0.50):.3f}%")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PM path timing analysis")
    parser.add_argument("--state", choices=["bull", "bear", "both"], default="both")
    parser.add_argument("--min-conf", type=float, default=0.70,
                        help="Minimum confidence (default=0.70 = high-conf only)")
    args = parser.parse_args()

    raw = load_raw()

    # Apply conf filter
    raw = raw[raw["confidence"] >= args.min_conf].copy()
    print(f"Days after conf>={args.min_conf}: {len(raw)}  "
          f"(bull={( raw['pred_label']=='BullTrend').sum()}, "
          f"bear={(raw['pred_label']=='BearTrend').sum()})")

    states_to_run = []
    if args.state in ("bull", "both"):
        states_to_run.append(("BullTrend", "bull"))
    if args.state in ("bear", "both"):
        states_to_run.append(("BearTrend", "bear"))

    all_results = {}

    for pred_label, direction in states_to_run:
        subset = raw[raw["pred_label"] == pred_label].copy()
        if subset.empty:
            continue

        # Load 1m bars and compute path stats
        records = []
        skipped = 0
        for _, row in subset.iterrows():
            d = row["date"]
            df_1m = load_1m(d.date() if hasattr(d, "date") else d)
            if df_1m.empty:
                skipped += 1
                continue
            path = analyse_path(df_1m, direction)
            if path is None:
                skipped += 1
                continue
            rec = path.copy()
            rec["date"]      = str(d)[:10]
            rec["year"]      = int(str(d)[:4])
            rec["confidence"] = float(row["confidence"])
            rec["correct"]   = row.get("correct", None)
            rec["pm_return"] = float(row.get("pm_return", 0))
            records.append(rec)

        if skipped:
            print(f"  [{pred_label}] skipped {skipped} days (missing data)")

        # Print report
        print_timing_report(pred_label, records, direction)

        # Build JSON block
        df_r = pd.DataFrame(records)
        made   = df_r[df_r["new_extreme_made"] == True]
        failed = df_r[df_r["new_extreme_made"] == False]

        block = {
            "n_total":           len(df_r),
            "n_extreme_made":    len(made),
            "extreme_rate":      round(len(made) / max(len(df_r), 1), 4),
            "timing_dist":       dist(made["first_extreme_min"]) if len(made) > 0 else {},
            "pre_extreme_adverse": dist(made["pre_extreme_adverse_pct"]) if len(made) > 0 else {},
            "post_extreme_giveback": dist(made["post_extreme_giveback_pct"]) if len(made) > 0 else {},
            "ultimate_extreme_pct":  dist(made["ultimate_extreme_pct"]) if len(made) > 0 else {},
            "continuation_pct":      dist(made["continuation_pct"]) if len(made) > 0 else {},
            "stop_survivability":    survivability_table(made["pre_extreme_adverse_pct"]) if len(made) > 0 else {},
            "entry_timing_curve":    entry_timing_curve(
                pd.concat([made["first_extreme_bar"],
                           pd.Series([-1] * len(failed))]).reset_index(drop=True),
                len(df_r)
            ),
            "failure_profile": {
                "n": len(failed),
                "max_adverse": dist(failed["failure_max_adverse_pct"]) if len(failed) > 0 else {},
                "pm_return":   dist(pd.Series([r["pm_return"] for r in records
                                               if not r["new_extreme_made"]])) if len(failed) > 0 else {},
            },
        }

        # Year-by-year extreme rate
        yoy = {}
        for yr in sorted(df_r["year"].unique()):
            yr_df = df_r[df_r["year"] == yr]
            yr_made = yr_df["new_extreme_made"].sum()
            yoy[int(yr)] = {
                "n": len(yr_df),
                "extreme_rate": round(float(yr_made / max(len(yr_df), 1)), 4),
                "pre_adverse_p50": round(float(yr_df[yr_df["new_extreme_made"]]["pre_extreme_adverse_pct"].median()), 4)
                if yr_made > 0 else None,
                "first_extreme_p50": round(float(yr_df[yr_df["new_extreme_made"]]["first_extreme_min"].median()), 1)
                if yr_made > 0 else None,
            }
        block["year_on_year"] = yoy

        all_results[pred_label] = block

    # Save JSON
    with open(OUT_JSON, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved -> {OUT_JSON}")

    # Print architecture verdict
    for pred_label, direction in states_to_run:
        if pred_label not in all_results:
            continue
        b = all_results[pred_label]
        if not b["timing_dist"]:
            continue

        print(f"\n{'='*70}")
        print(f"  PHASE 5 ARCHITECTURE CONSTRAINTS  [{pred_label}]")
        print(f"{'='*70}")

        t_p50 = b["timing_dist"].get("p50", "?")
        t_p75 = b["timing_dist"].get("p75", "?")
        pa_p50 = b["pre_extreme_adverse"].get("p50", "?")
        pa_p75 = b["pre_extreme_adverse"].get("p75", "?")
        pg_p50 = b["post_extreme_giveback"].get("p50", "?")
        surv_12 = b["stop_survivability"].get("stop_12bp", "?")
        surv_15 = b["stop_survivability"].get("stop_15bp", "?")
        surv_20 = b["stop_survivability"].get("stop_20bp", "?")

        print(f"  Extreme timing:      median={t_p50:.0f} min, p75={t_p75:.0f} min after 13:00")
        print(f"  Pre-extreme adverse: p50={pa_p50:.3f}%  p75={pa_p75:.3f}%")
        print(f"  Stop survivability:  12bp={surv_12:.1%}  15bp={surv_15:.1%}  20bp={surv_20:.1%}")
        print(f"  Post-extreme giveback: p50={pg_p50:.3f}%")

        # Entry model recommendation
        if t_p50 <= 20:
            timing_verdict = "EARLY (< 20m) -- pullback entry likely misses most extremes"
        elif t_p50 <= 50:
            timing_verdict = "MID-SESSION (20-50m) -- both entry models viable"
        else:
            timing_verdict = "LATE (50m+) -- pullback/confirmation model has time to work"

        if pa_p75 <= 0.12:
            stop_verdict = "TIGHT STOP viable (p75 adverse <= 12bp)"
        elif pa_p75 <= 0.20:
            stop_verdict = "MEDIUM STOP required (12-20bp)"
        else:
            stop_verdict = "WIDE STOP required (> 20bp) -- immediate entry fragile"

        print(f"\n  Timing:  {timing_verdict}")
        print(f"  Stop:    {stop_verdict}")


if __name__ == "__main__":
    main()
