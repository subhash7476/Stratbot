"""CLI entry point for FTMO Challenge system.

Usage:
    python -m ftmo.cli import path/to/US100_M5.csv [--source-tz UTC]
    python -m ftmo.cli backtest [--start 2025-01-01] [--end 2025-12-31]
    python -m ftmo.cli simulate [--window 30] [--step 1]
    python -m ftmo.cli report
"""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("ftmo.cli")


def cmd_import(args):
    from ftmo.ingest import import_csv
    df = import_csv(args.csv_path, source_tz=args.source_tz)
    print(f"Loaded {len(df)} M5 bars")
    print(f"Range: {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")

    # Save to parquet for reuse between CLI commands
    cache = Path(__file__).parent / "cache_m5.parquet"
    df.to_parquet(str(cache), index=False)
    print(f"Cached to {cache}")


def cmd_backtest(args):
    import pandas as pd
    from ftmo.engine import FTMOBacktestEngine
    from ftmo.analytics import compute_trade_analytics
    from ftmo import db

    cache = Path(__file__).parent / "cache_m5.parquet"
    if not cache.exists():
        print("No cached data. Run 'import' first.")
        sys.exit(1)

    df = pd.read_parquet(str(cache))
    print(f"Loaded {len(df)} cached M5 bars")

    engine = FTMOBacktestEngine(df)
    result = engine.run(start_date=args.start, end_date=args.end)

    # Persist
    db.init_db()
    db.clear_tables()
    db.insert_trades([t.to_dict() for t in result.trades])
    db.insert_daily_stats([{
        "session_date": d.session_date,
        "starting_equity": d.starting_equity,
        "ending_equity": d.ending_equity,
        "daily_pnl": d.daily_pnl,
        "daily_pnl_pct": d.daily_pnl_pct,
        "trades_taken": d.trades_taken,
        "wins": d.wins,
        "losses": d.losses,
        "max_equity": d.max_equity,
        "daily_drawdown": d.daily_drawdown,
        "overall_drawdown": d.overall_drawdown,
        "risk_status": d.risk_status,
        "consecutive_losses": d.consecutive_losses,
    } for d in result.daily_stats])

    # Print summary
    analytics = compute_trade_analytics(result.trades)
    print(f"\n{'='*50}")
    print(f"BACKTEST RESULTS")
    print(f"{'='*50}")
    print(f"Total trades:     {analytics['total_trades']}")
    if analytics["total_trades"] > 0:
        print(f"Win rate:         {analytics['win_rate_pct']}%")
        print(f"Avg R:            {analytics['avg_r']}")
        print(f"Expectancy:       {analytics['expectancy']}")
        print(f"Profit factor:    {analytics['profit_factor']}")
        print(f"Total P&L:        ${analytics['total_pnl_dollar']:,.2f}")
        print(f"Max win streak:   {analytics['max_winning_streak']}")
        print(f"Max loss streak:  {analytics['max_losing_streak']}")
        print(f"Exit reasons:     {analytics['exit_reasons']}")
    else:
        print("No trades generated. Check session times vs data coverage.")
        print("The strategy needs pre-market data (4:30-8:00 PM IST = pre-market + open ET).")
    print(f"Final equity:     ${result.final_equity:,.2f}")


