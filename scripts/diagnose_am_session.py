#!/usr/bin/env python3
"""
AM Session Diagnostic — Path A Pre-flight
==========================================
Answers the load-bearing question before committing to AM session architecture:

  "What is the AM session return distribution (9:15 -> 13:00) by day-type cluster,
   and is there a viable drift edge comparable to (or stronger than) the PM finding?"

Four analyses:
  A. AM return by CURRENT DAY cluster (oracle view — upper bound of any AM signal)
  B. AM return by LAGGED cluster (yesterday's label -> today's AM)
     -- This IS feasible live: yesterday's EOD type is known before 9:15
  C. AM sub-window analysis: first 30m / 60m / 90m / full AM per cluster
  D. Gap alignment: gap_dir × cluster agreement vs disagreement

Benchmark: PM hold-to-close showed E=+0.036%/trade for BullTrend.
AM must show p50 >= 0.12% (3× PM) to justify the longer session risk.

Input:  data/features/day_type/cluster_labels.csv
        data/features/day_type/intraday_features_10am.csv
        data/market_data/nse/candles/1m/{date}.duckdb
Output: data/features/day_type/am_session_diagnostic.json
        console report
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FEAT_DIR   = ROOT / "data" / "features" / "day_type"
CANDLE_DIR = ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
SYMBOL     = "NSE_INDEX|Nifty 50"
OUT_JSON   = FEAT_DIR / "am_session_diagnostic.json"

RTC = 0.04  # round-trip cost %

# cluster_id -> label (verified)
CLUSTER_LABEL = {0: "BearTrend", 1: "BullTrend", 2: "Choppy"}
DIRECTION     = {"BullTrend": 1, "BearTrend": -1, "Choppy": 0}


# ── Data loading ────────────────────────────────────────────────────────────────

def load_1m(d) -> pd.DataFrame:
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


def compute_am_metrics(df_1m: pd.DataFrame) -> dict | None:
    """
    Compute AM session metrics from 1m bars.

    AM session: 9:15 (first bar) to end of 12:59 bar (last bar before PM).
    Entry price  = open of first 1m bar (9:15 open).
    Exit price   = close of last AM bar (12:59 close, just before 13:00).
    """
    am_mask = df_1m["timestamp"].dt.hour < 13
    am_df   = df_1m[am_mask].reset_index(drop=True)

    if len(am_df) < 60:   # need at least 60 AM bars
        return None

    entry_price = float(am_df["open"].iloc[0])    # 9:15 open
    if entry_price <= 0:
        return None

    exit_price  = float(am_df["close"].iloc[-1])  # 12:59 close

    # Direction-neutral raw metrics
    highs  = am_df["high"].values.astype(float)
    lows   = am_df["low"].values.astype(float)
    closes = am_df["close"].values.astype(float)

    am_return   = (exit_price - entry_price) / entry_price * 100.0
    am_max_gain = (highs.max() - entry_price) / entry_price * 100.0   # max up
    am_max_loss = (entry_price - lows.min()) / entry_price * 100.0    # max down (positive)
    am_range    = am_max_gain + am_max_loss

    # Sub-window returns (from 9:15 open)
    # 9:15 -> 10:00 = bars 0..44 (45 bars)
    # 9:15 -> 11:00 = bars 0..104 (105 bars)
    # 9:15 -> 12:00 = bars 0..164 (165 bars)
    def ret_at(n: int) -> float | None:
        if len(closes) < n:
            return None
        return (closes[n - 1] - entry_price) / entry_price * 100.0

    return {
        "entry_price":   entry_price,
        "exit_price":    exit_price,
        "am_return":     round(am_return, 4),
        "am_max_gain":   round(am_max_gain, 4),
        "am_max_loss":   round(am_max_loss, 4),
        "am_range":      round(am_range, 4),
        "ret_30m":       round(ret_at(30) or 0, 4),    # 9:15->9:45
        "ret_60m":       round(ret_at(60) or 0, 4),    # 9:15->10:15
        "ret_90m":       round(ret_at(90) or 0, 4),    # 9:15->10:45
        "ret_120m":      round(ret_at(120) or 0, 4),   # 9:15->11:15
    }


# ── Statistics helpers ──────────────────────────────────────────────────────────

def dist_stats(arr: np.ndarray, label: str = "") -> dict:
    if len(arr) == 0:
        return {}
    se   = arr.std() / len(arr) ** 0.5
    tval = arr.mean() / se if se > 0 else 0
    pval = float(stats.ttest_1samp(arr, 0).pvalue / 2)  # one-tailed
    return {
        "n":     int(len(arr)),
        "mean":  round(float(arr.mean()), 4),
        "std":   round(float(arr.std()), 4),
        "p10":   round(float(np.percentile(arr, 10)), 4),
        "p25":   round(float(np.percentile(arr, 25)), 4),
        "p50":   round(float(np.percentile(arr, 50)), 4),
        "p75":   round(float(np.percentile(arr, 75)), 4),
        "p90":   round(float(np.percentile(arr, 90)), 4),
        "t_stat": round(tval, 3),
        "p_val":  round(pval, 4),
    }


def strategy_stats(pnl_arr: np.ndarray) -> dict:
    """WR, E/trade, total, MaxDD, t-stat for a PnL series."""
    if len(pnl_arr) == 0:
        return {}
    pnl   = pd.Series(pnl_arr)
    wins  = pnl[pnl > 0]
    loss  = pnl[pnl <= 0]
    wr    = len(wins) / len(pnl)
    e     = float(pnl.mean())
    tot   = float(pnl.sum())
    cum   = pnl.cumsum()
    maxdd = float((cum - cum.cummax()).min())
    se    = pnl.std() / len(pnl) ** 0.5
    tval  = e / se if se > 0 else 0
    pval  = float(stats.ttest_1samp(pnl, 0).pvalue / 2)
    return {
        "n":            int(len(pnl)),
        "win_rate":     round(wr, 4),
        "avg_win":      round(float(wins.mean()) if len(wins) > 0 else 0, 4),
        "avg_loss":     round(float(loss.mean()) if len(loss) > 0 else 0, 4),
        "expectancy":   round(e, 4),
        "total":        round(tot, 4),
        "max_dd":       round(maxdd, 4),
        "t_stat":       round(tval, 3),
        "p_val":        round(pval, 4),
    }


# ── Main ────────────────────────────────────────────────────────────────────────

def sep(title: str = "") -> None:
    print(f"\n{'='*62}")
    if title:
        print(f"  {title}")
        print(f"{'='*62}")


def main():
    # ── Load cluster labels and intraday features ──────────────────────────────
    cl   = pd.read_csv(FEAT_DIR / "cluster_labels.csv", parse_dates=["date"])
    feat = pd.read_csv(FEAT_DIR / "intraday_features_10am.csv", parse_dates=["date"])

    # Map cluster_id -> label
    cl["label"] = cl["cluster_id"].map(CLUSTER_LABEL)

    # Merge gap features from 10am intraday features
    feat_sub = feat[["date", "gap_pct", "gap_dir"]].copy()
    cl = cl.merge(feat_sub, on="date", how="left")

    # ── Compute AM metrics for every available date ────────────────────────────
    print("Loading 1m bars and computing AM metrics...")
    records = []
    for _, row in cl.iterrows():
        d = row["date"].date()
        df_1m = load_1m(d)
        if df_1m.empty:
            continue
        m = compute_am_metrics(df_1m)
        if m is None:
            continue
        records.append({
            "date":       str(d),
            "year":       d.year,
            "cluster_id": row["cluster_id"],
            "label":      row["label"],
            "gap_pct":    row.get("gap_pct", 0),
            "gap_dir":    row.get("gap_dir", 0),
            **m,
        })

    df = pd.DataFrame(records)
    print(f"  Loaded: {len(df)} days across {df['year'].nunique()} years")

    # Build lagged label (yesterday's EOD cluster -> today's AM)
    df = df.sort_values("date").reset_index(drop=True)
    df["lag_label"]      = df["label"].shift(1)
    df["lag_cluster_id"] = df["cluster_id"].shift(1)

    # ── A. AM RETURN BY CURRENT-DAY CLUSTER (oracle upper bound) ──────────────
    sep("A. AM RETURN BY CURRENT-DAY CLUSTER (oracle view)")
    print("   Entry: 9:15 open | Exit: 12:59 close | Direction: long/short per cluster")
    print("   This is the CEILING: uses EOD label which is only known at 15:30.\n")

    results_a = {}
    for cid in [0, 1, 2]:
        lbl  = CLUSTER_LABEL[cid]
        dirn = DIRECTION[lbl]
        sub  = df[df["cluster_id"] == cid]
        if dirn == 0:
            continue

        # Direction-adjusted AM return
        pnl = (sub["am_return"] * dirn - RTC).values

        d = dist_stats(sub["am_return"].values, lbl)
        s = strategy_stats(pnl)

        print(f"  {lbl} (cluster {cid}, dir={'long' if dirn>0 else 'short'}):")
        print(f"    Raw AM return:  mean={d['mean']:>+6.3f}%  "
              f"p25={d['p25']:>+6.3f}%  p50={d['p50']:>+6.3f}%  "
              f"p75={d['p75']:>+6.3f}%  std={d['std']:>5.3f}%")
        print(f"    Strategy PnL:   n={s['n']}  WR={s['win_rate']:.1%}  "
              f"E={s['expectancy']:>+.4f}%  tot={s['total']:>+.2f}%  "
              f"DD={s['max_dd']:.2f}%  t={s['t_stat']:>+.2f}  p={s['p_val']:.4f}")

        # Year-by-year
        print(f"    Year-by-year:")
        for yr, grp in sub.groupby("year"):
            p = (grp["am_return"] * dirn - RTC).values
            if len(p) < 5:
                continue
            wr = (p > 0).mean()
            e  = p.mean()
            se = p.std() / len(p)**0.5
            t  = e / se if se > 0 else 0
            print(f"      {yr}: n={len(p):3d}  WR={wr:.1%}  E={e:>+.4f}%  "
                  f"tot={p.sum():>+.2f}%  t={t:>+.2f}")

        results_a[lbl] = {"raw_return": d, "strategy": s}

    # ── B. LAGGED CLUSTER: YESTERDAY'S LABEL -> TODAY'S AM ────────────────────
    sep("B. LAGGED CLUSTER (yesterday's EOD type -> today's AM return)")
    print("   This IS feasible live — yesterday's label known before 9:15 open.\n")

    df_lag = df.dropna(subset=["lag_label"]).copy()
    results_b = {}
    for lbl in ["BullTrend", "BearTrend"]:
        dirn = DIRECTION[lbl]
        sub  = df_lag[df_lag["lag_label"] == lbl]
        pnl  = (sub["am_return"] * dirn - RTC).values

        s = strategy_stats(pnl)
        d = dist_stats(sub["am_return"].values * dirn)

        print(f"  Yesterday={lbl} -> Today (dir={'long' if dirn>0 else 'short'}):")
        print(f"    n={s['n']}  WR={s['win_rate']:.1%}  E={s['expectancy']:>+.4f}%  "
              f"tot={s['total']:>+.2f}%  DD={s['max_dd']:.2f}%  "
              f"t={s['t_stat']:>+.2f}  p={s['p_val']:.4f}")
        print(f"    p50 dir-adj return = {d['p50']:>+.4f}%  "
              f"avg_win={s['avg_win']:>+.4f}%  avg_loss={s['avg_loss']:>+.4f}%")

        # Year-by-year
        for yr, grp in sub.groupby("year"):
            p = (grp["am_return"] * dirn - RTC).values
            if len(p) < 5:
                continue
            wr = (p > 0).mean()
            e  = p.mean()
            se = p.std() / len(p)**0.5
            t  = e / se if se > 0 else 0
            print(f"      {yr}: n={len(p):3d}  WR={wr:.1%}  E={e:>+.4f}%  t={t:>+.2f}")

        results_b[lbl] = {"strategy": s}
        print()

    # ── C. SUB-WINDOW ANALYSIS ────────────────────────────────────────────────
    sep("C. SUB-WINDOW DRIFT (BullTrend oracle view only)")
    print("   How quickly does the AM drift accumulate?\n")

    bull = df[df["cluster_id"] == 1]
    windows = [("30m (9:15->9:45)",   "ret_30m",  1),
               ("60m (9:15->10:15)",  "ret_60m",  1),
               ("90m (9:15->10:45)",  "ret_90m",  1),
               ("120m (9:15->11:15)", "ret_120m", 1),
               ("Full AM (->12:59)",  "am_return", 1)]

    print(f"  {'Window':<22}  {'p25':>7}  {'p50':>7}  {'p75':>7}  "
          f"{'WR':>7}  {'E/trade':>9}  {'t-stat':>7}")
    print(f"  {'-'*22}  {'-'*7}  {'-'*7}  {'-'*7}  "
          f"{'-'*7}  {'-'*9}  {'-'*7}")

    for win_label, col, dirn in windows:
        arr  = bull[col].values * dirn
        pnl  = arr - RTC
        wr   = (pnl > 0).mean()
        e    = pnl.mean()
        se   = pnl.std() / len(pnl) ** 0.5
        t    = e / se if se > 0 else 0
        p25, p50, p75 = np.percentile(arr, [25, 50, 75])
        print(f"  {win_label:<22}  {p25:>+6.3f}%  {p50:>+6.3f}%  {p75:>+6.3f}%  "
              f"{wr:>6.1%}  {e:>+8.4f}%  {t:>+6.2f}")

    # BearTrend same
    print()
    bear = df[df["cluster_id"] == 0]
    print(f"  BearTrend (short):")
    print(f"  {'Window':<22}  {'p25':>7}  {'p50':>7}  {'p75':>7}  "
          f"{'WR':>7}  {'E/trade':>9}  {'t-stat':>7}")
    print(f"  {'-'*22}  {'-'*7}  {'-'*7}  {'-'*7}  "
          f"{'-'*7}  {'-'*9}  {'-'*7}")

    for win_label, col, dirn in windows:
        arr  = bear[col].values * -1  # short direction
        pnl  = arr - RTC
        wr   = (pnl > 0).mean()
        e    = pnl.mean()
        se   = pnl.std() / len(pnl) ** 0.5
        t    = e / se if se > 0 else 0
        p25, p50, p75 = np.percentile(arr, [25, 50, 75])
        print(f"  {win_label:<22}  {p25:>+6.3f}%  {p50:>+6.3f}%  {p75:>+6.3f}%  "
              f"{wr:>6.1%}  {e:>+8.4f}%  {t:>+6.2f}")

    # ── D. GAP ALIGNMENT EFFECT ───────────────────────────────────────────────
    sep("D. GAP ALIGNMENT: cluster × gap direction agreement")
    print("   Does gap aligning with cluster direction improve edge?\n")

    for cid, lbl, dirn in [(1, "BullTrend", 1), (0, "BearTrend", -1)]:
        sub = df[df["cluster_id"] == cid].copy()
        sub["gap_aligned"] = (sub["gap_dir"] * dirn) > 0   # gap same dir as trade

        for aligned_flag, tag in [(True, "gap ALIGNED"), (False, "gap AGAINST")]:
            grp = sub[sub["gap_aligned"] == aligned_flag]
            if len(grp) < 10:
                continue
            pnl = (grp["am_return"] * dirn - RTC).values
            wr  = (pnl > 0).mean()
            e   = pnl.mean()
            se  = pnl.std() / len(pnl)**0.5
            t   = e / se if se > 0 else 0
            p50 = np.median(pnl)
            print(f"  {lbl} + {tag:<14}  n={len(grp):3d}  WR={wr:.1%}  "
                  f"E={e:>+.4f}%  p50_pnl={p50:>+.4f}%  t={t:>+.2f}")
        print()

    # ── Summary verdict ────────────────────────────────────────────────────────
    sep("VERDICT: PATH A VIABILITY")
    pm_bull_e   = 0.0357   # baseline PM result
    pm_threshold = 0.10    # must exceed this to justify AM architecture

    print(f"\n  PM BullTrend hold-to-close benchmark: E=+{pm_bull_e:.4f}%/trade")
    print(f"  AM viability threshold:               E>+{pm_threshold:.4f}%/trade")
    print(f"  (AM session is 3.75h vs PM 2.5h — must be proportionally larger)\n")

    for section, label, res_dict in [("A (oracle)", "BullTrend", results_a),
                                      ("A (oracle)", "BearTrend", results_a),
                                      ("B (lagged)", "BullTrend", results_b),
                                      ("B (lagged)", "BearTrend", results_b)]:
        r = res_dict.get(label, {})
        if not r:
            continue
        s = r.get("strategy", {})
        e = s.get("expectancy", 0)
        t = s.get("t_stat", 0)
        viable = "VIABLE" if e > pm_threshold and t > 1.5 else (
                 "WEAK"   if e > pm_bull_e else "NO EDGE")
        print(f"  {section} {label:<12}:  E={e:>+.4f}%  t={t:>+.2f}  -> {viable}")

    # ── Save JSON ──────────────────────────────────────────────────────────────
    with open(OUT_JSON, "w") as f:
        json.dump({"oracle": results_a, "lagged": results_b}, f, indent=2)
    print(f"\n  Saved: {OUT_JSON}")

    sep("DONE")


if __name__ == "__main__":
    main()
