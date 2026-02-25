#!/usr/bin/env python3
"""
Phase 5 Backtest — PM Impulse Capture Strategy (v9)
=====================================================
Backtest of immediate-entry PM strategy on high-confidence
BearTrend and BullTrend predictions at 13:00.

Strategy architecture (derived from timing analysis, not optimized here):
  Entry:      Configurable bar (default: bar 2 = 13:02 open; use --entry-bar 0 for 13:00)
  Direction:  Short for BearTrend, Long for BullTrend
  Stop:       Hard stop from entry price
  Target:     Fixed % target (or None with --no-target to hold until time exit)
  Time exit:  Close of bar at T minutes after 13:00
  Filter:     High-confidence predictions only (>= 0.70)

Three grids:
  Original:  stop=[0.15,0.18,0.20], target=[0.18,0.22,0.25], time=[35,45]   (18 combos)
  Tight:     stop=[0.08,0.10,0.12,0.15], target=[0.10,0.12,0.15,0.18],       (only pos R:R)
             time=[25,35,45]  — only combos where target >= stop
  Extended:  stop=[0.15,0.20,0.25,0.30], target=[0.30,0.40,0.50] (or --no-target),
             time=[75,105,135]  — 14:15, 14:45, 15:15 exits to capture full PM trend

--no-target mode: removes fixed target cap entirely; position held until stop or time exit.
  Best used with --extended to test "let BullTrend PM run to close" hypothesis.

--conf-sweep: runs the selected grid at min_conf=0.70, 0.75, 0.80 in one report,
  making it easy to see how edge concentration changes with confidence threshold.

Cost model (Nifty 50 futures, conservative):
  Round-trip: 0.04% of notional
  Covers: spread (0.01% x2) + STT + brokerage + exchange + SEBI + GST

Walk-forward: results reported year-by-year. Parameters are fixed from
timing analysis — no optimization is performed on the historical data.

Usage:
  python scripts/run_v9_backtest.py                                    # 13:02 entry, original grid
  python scripts/run_v9_backtest.py --entry-bar 0                      # 13:00 entry
  python scripts/run_v9_backtest.py --tight-grid                       # tight stop/target grid
  python scripts/run_v9_backtest.py --extended                         # hold up to 15:15
  python scripts/run_v9_backtest.py --extended --no-target             # stop only, hold to 15:15
  python scripts/run_v9_backtest.py --extended --no-target --state bull # BullTrend only
  python scripts/run_v9_backtest.py --extended --conf-sweep            # 0.70/0.75/0.80 in one run
  python scripts/run_v9_backtest.py --full-grid                        # all combos in chosen grid
  python scripts/run_v9_backtest.py --stop 0.20 --no-target --time-exit 135
  python scripts/run_v9_backtest.py --min-conf 0.75 --extended --state bull

Input:  data/features/day_type/pm_expectancy_raw.csv
        data/market_data/nse/candles/1m/{date}.duckdb
Output: data/features/day_type/v9_trades.csv
        console report
"""

from __future__ import annotations

import argparse
import sys
from itertools import product
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FEATURE_DIR = ROOT / "data" / "features" / "day_type"
CANDLE_DIR  = ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
SYMBOL      = "NSE_INDEX|Nifty 50"

RAW_CSV    = FEATURE_DIR / "pm_expectancy_raw.csv"
TRADES_CSV = FEATURE_DIR / "v9_trades.csv"

# Derived from timing analysis — not to be optimized on the test data
STOP_GRID        = [0.15, 0.18, 0.20]              # % from entry (original)
TARGET_GRID      = [0.18, 0.22, 0.25]              # % from entry (original)
TIME_EXIT_GRID   = [35, 45]                        # minutes after 13:00

# Tight grid — recalibrated from remaining excursion diagnostic (Feb 2026)
STOP_GRID_TIGHT   = [0.08, 0.10, 0.12, 0.15]      # tighter stops
TARGET_GRID_TIGHT = [0.10, 0.12, 0.15, 0.18]      # tighter targets
TIME_EXIT_TIGHT   = [25, 35, 45]                   # add 25m option

