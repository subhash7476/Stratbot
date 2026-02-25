"""
PixityAI Combination — v6 Momentum + v7 Mean Reversion
=======================================================
Pure arithmetic: run both strategies independently on the same capital pool
(50/50 split), sum the PnL, measure the blended equity curve.

No new parameters. No new filters. No changes to either strategy.
Just: capital_per_strategy = 250,000. Add results.

Walk-forward: same four periods as v6/v7 individual runs.
  TRAIN-A: 2023-01-02 to 2023-12-29
  TEST-A:  2024-01-02 to 2024-12-31
  TRAIN-B: 2025-01-02 to 2025-07-31
  TEST-B:  2025-08-01 to 2026-02-13

Usage:
    python scripts/run_combo_backtest.py
"""

from __future__ import annotations

import os, sys, logging
import numpy as np
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

os.environ["PYTHONIOENCODING"] = "utf-8"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Import shared infrastructure from v7 (data loading, universe selection)
from scripts.run_v7_backtest import (
    preload_daily_ohlcv, preload_nifty, get_trading_days,
    select_universe, compute_fees, compute_rsi_arr, compute_atr,
    generate_signals as v7_signals,
    Portfolio as V7Portfolio,
    Trade as V7Trade,
)
# Import momentum signal generator from v6
from scripts.run_v6_backtest import (
    generate_signals as v6_signals,
    Portfolio as V6Portfolio,
    Trade as V6Trade,
)
from core.database.manager import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

TOTAL_CAPITAL   = 500_000.0
SPLIT           = 0.50          # 50/50
CAPITAL_EACH    = TOTAL_CAPITAL * SPLIT   # Rs 250,000 per strategy


# ---------------------------------------------------------------------------
# Run one period for both strategies, return combined stats
# ---------------------------------------------------------------------------

