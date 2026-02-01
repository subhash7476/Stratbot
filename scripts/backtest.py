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

def main():
    parser = argparse.ArgumentParser(description="Systematic Backtester")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--days", type=int, default=30)
    
    args = parser.parse_args()
    
    # Time setup
    end_time = datetime.now()
    start_time = end_time - timedelta(days=args.days)
    clock = ReplayClock(start_time)
    
    # Provider setup
    market_data = DuckDBMarketDataProvider([args.symbol], start_time=start_time, end_time=end_time)
    analytics = DuckDBAnalyticsProvider()
    
    # Strategy
    strategy = create_strategy(args.strategy, f"{args.strategy}_bt", {})
    
    # Execution
    broker = PaperBroker(clock)
    exec_config = ExecutionConfig(mode=ExecutionMode.PAPER)
    execution = ExecutionHandler(clock, broker, exec_config)
    position_tracker = PositionTracker()
    
    runner = TradingRunner(
        config=RunnerConfig(symbols=[args.symbol], strategy_ids=[args.strategy]),
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