# Extended grid — Path A enhancement (Feb 2026): hold up to 15:15 to capture full PM trend
# Hypothesis: BullTrend days trend all PM (81.2% new-day-high rate); fixed 35-45m target
# exits too early. Wider stops needed to survive intraday noise on longer hold.
STOP_GRID_EXTENDED   = [0.15, 0.20, 0.25, 0.30]   # wider stops for longer hold
TARGET_GRID_EXTENDED = [0.30, 0.40, 0.50]          # higher targets (or use --no-target)
TIME_EXIT_EXTENDED   = [75, 105, 135]              # 14:15, 14:45, 15:15 exits

ROUND_TRIP_COST  = 0.04                            # % of notional, all-in

DIRECTION = {"BullTrend": "long", "BearTrend": "short"}


# ── Data loading ───────────────────────────────────────────────────────────────

def load_predictions(min_conf: float, state_filter: str,
                     raw_csv: Path | None = None) -> pd.DataFrame:
    csv_path = raw_csv if raw_csv else RAW_CSV
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing: {csv_path}\nRun: python scripts/run_pm_expectancy.py")
    raw = pd.read_csv(csv_path, parse_dates=["date"])
    raw = raw[raw["confidence"] >= min_conf]
    if state_filter == "bear":
        raw = raw[raw["pred_label"] == "BearTrend"]
    elif state_filter == "bull":
        raw = raw[raw["pred_label"] == "BullTrend"]
    else:
        raw = raw[raw["pred_label"].isin(["BullTrend", "BearTrend"])]
    return raw.sort_values("date").reset_index(drop=True)


def load_pm_bars(d) -> pd.DataFrame:
    """Load PM session (13:00+) 1m bars for date d."""
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
        df = df.sort_values("timestamp").reset_index(drop=True)
        # Return PM session only (13:00+)
        pm_mask = df["timestamp"].dt.hour >= 13
        return df[pm_mask].reset_index(drop=True)
    except Exception:
        return pd.DataFrame()


# ── Trade execution ────────────────────────────────────────────────────────────

def execute_trade(
    pm_df: pd.DataFrame,
    direction: str,
    stop_pct: float,
    target_pct: float | None,
    time_exit_bars: int,
    rtc: float = ROUND_TRIP_COST,
    entry_bar: int = 2,
) -> dict | None:
    """
    Simulate one trade on the PM 1m bars.

    pm_df:          1m bars starting from 13:00 (pm_bar 0)
    direction:      'long' or 'short'
    stop_pct:       stop distance as % (e.g. 0.15 means 0.15%)
    target_pct:     target distance as % (e.g. 0.22 means 0.22%), or None for no fixed target.
                    When None, position is held until stop or time exit — useful for
                    capturing the full PM trend on BullTrend/BearTrend days.
    time_exit_bars: max bars to hold (0=13:00, 35=13:35, 135=15:15, etc.)
    rtc:            round-trip cost as % of notional
    entry_bar:      which PM bar's open to use for entry (0=13:00, 2=13:02)

    Returns dict with pnl_pct, exit_reason, etc., or None if insufficient bars.
    """
    # Need at least entry_bar+1 bars
    if len(pm_df) <= entry_bar:
        return None

    entry_price = float(pm_df["open"].iloc[entry_bar])
    if entry_price <= 0:
        return None

    sp = stop_pct / 100.0

    if direction == "long":
        stop_p = entry_price * (1 - sp)
        tgt_p  = entry_price * (1 + target_pct / 100.0) if target_pct is not None else None
    else:
        stop_p = entry_price * (1 + sp)
        tgt_p  = entry_price * (1 - target_pct / 100.0) if target_pct is not None else None

    max_bar = min(time_exit_bars, len(pm_df) - 1)

    for i in range(entry_bar, max_bar + 1):
        hi = float(pm_df["high"].iloc[i])
        lo = float(pm_df["low"].iloc[i])
        cl = float(pm_df["close"].iloc[i])

        if direction == "long":
            stop_hit = lo <= stop_p
            tgt_hit  = (tgt_p is not None) and (hi >= tgt_p)
        else:
            stop_hit = hi >= stop_p
            tgt_hit  = (tgt_p is not None) and (lo <= tgt_p)

        if stop_hit and tgt_hit:
            # Both hit in same bar: conservative — stop fills
            exit_p = stop_p
            reason = "stop"
        elif stop_hit:
            exit_p = stop_p
            reason = "stop"
        elif tgt_hit:
            exit_p = tgt_p
            reason = "target"
        elif i == max_bar:
            # Time exit at close of final bar
            exit_p = cl
            reason = "time_exit"
        else:
            continue

        if direction == "long":
            gross = (exit_p - entry_price) / entry_price * 100
        else:
            gross = (entry_price - exit_p) / entry_price * 100
        net = round(gross - rtc, 5)

        return {
            "exit_reason":  reason,
            "pnl_pct":      net,
            "exit_bar":     i,
            "entry_price":  entry_price,
            "exit_price":   round(exit_p, 2),
        }

    # Fallback (should be covered by i == max_bar branch above)
    exit_p = float(pm_df["close"].iloc[max_bar])
    if direction == "long":
        gross = (exit_p - entry_price) / entry_price * 100
    else:
        gross = (entry_price - exit_p) / entry_price * 100
    return {
        "exit_reason": "time_exit",
        "pnl_pct":     round(gross - rtc, 5),
        "exit_bar":    max_bar,
        "entry_price": entry_price,
        "exit_price":  round(exit_p, 2),
    }


