#!/usr/bin/env python3
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager
from core.backtest.runner import BacktestRunner

def main():
    parser = argparse.ArgumentParser(description="Systematic Backtester")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--start_date", type=str, default=None)
    parser.add_argument("--end_date", type=str, default=None)
    parser.add_argument("--timeframe", type=str, default='1m')

    args = parser.parse_args()

    # Database setup
    db_manager = DatabaseManager(ROOT / "data")
    runner = BacktestRunner(db_manager)

    # Time setup
    if args.start_date and args.end_date:
        start_time = datetime.strptime(args.start_date, '%Y-%m-%d')
        end_time = datetime.strptime(args.end_date, '%Y-%m-%d')
    else:
        days = args.days or 30
        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)

    print(f"Starting backtest for {args.symbol} using {args.strategy}...")
    run_id = runner.run(
        strategy_id=args.strategy,
        symbol=args.symbol,
        start_time=start_time,
        end_time=end_time,
        timeframe=args.timeframe
    )
    
    print(f"\n[SUCCESS] Backtest completed. Run ID: {run_id}")
    print(f"Results summary available in backtest index.")

if __name__ == "__main__":
    main()