def cmd_simulate(args):
    from ftmo.simulation import FTMOSimulator
    from ftmo import db

    trades_raw = db.get_all_trades()
    daily_stats = db.get_daily_stats()
    if not daily_stats:
        print("No data found. Run 'backtest' first.")
        sys.exit(1)

    # All trading dates (including days with 0 trades)
    all_dates = [d["session_date"] for d in daily_stats]

    # Build minimal trade objects
    trades = []
    for t in trades_raw:
        trades.append(type("TR", (), {
            "session_date": t["session_date"],
            "pnl_r": t["pnl_r"],
            "pnl_dollar": t["pnl_dollar"],
            "risk_amount": t["risk_amount"],
            "timestamp_entry": t["timestamp_entry"],
        })())

    sim = FTMOSimulator(trades, all_dates=all_dates)
    results = sim.run_rolling(window_days=args.window, step_days=args.step)

    # Persist
    db.insert_simulations([r.to_dict() for r in results])

    # Summary
    summary = FTMOSimulator.get_aggregate_stats(results)
    print(f"\n{'='*50}")
    print(f"FTMO SIMULATION ({len(results)} windows)")
    print(f"{'='*50}")
    print(f"Pass rate:          {summary.get('pass_rate_pct', 0)}%")
    print(f"Avg days to pass:   {summary.get('avg_days_to_pass', '-')}")
    print(f"Avg max drawdown:   {summary.get('avg_max_drawdown_pct', 0)}%")
    print(f"Worst drawdown:     {summary.get('worst_drawdown_pct', 0)}%")
    print(f"Worst losing streak:{summary.get('worst_losing_streak', 0)}")
    print(f"Median win rate:    {summary.get('median_win_rate', 0)}%")
    print(f"Avg expectancy:     {summary.get('avg_expectancy', 0)}")
    print(f"Daily breaches:     {summary.get('daily_breach_count', 0)}")
    print(f"Overall breaches:   {summary.get('overall_breach_count', 0)}")

    verdict = "PROCEED TO DEMO" if summary.get("pass_rate_pct", 0) >= 65 else "DO NOT PROCEED"
    print(f"\nVerdict: {verdict}")


def cmd_report(args):
    from ftmo.analytics import compute_trade_analytics, compute_daily_summary
    from ftmo.simulation import FTMOSimulator
    from ftmo import db

    trades_raw = db.get_all_trades()
    daily = db.get_daily_stats()
    sims_raw = db.get_simulations()

    if not trades_raw:
        print("No data. Run backtest + simulate first.")
        sys.exit(1)

    # Trade analytics
    trades = [type("T", (), {
        "pnl_r": t["pnl_r"], "pnl_dollar": t["pnl_dollar"], "exit_reason": t["exit_reason"],
    })() for t in trades_raw]

    analytics = compute_trade_analytics(trades)
    daily_sum = compute_daily_summary(daily)

    print(f"\n{'='*60}")
    print(f"FTMO CHALLENGE SYSTEM — FULL REPORT")
    print(f"{'='*60}")
    print(f"\n--- Trade Analytics ---")
    for k, v in analytics.items():
        if k != "r_distribution":
            print(f"  {k:25s}: {v}")

    print(f"\n--- Daily Summary ---")
    for k, v in daily_sum.items():
        print(f"  {k:25s}: {v}")

    if sims_raw:
        from ftmo.simulation import SimulationResult
        results = [SimulationResult(**{k: s[k] for k in SimulationResult.__dataclass_fields__}) for s in sims_raw]
        summary = FTMOSimulator.get_aggregate_stats(results)
        print(f"\n--- Simulation Summary ---")
        for k, v in summary.items():
            print(f"  {k:25s}: {v}")

        verdict = "PROCEED TO DEMO" if summary.get("pass_rate_pct", 0) >= 65 else "DO NOT PROCEED"
        print(f"\n  VERDICT: {verdict}")


def main():
    parser = argparse.ArgumentParser(description="FTMO $50K Challenge System")
    sub = parser.add_subparsers(dest="command", required=True)

    # import
    p_import = sub.add_parser("import", help="Import CSV data")
    p_import.add_argument("csv_path", type=str, help="Path to CSV file")
    p_import.add_argument("--source-tz", default="UTC", help="Source timezone (default: UTC)")

    # backtest
    p_bt = sub.add_parser("backtest", help="Run backtest")
    p_bt.add_argument("--start", default=None, help="Start date (YYYY-MM-DD)")
    p_bt.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")

    # simulate
    p_sim = sub.add_parser("simulate", help="Run rolling FTMO simulations")
    p_sim.add_argument("--window", type=int, default=30, help="Window days (default: 30)")
    p_sim.add_argument("--step", type=int, default=1, help="Step days (default: 1)")

    # report
    sub.add_parser("report", help="Print full report")

    args = parser.parse_args()

    if args.command == "import":
        cmd_import(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "simulate":
        cmd_simulate(args)
    elif args.command == "report":
        cmd_report(args)


if __name__ == "__main__":
    main()