# ── Metrics ────────────────────────────────────────────────────────────────────

def compute_metrics(pnl: pd.Series) -> dict:
    pnl = pnl.dropna()
    if len(pnl) == 0:
        return {}

    wins   = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    n      = len(pnl)

    win_rate  = len(wins) / n
    avg_win   = float(wins.mean())  if len(wins)   > 0 else 0.0
    avg_loss  = float(losses.mean()) if len(losses) > 0 else 0.0   # negative
    expect    = win_rate * avg_win + (1 - win_rate) * avg_loss      # avg_loss is negative

    cum   = pnl.cumsum()
    peak  = cum.cummax()
    dd    = cum - peak
    max_dd = float(dd.min())

    # Sharpe: annualized using daily trade PnL (not calendar-day PnL)
    sharpe = float((pnl.mean() / pnl.std()) * np.sqrt(252)) if pnl.std() > 0 else 0.0

    max_consec_loss = 0
    cur = 0
    for p in pnl:
        if p <= 0:
            cur += 1
            max_consec_loss = max(max_consec_loss, cur)
        else:
            cur = 0

    return {
        "n_trades":          n,
        "win_rate":          round(win_rate, 4),
        "avg_win_pct":       round(avg_win, 4),
        "avg_loss_pct":      round(avg_loss, 4),
        "expectancy_pct":    round(expect, 4),
        "total_return_pct":  round(float(pnl.sum()), 4),
        "max_dd_pct":        round(max_dd, 4),
        "max_consec_loss":   max_consec_loss,
        "sharpe":            round(sharpe, 3),
    }


# ── Report helpers ─────────────────────────────────────────────────────────────

