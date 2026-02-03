import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.clock import ReplayClock
from core.runner import TradingRunner, RunnerConfig
from core.data.duckdb_market_data_provider import DuckDBMarketDataProvider
from core.data.duckdb_analytics_provider import DuckDBAnalyticsProvider
from core.execution.handler import ExecutionHandler, ExecutionConfig, ExecutionMode
from core.execution.position_tracker import PositionTracker
from core.brokers.paper_broker import PaperBroker
from core.strategies.registry import create_strategy
from core.analytics.populator import AnalyticsPopulator

def main():
    parser = argparse.ArgumentParser(description="Systematic Backtester")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--start_date", type=str, default=None)
    parser.add_argument("--end_date", type=str, default=None)

    args = parser.parse_args()

    # Time setup
    if args.start_date and args.end_date:
        start_time = datetime.strptime(args.start_date, '%Y-%m-%d')
        end_time = datetime.strptime(args.end_date, '%Y-%m-%d')
    else:
        days = args.days or 30
        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)
    clock = ReplayClock(start_time)

    # Provider setup
    market_data = DuckDBMarketDataProvider([args.symbol], start_time=start_time, end_time=end_time)
    analytics = DuckDBAnalyticsProvider()
    
    # Pre-load analytics for performance
    if hasattr(analytics, 'pre_load'):
        analytics.pre_load(args.symbol, start_time, end_time)

    # Strategy
    strategy = create_strategy(args.strategy, f"{args.strategy}_bt", {})
    if not strategy:
        print(f"Error: Strategy {args.strategy} not found.")
        sys.exit(1)

    # Phase 2.5: Populate analytics before running the backtest
    print(f"Populating analytics for backtest period: {start_time} to {end_time}")
    populator = AnalyticsPopulator()
    populator.update_all([args.symbol], start_date=start_time, end_date=end_time)
    print("Analytics population complete.")

    # Execution
    broker = PaperBroker(clock)
    exec_config = ExecutionConfig(mode=ExecutionMode.PAPER)
    execution = ExecutionHandler(clock, broker, exec_config)
    position_tracker = PositionTracker()

    runner = TradingRunner(
        config=RunnerConfig(symbols=[args.symbol], strategy_ids=[strategy.strategy_id]),
        market_data_provider=market_data,
        analytics_provider=analytics,
        strategies=[strategy],
        execution_handler=execution,
        position_tracker=position_tracker,
        clock=clock
    )

    stats = runner.run()
    print(f"\nBacktest Stats: {stats}")

if __name__ == "__main__":
    main()
