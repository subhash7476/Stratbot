"""
Live Day-Type Engine — CLI Runner
===================================
Runs the DayTypeEngine against a specific historical date (replay mode)
or prints the current live state.

Use cases:
  1. Replay a past day — verify predictions at each checkpoint:
       python scripts/live_daytype_engine.py --date 2025-06-03

  2. Replay a range of days — batch prediction quality check:
       python scripts/live_daytype_engine.py --start 2025-01-01 --end 2025-12-31

  3. Print JSON state at each checkpoint for integration testing:
       python scripts/live_daytype_engine.py --date 2025-06-03 --json

  4. Run from a live 1m bar feed (see DayTypeEngine.on_bar() in core/state/daytype_engine.py)

Output per day:
  Date        CP     Pred       Conf   Tier   Locked  Actual
  2025-06-03  10am   BullTrend  0.62   med    False   BullTrend  [CORRECT]
  2025-06-03  11am   BullTrend  0.74   high   True    BullTrend  [CORRECT]
  2025-06-03  13pm   --         --     --     True    (locked, skipped)
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.state.daytype_engine import DayTypeEngine, CLUSTER_NAMES

CANDLE_DIR_1M = ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
FEATURE_DIR   = ROOT / "data" / "features" / "day_type"
LABEL_MAP     = {v: k for k, v in CLUSTER_NAMES.items()}


# ── Data loading ───────────────────────────────────────────────────────────────

def load_session(d: date, symbol: str) -> pd.DataFrame:
    db_path = CANDLE_DIR_1M / f"{d.isoformat()}.duckdb"
    if not db_path.exists():
        return pd.DataFrame()

    try:
        con = duckdb.connect(str(db_path), read_only=True)
        df = con.execute(
            "SELECT * FROM candles WHERE symbol = ? ORDER BY timestamp",
            [symbol]
        ).df()
        con.close()
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return df

    hour_min = df['timestamp'].dt.hour * 60 + df['timestamp'].dt.minute
    return df[(hour_min >= 555) & (hour_min <= 929)].reset_index(drop=True)


def load_actual_labels() -> dict:
    """Load ground-truth cluster labels for evaluation."""
    path = FEATURE_DIR / "cluster_labels.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path, index_col='date', parse_dates=True)
    return {pd.Timestamp(d).date(): int(row['cluster_id']) for d, row in df.iterrows()
            if not pd.isna(row['cluster_id'])}


# ── Replay logic ───────────────────────────────────────────────────────────────

def replay_day(engine: DayTypeEngine, d: date, symbol: str,
               actual_labels: dict, emit_json: bool) -> list[dict]:
    """Replay one day through the engine. Returns list of checkpoint state dicts."""
    df_1m = load_session(d, symbol)
    if df_1m.empty or len(df_1m) < 45:
        return []

    engine.reset(d)
    actual_cls = actual_labels.get(d, -1)
    actual_name = CLUSTER_NAMES.get(actual_cls, 'Unknown')

    rows = []
    prev_locked = False

    for _, bar in df_1m.iterrows():
        state = engine.on_bar(bar.to_dict())

        if state is not None:
            correct = (state.cluster_id == actual_cls) if actual_cls >= 0 else None
            row = {
                'date':      str(d),
                'checkpoint': state.checkpoint,
                'predicted':  state.predicted_state,
                'confidence': state.confidence,
                'conf_tier':  state.conf_tier,
                'locked':     state.locked,
                'actual':     actual_name,
                'correct':    correct,
                'p_bear':     state.p_bear,
                'p_bull':     state.p_bull,
                'p_choppy':   state.p_choppy,
            }
            rows.append(row)

            if emit_json:
                import json
                print(json.dumps(state.to_dict(), indent=2))

            if state.locked and not prev_locked:
                # Engine locked — remaining checkpoints won't run
                pass
            prev_locked = state.locked

    return rows


# ── Reporting ──────────────────────────────────────────────────────────────────

def print_header():
    print(f"\n{'Date':>12}  {'CP':>5}  {'Predicted':>10}  {'Conf':>6}  {'Tier':>5}  {'Lock':>5}  {'Actual':>10}  {'Result':>9}")
    print(f"{'─'*12}  {'─'*5}  {'─'*10}  {'─'*6}  {'─'*5}  {'─'*5}  {'─'*10}  {'─'*9}")


def print_row(row: dict):
    result = ''
    if row['correct'] is True:
        result = '[OK]'
    elif row['correct'] is False:
        result = '[MISS]'

    print(
        f"{row['date']:>12}  {row['checkpoint']:>5}  {row['predicted']:>10}  "
        f"{row['confidence']:>6.2f}  {row['conf_tier']:>5}  "
        f"{'YES' if row['locked'] else 'no':>5}  "
        f"{row['actual']:>10}  {result:>9}"
    )


def print_summary(all_rows: list[dict]) -> None:
    if not all_rows:
        print("\nNo results.")
        return

    df = pd.DataFrame(all_rows)
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total checkpoint predictions: {len(df)}")
    print(f"Total days: {df['date'].nunique()}")

    for cp in ['10am', '11am', '13pm']:
        sub = df[df['checkpoint'] == cp]
        if len(sub) == 0:
            continue
        has_label = sub['correct'].notna()
        acc = sub.loc[has_label, 'correct'].mean() if has_label.sum() > 0 else float('nan')
        n_locked = sub['locked'].sum()
        print(f"\n  {cp}:  n={len(sub)}, acc={acc:.1%}, locked={n_locked} ({n_locked/len(sub):.0%})")

        # Accuracy by confidence tier
        for tier in ['high', 'med', 'low']:
            t_sub = sub[sub['conf_tier'] == tier]
            t_corr = t_sub[t_sub['correct'].notna()]
            if len(t_corr) > 0:
                t_acc = t_corr['correct'].mean()
                print(f"    {tier:>5}: n={len(t_sub)}, acc={t_acc:.1%}")

    # Overall accuracy across all checkpoints
    has_label = df['correct'].notna()
    overall_acc = df.loc[has_label, 'correct'].mean()
    print(f"\n  Overall accuracy (all CPs, labeled days): {overall_acc:.1%}")

    # Final locked state accuracy (first checkpoint where locked=True per day)
    locked_preds = (
        df[df['locked']]
        .sort_values(['date', 'checkpoint'])
        .drop_duplicates(subset='date', keep='first')
    )
    if len(locked_preds) > 0:
        lp_corr = locked_preds['correct'].notna()
        locked_acc = locked_preds.loc[lp_corr, 'correct'].mean()
        print(f"  Accuracy on first-locked prediction:      {locked_acc:.1%} (n={len(locked_preds)})")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Live/replay day-type engine")
    parser.add_argument('--date',   help='Single date to replay (YYYY-MM-DD)')
    parser.add_argument('--start',  help='Start date for range replay')
    parser.add_argument('--end',    help='End date for range replay')
    parser.add_argument('--symbol', default='NSE_INDEX|Nifty 50')
    parser.add_argument('--model',  default='logistic', choices=['logistic', 'lgbm'])
    parser.add_argument('--json',   action='store_true', help='Emit JSON state at each checkpoint')
    args = parser.parse_args()

    # Build date list
    if args.date:
        dates = [date.fromisoformat(args.date)]
    elif args.start and args.end:
        s = date.fromisoformat(args.start)
        e = date.fromisoformat(args.end)
        dates = [s + timedelta(n) for n in range((e - s).days + 1)
                 if (CANDLE_DIR_1M / f"{(s + timedelta(n)).isoformat()}.duckdb").exists()]
    else:
        parser.print_help()
        sys.exit(1)

    print("=" * 60)
    print("DAY-TYPE ENGINE — REPLAY MODE")
    print("=" * 60)
    print(f"  Symbol: {args.symbol}")
    print(f"  Model:  {args.model}")
    print(f"  Days:   {len(dates)}")

    # Init engine
    try:
        engine = DayTypeEngine(model_name=args.model)
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    actual_labels = load_actual_labels()
    print(f"  Ground truth labels loaded: {len(actual_labels)} days")

    all_rows = []
    if not args.json:
        print_header()

    for d in dates:
        rows = replay_day(engine, d, args.symbol, actual_labels, emit_json=args.json)
        if not args.json:
            for row in rows:
                print_row(row)
        all_rows.extend(rows)

    if not args.json:
        print_summary(all_rows)

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)


if __name__ == '__main__':
    main()