def hdr(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def print_metrics_row(label: str, m: dict, prefix: str = "  ") -> None:
    if not m:
        print(f"{prefix}{label:<20}  [no trades]")
        return
    print(
        f"{prefix}{label:<20}  "
        f"n={m['n_trades']:<4}  "
        f"WR={m['win_rate']:.1%}  "
        f"E={m['expectancy_pct']:>+.3f}%  "
        f"tot={m['total_return_pct']:>+.2f}%  "
        f"DD={m['max_dd_pct']:.2f}%  "
        f"CL={m['max_consec_loss']}  "
        f"Sharpe={m['sharpe']:.2f}"
    )


def print_exit_breakdown(trades_df: pd.DataFrame) -> None:
    reasons = trades_df["exit_reason"].value_counts()
    n = len(trades_df)
    for r in ["stop", "target", "time_exit"]:
        cnt = reasons.get(r, 0)
        pnl_r = trades_df[trades_df["exit_reason"] == r]["pnl_pct"]
        avg_p  = pnl_r.mean() if len(pnl_r) > 0 else 0.0
        print(f"    {r:<12}  {cnt:>4} ({cnt/n:.1%})  avg_pnl={avg_p:>+.3f}%")


# ── Confidence sweep helper ────────────────────────────────────────────────────

def _run_conf_sweep(args, eb: int, entry_label: str,
                    raw_csv: Path | None = None) -> None:
    """Run the selected grid at conf=0.70, 0.75, 0.80 and print side-by-side."""
    CONF_LEVELS = [0.70, 0.75, 0.80]

    # Determine the single representative combo for the sweep
    no_target = args.no_target
    if args.stop is not None and args.time_exit is not None:
        stop_pct   = args.stop
        target_pct = None if no_target else (args.target if args.target is not None else 0.22)
        tex        = args.time_exit
    elif args.extended:
        stop_pct   = args.stop   or 0.20
        target_pct = None if no_target else (args.target or 0.40)
        tex        = args.time_exit or 135
    elif args.tight_grid:
        stop_pct   = args.stop   or 0.12
        target_pct = None if no_target else (args.target or 0.15)
        tex        = args.time_exit or 35
    else:
        stop_pct   = args.stop   or 0.15
        target_pct = None if no_target else (args.target or 0.22)
        tex        = args.time_exit or 35

    tgt_disp = "none" if target_pct is None else f"{target_pct}%"
    hdr(f"CONFIDENCE SWEEP  stop={stop_pct}%  target={tgt_disp}  "
        f"time_exit={tex}m  entry={entry_label}  [{args.state.upper()}]")

    print(f"  {'Conf':>6}  {'N':>5}  {'Bear_N':>7}  {'Bull_N':>7}  "
          f"{'WR':>7}  {'Expect%':>8}  {'TotRet%':>8}  {'MaxDD%':>7}  {'Sharpe':>7}")
    print(f"  {'-'*6}  {'-'*5}  {'-'*7}  {'-'*7}  "
          f"{'-'*7}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*7}")

    for conf in CONF_LEVELS:
        preds = load_predictions(conf, args.state, raw_csv=raw_csv)
        trades = run_param_combo(preds, stop_pct, target_pct, tex, entry_bar=eb)
        if trades.empty:
            print(f"  {conf:>5.2f}   [no trades]")
            continue
        m = compute_metrics(trades["pnl_pct"])
        n_bear = (trades["state"] == "BearTrend").sum()
        n_bull = (trades["state"] == "BullTrend").sum()
        print(
            f"  {conf:>5.2f}   "
            f"{m['n_trades']:>5}  {n_bear:>7}  {n_bull:>7}  "
            f"{m['win_rate']:>6.1%}  "
            f"{m['expectancy_pct']:>+7.3f}%  {m['total_return_pct']:>+7.2f}%  "
            f"{m['max_dd_pct']:>7.2f}%  {m['sharpe']:>7.2f}"
        )

    # Per-state breakdown at each conf level
    print()
    hdr(f"CONF SWEEP — PER STATE BREAKDOWN")
    for conf in CONF_LEVELS:
        preds = load_predictions(conf, args.state, raw_csv=raw_csv)
        trades = run_param_combo(preds, stop_pct, target_pct, tex, entry_bar=eb)
        if trades.empty:
            continue
        print(f"\n  conf >= {conf:.2f}  (n={len(trades)})")
        for state in ["BearTrend", "BullTrend"]:
            st = trades[trades["state"] == state]
            if len(st) >= 3:
                m = compute_metrics(st["pnl_pct"])
                print_metrics_row(f"  {state}", m)


# ── Main ───────────────────────────────────────────────────────────────────────

def run_param_combo(
    preds: pd.DataFrame,
    stop_pct: float,
    target_pct: float | None,
    time_exit_bars: int,
    entry_bar: int = 2,
) -> pd.DataFrame:
    """Execute all trades for one parameter combination. Returns trades DataFrame.

    target_pct may be None (--no-target mode): hold to time_exit_bars or stop only.
    """
    rows = []
    for _, row in preds.iterrows():
        d     = row["date"]
        label = row["pred_label"]
        dirn  = DIRECTION.get(label)
        if dirn is None:
            continue

        pm_df = load_pm_bars(d.date() if hasattr(d, "date") else d)
        if pm_df.empty:
            continue

        result = execute_trade(pm_df, dirn, stop_pct, target_pct,
                               time_exit_bars, entry_bar=entry_bar)
        if result is None:
            continue

        rows.append({
            "date":       str(d)[:10],
            "year":       int(str(d)[:4]),
            "state":      label,
            "direction":  dirn,
            "confidence": float(row["confidence"]),
            "correct":    row.get("correct", None),
            **result,
        })

    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description="Phase 5: PM impulse backtest")
    parser.add_argument("--state",      choices=["bull", "bear", "both"], default="both")
    parser.add_argument("--min-conf",   type=float, default=0.70)
    parser.add_argument("--entry-bar",  type=int,   default=2,
                        help="PM bar for entry open. 0=13:00, 1=13:01, 2=13:02 (default)")
    parser.add_argument("--stop",       type=float, default=None,
                        help="Single stop pct, overrides grid")
    parser.add_argument("--target",     type=float, default=None,
                        help="Single target pct, overrides grid")
    parser.add_argument("--time-exit",  type=int,   default=None,
                        help="Single time exit in minutes, overrides grid")
    parser.add_argument("--full-grid",  action="store_true",
                        help="Run full grid in chosen grid mode")
    parser.add_argument("--tight-grid", action="store_true",
                        help="Use tight stop/target grid from remaining excursion diagnostic")
    # ── Path A enhancements (Feb 2026) ──────────────────────────────────────
    parser.add_argument("--extended",   action="store_true",
                        help="Use extended time exits [75,105,135m] to hold up to 15:15. "
                             "Uses wider stops/targets suited to longer hold.")
    parser.add_argument("--no-target",  action="store_true",
                        help="Remove fixed target cap. Position held until stop or time exit "
                             "only. Captures full PM trend on directional days. "
                             "Best combined with --extended.")
    parser.add_argument("--conf-sweep", action="store_true",
                        help="Run the selected grid at min_conf 0.70, 0.75, 0.80 and print "
                             "side-by-side comparison. Shows how edge concentrates at higher "
                             "confidence thresholds.")
    parser.add_argument("--raw-csv", type=str, default=None,
                        help="Path to pm_expectancy_raw.csv (default: data/features/day_type/pm_expectancy_raw.csv). "
                             "Use to compare original vs retrained v2 model predictions.")
    args = parser.parse_args()

    eb = args.entry_bar
    entry_label = f"13:{eb:02d}" if eb < 60 else f"bar{eb}"
    raw_csv = Path(args.raw_csv) if args.raw_csv else None

    # ── Confidence sweep mode ────────────────────────────────────────────────
    if args.conf_sweep:
        _run_conf_sweep(args, eb, entry_label, raw_csv=raw_csv)
        return

    preds = load_predictions(args.min_conf, args.state, raw_csv=raw_csv)
    print(f"Predictions loaded: {len(preds)} days  "
          f"(bear={(preds['pred_label']=='BearTrend').sum()}, "
          f"bull={(preds['pred_label']=='BullTrend').sum()})")
    print(f"Entry: {entry_label} open (pm_bar {eb})")

    no_target = args.no_target

    # Build parameter grid
    if args.stop is not None and args.time_exit is not None and not args.full_grid:
        # Explicit single combo: target may be None (--no-target) or specified
        target_val = None if no_target else (args.target if args.target is not None else 0.22)
        grid = [(args.stop, target_val, args.time_exit)]
    elif args.full_grid or (args.stop is None and args.target is None):
        if args.extended:
            if no_target:
                # No-target: only stop × time combos
                grid = [(s, None, tex)
                        for s in STOP_GRID_EXTENDED
                        for tex in TIME_EXIT_EXTENDED]
            else:
                grid = [(s, t, tex)
                        for s in STOP_GRID_EXTENDED
                        for t in TARGET_GRID_EXTENDED
                        for tex in TIME_EXIT_EXTENDED]
        elif args.tight_grid:
            # Only combos with target >= stop (positive R:R)
            grid = [
                (s, t, tex)
                for s in STOP_GRID_TIGHT
                for t in TARGET_GRID_TIGHT
                for tex in TIME_EXIT_TIGHT
                if t >= s
            ]
        else:
            grid = list(product(STOP_GRID, TARGET_GRID, TIME_EXIT_GRID))
    else:
        if args.extended:
            stop   = args.stop   if args.stop   is not None else 0.20
            target = None if no_target else (args.target if args.target is not None else 0.40)
            tex    = args.time_exit or 135
        elif args.tight_grid:
            stop   = args.stop   if args.stop   is not None else 0.12
            target = None if no_target else (args.target if args.target is not None else 0.15)
            tex    = args.time_exit or 35
        else:
            stop   = args.stop   if args.stop   is not None else 0.15
            target = None if no_target else (args.target if args.target is not None else 0.22)
            tex    = args.time_exit or 35
        grid = [(stop, target, tex)]

    # Base case for detailed report
    if args.extended:
        BASE = (0.20, None if no_target else 0.40, 135)
    elif args.tight_grid:
        BASE = (0.12, 0.15, 35)
    else:
        BASE = (0.15, 0.22, 35)

    # ── Full grid summary ────────────────────────────────────────────────────
    if args.extended:
        grid_label = "EXTENDED (no-target)" if no_target else "EXTENDED"
    elif args.tight_grid:
        grid_label = "TIGHT"
    else:
        grid_label = "ORIGINAL"

    if args.full_grid or len(grid) > 1:
        hdr(f"PARAMETER GRID SUMMARY  [{grid_label}]  [{args.state.upper()}]  "
            f"entry={entry_label}  min_conf={args.min_conf}  rtc={ROUND_TRIP_COST}%")
        tgt_hdr = "Target " if not no_target else "Target"
        print(f"  {'Stop':>6}  {tgt_hdr:>7}  {'TmExit':>7}  "
              f"{'N':>5}  {'WR':>7}  {'Expect%':>8}  {'TotRet%':>8}  "
              f"{'MaxDD%':>7}  {'CL':>4}  {'Sharpe':>7}")
        print(f"  {'-'*6}  {'-'*7}  {'-'*7}  "
              f"{'-'*5}  {'-'*7}  {'-'*8}  {'-'*8}  "
              f"{'-'*7}  {'-'*4}  {'-'*7}")

        best_expect = -999
        best_combo  = None

        for stop_pct, target_pct, tex in grid:
            trades = run_param_combo(preds, stop_pct, target_pct, tex, entry_bar=eb)
            if trades.empty:
                continue
            m = compute_metrics(trades["pnl_pct"])
            marker = " <-- BASE" if (stop_pct, target_pct, tex) == BASE else ""
            tgt_str = "  none" if target_pct is None else f"{target_pct:>6.2f}%"
            print(
                f"  {stop_pct:>5.2f}%  {tgt_str}  {tex:>6}m  "
                f"{m['n_trades']:>5}  {m['win_rate']:>6.1%}  "
                f"{m['expectancy_pct']:>+7.3f}%  {m['total_return_pct']:>+7.2f}%  "
                f"{m['max_dd_pct']:>7.2f}%  {m['max_consec_loss']:>4}  "
                f"{m['sharpe']:>7.2f}{marker}"
            )
            if m["expectancy_pct"] > best_expect:
                best_expect = m["expectancy_pct"]
                best_combo  = (stop_pct, target_pct, tex)

        if best_combo:
            tgt_disp = "none" if best_combo[1] is None else f"{best_combo[1]}%"
            print(f"\n  Best by expectancy: stop={best_combo[0]}%  "
                  f"target={tgt_disp}  time_exit={best_combo[2]}m")

    # ── Detailed report for base case (or single specified combo) ────────────
    primary = grid[0] if len(grid) == 1 else BASE
    # Ensure BASE combo is in grid; if not, use first
    if len(grid) > 1 and BASE not in grid:
        primary = grid[0]
    stop_pct, target_pct, tex = primary

    tgt_label = "none" if target_pct is None else f"{target_pct}%"
    hdr(f"DETAILED REPORT  entry={entry_label}  stop={stop_pct}%  "
        f"target={tgt_label}  time_exit={tex}m  [{args.state.upper()}]")

    all_trades = run_param_combo(preds, stop_pct, target_pct, tex, entry_bar=eb)
    if all_trades.empty:
        print("  No trades executed.")
        return

    # Save trades
    all_trades.to_csv(TRADES_CSV, index=False)
    print(f"  Trades saved -> {TRADES_CSV}\n")

    # Overall summary
    print("  OVERALL")
    print_metrics_row("All states", compute_metrics(all_trades["pnl_pct"]))
    for state in ["BearTrend", "BullTrend"]:
        s_trades = all_trades[all_trades["state"] == state]
        if not s_trades.empty:
            print_metrics_row(state, compute_metrics(s_trades["pnl_pct"]))

    # Exit reason breakdown
    print(f"\n  EXIT REASONS (all states)")
    print_exit_breakdown(all_trades)
    for state in ["BearTrend", "BullTrend"]:
        s_trades = all_trades[all_trades["state"] == state]
        if not s_trades.empty:
            print(f"\n  EXIT REASONS ({state})")
            print_exit_breakdown(s_trades)

    # Year-by-year — the walk-forward view
    hdr("YEAR-BY-YEAR WALK-FORWARD")
    years = sorted(all_trades["year"].unique())
    print(f"  {'Year':<6}  {'State':<12}  "
          f"{'N':>5}  {'WR':>7}  {'Expect%':>8}  {'TotRet%':>8}  "
          f"{'MaxDD%':>7}  {'CL':>4}  {'Sharpe':>7}")
    print(f"  {'-'*6}  {'-'*12}  "
          f"{'-'*5}  {'-'*7}  {'-'*8}  {'-'*8}  "
          f"{'-'*7}  {'-'*4}  {'-'*7}")

    for yr in years:
        yr_trades = all_trades[all_trades["year"] == yr]
        # Combined
        m = compute_metrics(yr_trades["pnl_pct"])
        if m:
            print(f"  {yr:<6}  {'(combined)':<12}  "
                  f"{m['n_trades']:>5}  {m['win_rate']:>6.1%}  "
                  f"{m['expectancy_pct']:>+7.3f}%  {m['total_return_pct']:>+7.2f}%  "
                  f"{m['max_dd_pct']:>7.2f}%  {m['max_consec_loss']:>4}  "
                  f"{m['sharpe']:>7.2f}")
        # Per state
        for state in ["BearTrend", "BullTrend"]:
            st = yr_trades[yr_trades["state"] == state]
            if len(st) >= 3:
                m = compute_metrics(st["pnl_pct"])
                if m:
                    print(f"  {'':6}  {state:<12}  "
                          f"{m['n_trades']:>5}  {m['win_rate']:>6.1%}  "
                          f"{m['expectancy_pct']:>+7.3f}%  {m['total_return_pct']:>+7.2f}%  "
                          f"{m['max_dd_pct']:>7.2f}%  {m['max_consec_loss']:>4}  "
                          f"{m['sharpe']:>7.2f}")

    # Correct vs incorrect prediction performance
    if "correct" in all_trades.columns:
        hdr("PREDICTION ACCURACY CONDITIONAL ON TRADE OUTCOME")
        for state in ["BearTrend", "BullTrend"]:
            s_t = all_trades[all_trades["state"] == state]
            if s_t.empty:
                continue
            corr = s_t[s_t["correct"] == True]
            incorr = s_t[s_t["correct"] == False]
            print(f"\n  {state}  (n={len(s_t)})")
            print_metrics_row("  correct pred", compute_metrics(corr["pnl_pct"]))
            print_metrics_row("  wrong pred",   compute_metrics(incorr["pnl_pct"]))

    # PnL distribution
    hdr("PnL DISTRIBUTION")
    for state in ["BearTrend", "BullTrend", "combined"]:
        if state == "combined":
            pnl = all_trades["pnl_pct"]
            lbl = "combined"
        else:
            s_t = all_trades[all_trades["state"] == state]
            if s_t.empty:
                continue
            pnl = s_t["pnl_pct"]
            lbl = state
        print(f"\n  {lbl}")
        print(f"    p10={pnl.quantile(0.10):>+.3f}%  p25={pnl.quantile(0.25):>+.3f}%  "
              f"p50={pnl.quantile(0.50):>+.3f}%  p75={pnl.quantile(0.75):>+.3f}%  "
              f"p90={pnl.quantile(0.90):>+.3f}%")
        print(f"    best={pnl.max():>+.3f}%  worst={pnl.min():>+.3f}%  "
              f"std={pnl.std():.3f}%")

    # Consecutive loss streaks
    hdr("CONSECUTIVE LOSS ANALYSIS (combined)")
    pnl_seq = all_trades["pnl_pct"].values
    streaks = []
    cur = 0
    for p in pnl_seq:
        if p <= 0:
            cur += 1
        else:
            if cur > 0:
                streaks.append(cur)
            cur = 0
    if cur > 0:
        streaks.append(cur)
    if streaks:
        s = pd.Series(streaks)
        print(f"  Loss streaks: n={len(s)}  mean={s.mean():.1f}  "
              f"p75={s.quantile(0.75):.0f}  max={s.max()}")
        print(f"  Streaks of 3+: {(s >= 3).sum()}  Streaks of 5+: {(s >= 5).sum()}")

    # Deployment verdict
    hdr("DEPLOYMENT VERDICT")
    m_all  = compute_metrics(all_trades["pnl_pct"])
    m_bear = compute_metrics(all_trades[all_trades["state"] == "BearTrend"]["pnl_pct"])
    m_bull = compute_metrics(all_trades[all_trades["state"] == "BullTrend"]["pnl_pct"])

    for label, m in [("BearTrend", m_bear), ("BullTrend", m_bull), ("Combined", m_all)]:
        if not m:
            continue
        expect = m["expectancy_pct"]
        dd     = m["max_dd_pct"]
        wr     = m["win_rate"]
        sharpe = m["sharpe"]

        if expect > 0 and dd > -2.0 and wr > 0.50 and sharpe > 0.5:
            verdict = "CANDIDATE for paper trading"
        elif expect > 0 and dd > -5.0:
            verdict = "WEAK EDGE — needs refinement before deployment"
        elif expect <= 0:
            verdict = "NO EDGE — do not deploy"
        else:
            verdict = "HIGH RISK — DD too large"

        print(f"  {label:<12}  expectancy={expect:>+.3f}%  "
              f"DD={dd:.2f}%  WR={wr:.1%}  Sharpe={sharpe:.2f}"
              f"  -> {verdict}")

    print(f"\nDone. Round-trip cost assumed: {ROUND_TRIP_COST}% per trade.")


if __name__ == "__main__":
    main()
