#!/usr/bin/env python3
"""
Trade Learning Protocol V1 - Structural Review
===============================================
Reads completed stock_paper_trades from trading.db and produces a structured
analysis of edge by regime, dispersion, signal quality, and MAE/MFE diagnostics.

Usage:
    python scripts/perform_structural_review.py
    python scripts/perform_structural_review.py --min-trades 20
    python scripts/perform_structural_review.py --start-date 2026-01-01
    python scripts/perform_structural_review.py --end-date 2026-03-31
"""
import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "trading" / "trading.db"

# -------------------------------------------------------------
#  Data loading
# -------------------------------------------------------------

def load_trades(start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Load completed trades from trading.db into a list of dicts."""
    if not DB_PATH.exists():
        print(f"ERROR: trading.db not found at {DB_PATH}")
        sys.exit(1)

    conn   = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    query  = "SELECT * FROM stock_paper_trades WHERE exit_time IS NOT NULL"
    params = []
    if start_date:
        query  += " AND session_date >= ?"
        params.append(start_date)
    if end_date:
        query  += " AND session_date <= ?"
        params.append(end_date)
    query += " ORDER BY session_date ASC, entry_time ASC"

    rows  = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# -------------------------------------------------------------
#  Formatting helpers
# -------------------------------------------------------------

SEP  = "-" * 66
DSEP = "=" * 66

def _fmt_pct(v):
    if v is None:
        return "   N/A"
    return f"{v*100:+6.1f}%"

def _fmt_r(v):
    if v is None:
        return "  N/A"
    return f"{v:5.2f}R"

def _fmt_rs(v):
    if v is None:
        return "     N/A"
    sign = "+" if v >= 0 else ""
    return f"Rs {sign}{v:,.0f}"

def _safe_avg(lst):
    lst = [x for x in lst if x is not None]
    return sum(lst) / len(lst) if lst else None

def _print_table(headers, rows, col_widths):
    """Print a simple fixed-width table."""
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print("  " + fmt.format(*headers))
    print("  " + SEP)
    for row in rows:
        print("  " + fmt.format(*row))


# -------------------------------------------------------------
#  Analysis sections
# -------------------------------------------------------------

def section_coverage(trades: list) -> None:
    n = len(trades)
    print(f"\n  Coverage ({n} completed trades)")
    print("  " + SEP)
    fields = [
        "regime_state", "session_type", "index_return_entry",
        "breadth_ratio", "signal_rank", "signal_percentile",
        "dispersion_csad", "dispersion_pct",
        "mae_pct", "mfe_pct", "mae_r", "mfe_r", "exit_efficiency",
    ]
    for f in fields:
        populated = sum(1 for t in trades if t.get(f) is not None)
        pct       = populated / n * 100 if n else 0
        bar       = "█" * int(pct / 5)
        print(f"  {f:<22s}: {populated:>4}/{n}  ({pct:5.1f}%)  {bar}")


def section_expectancy(trades: list, group_field: str, title: str, min_trades: int) -> None:
    """Print expectancy table grouped by a context field."""
    groups = {}
    for t in trades:
        key = t.get(group_field) or "unknown"
        groups.setdefault(key, []).append(t)

    rows = []
    for key in sorted(groups.keys()):
        g     = groups[key]
        n     = len(g)
        if n < min_trades:
            continue
        wins  = sum(1 for t in g if (t.get("pnl_rs") or 0) > 0)
        wr    = wins / n * 100
        avg_pnl = _safe_avg([t.get("pnl_rs") for t in g])
        tot_pnl = sum(t.get("pnl_rs") or 0 for t in g)
        avg_mfe = _safe_avg([t.get("mfe_r") for t in g])
        avg_mae = _safe_avg([t.get("mae_r") for t in g])

        rows.append((
            str(key)[:14],
            str(n),
            f"{wr:.0f}%",
            _fmt_rs(avg_pnl),
            _fmt_rs(tot_pnl),
            _fmt_r(avg_mfe),
            _fmt_r(avg_mae),
        ))

    if not rows:
        print(f"\n  {title} - insufficient data (< {min_trades} trades per group)")
        return

    print(f"\n  {title}")
    print("  " + SEP)
    _print_table(
        ["Group", "N", "WR%", "Avg PnL", "Total PnL", "MFE-R", "MAE-R"],
        rows,
        [14, 5, 5, 10, 12, 7, 7],
    )


def section_signal_decay(trades: list, min_trades: int) -> None:
    """Expectancy by signal percentile bucket."""
    def bucket(pct):
        if pct is None:
            return "unknown"
        if pct >= 90:
            return "top10%"
        if pct >= 80:
            return "top20%"
        if pct >= 60:
            return "top40%"
        return "bottom50%"

    enriched = [(bucket(t.get("signal_percentile")), t) for t in trades]
    groups   = {}
    for b, t in enriched:
        groups.setdefault(b, []).append(t)

    rows = []
    for key in ["top10%", "top20%", "top40%", "bottom50%", "unknown"]:
        g = groups.get(key, [])
        n = len(g)
        if n < min_trades:
            continue
        wins    = sum(1 for t in g if (t.get("pnl_rs") or 0) > 0)
        wr      = wins / n * 100
        avg_pnl = _safe_avg([t.get("pnl_rs") for t in g])
        tot_pnl = sum(t.get("pnl_rs") or 0 for t in g)
        avg_eff = _safe_avg([t.get("exit_efficiency") for t in g])
        rows.append((key, str(n), f"{wr:.0f}%", _fmt_rs(avg_pnl), _fmt_rs(tot_pnl),
                     f"{avg_eff:.2f}" if avg_eff is not None else "N/A"))

    if not rows:
        print(f"\n  Signal Percentile Decay - insufficient data (< {min_trades} trades per bucket)")
        return

    print("\n  Signal Percentile Decay")
    print("  " + SEP)
    _print_table(
        ["Bucket", "N", "WR%", "Avg PnL", "Total PnL", "Exit Eff"],
        rows,
        [10, 5, 5, 10, 12, 10],
    )


def section_dispersion_buckets(trades: list, min_trades: int) -> None:
    """Expectancy by dispersion CSAD percentile bucket."""
    def bucket(pct):
        if pct is None:
            return "unknown"
        if pct >= 80:
            return "high(>p80)"
        if pct >= 50:
            return "mid(p50-80)"
        return "low(<p50)"

    enriched = [(bucket(t.get("dispersion_pct")), t) for t in trades]
    groups   = {}
    for b, t in enriched:
        groups.setdefault(b, []).append(t)

    rows = []
    for key in ["high(>p80)", "mid(p50-80)", "low(<p50)", "unknown"]:
        g = groups.get(key, [])
        n = len(g)
        if n < min_trades:
            continue
        wins    = sum(1 for t in g if (t.get("pnl_rs") or 0) > 0)
        wr      = wins / n * 100
        avg_pnl = _safe_avg([t.get("pnl_rs") for t in g])
        tot_pnl = sum(t.get("pnl_rs") or 0 for t in g)
        avg_mfe = _safe_avg([t.get("mfe_r") for t in g])
        rows.append((key, str(n), f"{wr:.0f}%", _fmt_rs(avg_pnl), _fmt_rs(tot_pnl), _fmt_r(avg_mfe)))

    if not rows:
        print(f"\n  Dispersion Buckets - insufficient data (< {min_trades} trades per bucket)")
        return

    print("\n  Expectancy by Dispersion (11am CSAD percentile)")
    print("  " + SEP)
    _print_table(
        ["Bucket", "N", "WR%", "Avg PnL", "Total PnL", "MFE-R"],
        rows,
        [12, 5, 5, 10, 12, 7],
    )


def section_mae_mfe(trades: list) -> None:
    """MAE/MFE diagnostic summary."""
    with_mae = [t for t in trades if t.get("mae_r") is not None]
    with_mfe = [t for t in trades if t.get("mfe_r") is not None]
    with_eff = [t for t in trades if t.get("exit_efficiency") is not None]

    if not with_mfe:
        print("\n  MAE/MFE Diagnostic - no data yet (trades need to close with 1m bar access)")
        return

    avg_mfe  = _safe_avg([t["mfe_r"] for t in with_mfe])
    avg_mae  = _safe_avg([t["mae_r"] for t in with_mae])
    avg_eff  = _safe_avg([t["exit_efficiency"] for t in with_eff])
    pct_over1r = sum(1 for t in with_mfe if t["mfe_r"] > 1.0) / len(with_mfe) * 100

    # Avg realized R (pnl_gross_pct / stop_pct approximation)
    realized_rs = [t.get("pnl_rs") for t in trades if t.get("pnl_rs") is not None]
    avg_pnl_rs  = _safe_avg(realized_rs)

    print("\n  MAE/MFE Diagnostic")
    print("  " + SEP)
    print(f"  Avg MFE-R           : {avg_mfe:.2f}R  ({len(with_mfe)} trades)")
    print(f"  Avg MAE-R           : {avg_mae:.2f}R  ({len(with_mae)} trades)")
    print(f"  Avg Exit Efficiency : {avg_eff:.2f}  (1.0 = captured all MFE)")
    print(f"  Trades w/ MFE > 1R  : {pct_over1r:.0f}%")
    print(f"  Avg Realized PnL    : Rs {avg_pnl_rs:+,.0f}")
    if avg_mfe and avg_eff:
        captured_r = avg_mfe * avg_eff
        print(f"  Avg Realized vs MFE : {captured_r:.2f}R captured of {avg_mfe:.2f}R available")


# -------------------------------------------------------------
#  Main
# -------------------------------------------------------------

def run_analysis(min_trades: int = 30, start_date: Optional[str] = None, end_date: Optional[str] = None) -> None:
    trades = load_trades(start_date, end_date)

    if not trades:
        print("No completed trades found. Run the paper trading strategy first.")
        return

    dates = sorted(set(t["session_date"] for t in trades))
    period = f"{dates[0]} to {dates[-1]}" if dates else "N/A"

    print()
    print(f"  {DSEP}")
    print(f"  TRADE LEARNING PROTOCOL V1 - STRUCTURAL REVIEW")
    print(f"  Trades: {len(trades)} | Period: {period}")
    print(f"  {DSEP}")

    section_coverage(trades)
    section_expectancy(trades, "regime_state",  "Expectancy by Regime",        min_trades)
    section_dispersion_buckets(trades, min_trades)
    section_signal_decay(trades, min_trades)
    section_mae_mfe(trades)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="TLP V1 Structural Review")
    parser.add_argument("--min-trades", type=int, default=30,
                        help="Minimum trades per group to include in table (default: 30)")
    parser.add_argument("--start-date", default=None, metavar="YYYY-MM-DD",
                        help="Filter trades from this date")
    parser.add_argument("--end-date",   default=None, metavar="YYYY-MM-DD",
                        help="Filter trades up to this date")
    args = parser.parse_args()
    run_analysis(
        min_trades  = args.min_trades,
        start_date  = args.start_date,
        end_date    = args.end_date,
    )


if __name__ == "__main__":
    main()