def run_period(
    start:       date,
    end:         date,
    universe:    List[str],
    symbol_map:  Dict[str, str],
    daily_cache: Dict[str, pd.DataFrame],
    nifty_df:    Optional[object],
    label:       str,
) -> dict:
    import pandas as pd

    trading_days = get_trading_days(ROOT / "data", start, end)
    logger.info(f"[{label}] {start} to {end}  |  {len(trading_days)} trading days")

    # ── Signals ──────────────────────────────────────────────────────────────
    mom_sigs = v6_signals(
        universe     = universe,
        symbol_map   = symbol_map,
        daily_cache  = daily_cache,
        trading_days = trading_days,
        lookback     = 20,
    )
    rev_sigs = v7_signals(
        universe      = universe,
        symbol_map    = symbol_map,
        daily_cache   = daily_cache,
        nifty_df      = nifty_df,
        trading_days  = trading_days,
        rsi_threshold = 35.0,
        ret_threshold = -0.03,
    )
    logger.info(f"  Momentum signals:  {sum(len(v) for v in mom_sigs.values())}")
    logger.info(f"  MeanRev signals:   {sum(len(v) for v in rev_sigs.values())}")

    # ── Portfolios ────────────────────────────────────────────────────────────
    mom_port = V6Portfolio(capital=CAPITAL_EACH)
    rev_port = V7Portfolio(capital=CAPITAL_EACH)

    for td in trading_days:
        bar_today: Dict[str, pd.Series] = {}
        for sym in universe:
            df = daily_cache.get(sym)
            if df is None:
                continue
            row = df[df["date"] == td]
            if not row.empty:
                bar_today[sym] = row.iloc[0]

        # Momentum entries
        for sig in mom_sigs.get(td, []):
            bar = bar_today.get(sig["symbol"])
            if bar is None:
                continue
            entry = float(bar["open"])
            if entry > 0:
                mom_port.enter(sig, entry, td)

        # Mean-reversion entries
        for sig in rev_sigs.get(td, []):
            bar = bar_today.get(sig["symbol"])
            if bar is None:
                continue
            entry = float(bar["open"])
            if entry > 0:
                rev_port.enter(sig, entry, td)

        mom_port.process_day(bar_today, td)
        rev_port.process_day(bar_today, td)

    # Force-close at end
    last_bars: Dict[str, pd.Series] = {}
    for sym in universe:
        df = daily_cache.get(sym)
        if df is None:
            continue
        row = df[df["date"] <= end].tail(1)
        if not row.empty:
            last_bars[sym] = row.iloc[0]

    if mom_port.open_trades:
        mom_port.force_close_all(last_bars, end)
    if rev_port.open_trades:
        rev_port.force_close_all(last_bars, end)

    # ── Compute blended equity curve ─────────────────────────────────────────
    # Both curves are indexed to same trading_days via equity_curve list of (date, equity)
    mom_curve = {d: e for d, e in mom_port.equity_curve}
    rev_curve = {d: e for d, e in rev_port.equity_curve}

    blended_pnls = []
    peak_combined = TOTAL_CAPITAL
    max_dd_combined = 0.0
    prev_combined = TOTAL_CAPITAL

    for td in trading_days:
        m_eq = mom_curve.get(td, CAPITAL_EACH)
        r_eq = rev_curve.get(td, CAPITAL_EACH)
        combined = m_eq + r_eq
        peak_combined = max(peak_combined, combined)
        dd = (peak_combined - combined) / peak_combined
        max_dd_combined = max(max_dd_combined, dd)

    final_combined = (
        mom_port.equity_curve[-1][1] if mom_port.equity_curve else CAPITAL_EACH
    ) + (
        rev_port.equity_curve[-1][1] if rev_port.equity_curve else CAPITAL_EACH
    )

    mom_pnl = mom_port.equity - CAPITAL_EACH
    rev_pnl = rev_port.equity - CAPITAL_EACH
    combined_pnl = mom_pnl + rev_pnl
    combined_ret = combined_pnl / TOTAL_CAPITAL * 100

    # Individual stats
    def port_stats(port, cap):
        trades = port.closed_trades
        if not trades:
            return {}
        pnls = [t.pnl(compute_fees) for t in trades]
        winners = [p for p in pnls if p > 0]
        losers  = [p for p in pnls if p <= 0]
        pf_den  = abs(sum(losers)) if losers else 1e-9
        return {
            "trades":  len(trades),
            "wr":      len(winners) / len(trades) * 100,
            "pf":      sum(winners) / pf_den if winners else 0.0,
            "pnl":     sum(pnls),
            "dd":      port.max_dd * 100,
            "ret":     (port.equity / cap - 1) * 100,
        }

    m_stats = port_stats(mom_port, CAPITAL_EACH)
    r_stats = port_stats(rev_port, CAPITAL_EACH)

    return {
        "label":           label,
        "mom_pnl":         mom_pnl,
        "rev_pnl":         rev_pnl,
        "combined_pnl":    combined_pnl,
        "combined_ret":    combined_ret,
        "max_dd_combined": max_dd_combined * 100,
        "mom":             m_stats,
        "rev":             r_stats,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import pandas as pd

    db = DatabaseManager(ROOT / "data")
    with db.config_reader() as conn:
        rows = conn.execute(
            "SELECT instrument_key, trading_symbol FROM fo_stocks WHERE is_active = 1"
        ).fetchall()
    symbol_map = {r[0]: r[1] for r in rows}
    logger.info(f"F&O universe: {len(symbol_map)} symbols")

    all_start = date(2023, 1, 2)
    all_end   = date(2026, 2, 13)

    logger.info("Loading data (single pass for all periods)...")
    daily_cache = preload_daily_ohlcv(
        ROOT / "data", set(symbol_map.keys()), all_start, all_end
    )
    nifty_df = preload_nifty(ROOT / "data", all_start, all_end)
    logger.info(f"Nifty: {len(nifty_df)} days" if nifty_df is not None else "No Nifty data")

    # Universes
    td_a   = get_trading_days(ROOT / "data", date(2023, 1, 2), date(2023, 12, 29))
    univ_a = select_universe(daily_cache, symbol_map, td_a[59], 60)
    td_b   = get_trading_days(ROOT / "data", date(2025, 1, 2), date(2025, 7, 31))
    univ_b = select_universe(daily_cache, symbol_map, td_b[59], 60)

    periods = [
        (date(2023, 1, 2), date(2023, 12, 29), "TRAIN-A (2023)",    univ_a),
        (date(2024, 1, 2), date(2024, 12, 31), "TEST-A  (2024)",    univ_a),
        (date(2025, 1, 2), date(2025,  7, 31), "TRAIN-B (2025-H1)", univ_b),
        (date(2025, 8, 1), date(2026,  2, 13), "TEST-B  (2025-H2)", univ_b),
    ]

    all_results = []
    for p_start, p_end, label, univ in periods:
        r = run_period(p_start, p_end, univ, symbol_map, daily_cache, nifty_df, label)
        all_results.append(r)

    # ── Print results ─────────────────────────────────────────────────────────
    print("\n" + "=" * 75)
    print("  COMBINATION BACKTEST — v6 Momentum + v7 Mean Reversion (50/50)")
    print(f"  Total Capital: Rs {TOTAL_CAPITAL:,.0f}  |  "
          f"Rs {CAPITAL_EACH:,.0f} per strategy")
    print("=" * 75)

    labels = ["TRAIN-A", "TEST-A ", "TRAIN-B", "TEST-B "]

    # Per-strategy breakdown
    print(f"\n  {'Period':<20}  {'Mom PnL':>10}  {'Mom DD':>7}  "
          f"{'Rev PnL':>10}  {'Rev DD':>7}  "
          f"{'Combined PnL':>13}  {'Combined Ret':>13}  {'Combo DD':>9}")
    print(f"  {'-'*105}")
    for r in all_results:
        m, v = r["mom"], r["rev"]
        print(f"  {r['label']:<20}  "
              f"{m.get('pnl', 0):>+10,.0f}  {m.get('dd', 0):>6.1f}%  "
              f"{v.get('pnl', 0):>+10,.0f}  {v.get('dd', 0):>6.1f}%  "
              f"{r['combined_pnl']:>+13,.0f}  "
              f"{r['combined_ret']:>+12.1f}%  "
              f"{r['max_dd_combined']:>8.1f}%")

    # Totals
    total_mom = sum(r["mom"].get("pnl", 0) for r in all_results)
    total_rev = sum(r["rev"].get("pnl", 0) for r in all_results)
    total_combined = sum(r["combined_pnl"] for r in all_results)
    total_ret = total_combined / TOTAL_CAPITAL * 100
    print(f"  {'-'*105}")
    print(f"  {'TOTAL (3yr)':20}  {total_mom:>+10,.0f}  {'':>7}  "
          f"{total_rev:>+10,.0f}  {'':>7}  "
          f"{total_combined:>+13,.0f}  {total_ret:>+12.1f}%")

    # Summary table
    print(f"\n\n  {'Metric':<22}  {'TRAIN-A':>10}  {'TEST-A':>10}  "
          f"{'TRAIN-B':>10}  {'TEST-B':>10}  {'TOTAL':>10}")
    print(f"  {'-'*75}")

    rows_data = [
        ("Mom PnL (Rs)",    [r["mom"].get("pnl",0)       for r in all_results]),
        ("Mom trades",      [r["mom"].get("trades",0)    for r in all_results]),
        ("Mom WR%",         [r["mom"].get("wr",0)        for r in all_results]),
        ("Mom PF",          [r["mom"].get("pf",0)        for r in all_results]),
        ("Mom DD%",         [r["mom"].get("dd",0)        for r in all_results]),
        ("Rev PnL (Rs)",    [r["rev"].get("pnl",0)       for r in all_results]),
        ("Rev trades",      [r["rev"].get("trades",0)    for r in all_results]),
        ("Rev WR%",         [r["rev"].get("wr",0)        for r in all_results]),
        ("Rev PF",          [r["rev"].get("pf",0)        for r in all_results]),
        ("Rev DD%",         [r["rev"].get("dd",0)        for r in all_results]),
        ("Combined PnL",    [r["combined_pnl"]           for r in all_results]),
        ("Combined Ret%",   [r["combined_ret"]           for r in all_results]),
        ("Combined DD%",    [r["max_dd_combined"]        for r in all_results]),
    ]
    for name, vals in rows_data:
        total = sum(vals)
        if isinstance(vals[0], float):
            row = "  ".join(f"{v:>10.2f}" for v in vals)
            print(f"  {name:<22}  {row}  {total:>10.2f}")
        else:
            row = "  ".join(f"{v:>10}" for v in vals)
            print(f"  {name:<22}  {row}  {total:>10}")

    # Correlation note
    print(f"\n  CORRELATION CHECK:")
    mom_pnls  = [r["mom"].get("pnl", 0) for r in all_results]
    rev_pnls  = [r["rev"].get("pnl", 0) for r in all_results]
    if len(mom_pnls) >= 2:
        import numpy as np
        corr = np.corrcoef(mom_pnls, rev_pnls)[0, 1]
        print(f"  Momentum vs Mean-Reversion period PnL correlation: {corr:+.3f}")
        print(f"  (negative = diversifying, positive = correlated)")

    # Smoothness check
    print(f"\n  SMOOTHNESS (combined returns by period):")
    for r in all_results:
        bar = "#" * int(abs(r["combined_ret"]))
        sign = "+" if r["combined_ret"] >= 0 else "-"
        print(f"    {r['label']:<22}  {r['combined_ret']:>+6.1f}%  {sign}{bar}")

    print()


if __name__ == "__main__":
    main()
