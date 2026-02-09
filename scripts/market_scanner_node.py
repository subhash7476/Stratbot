#!/usr/bin/env python3
"""
Market Scanner Node
------------------
Standalone process for scanning a broad universe of symbols.
Generates signals but DOES NOT execute trades.
READ-ONLY for DuckDB.
"""
import sys
import os
import argparse
import json
import threading
from pathlib import Path

# Add root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.clock import RealTimeClock
from core.runner import TradingRunner, RunnerConfig
from core.database.providers import (
    ZmqMarketDataProvider,
    CachedAnalyticsProvider,
    LiveConfluenceProvider,
)
from core.database.manager import DatabaseManager
from core.strategies.registry import create_strategy
from core.logging import setup_logger

class MockExecutionHandler:
    """Drops all signals to ensure scanner never executes."""
    def __init__(self, config=None):
        self.config = type('obj', (object,), {'mode': type('obj', (object,), {'value': 'scanner'})})
    def process_signal(self, signal, price):
        return None

def main():
    parser = argparse.ArgumentParser(description="Standalone Market Scanner Node")
    parser.add_argument("--symbols", nargs="+", required=True, help="Symbols to scan")
    parser.add_argument("--strategies", nargs="+", default=["confluence_consumer"], help="Strategy IDs to use for scanning")

    args = parser.parse_args()

    logger = setup_logger("scanner")

    clock = RealTimeClock()
    db_manager = DatabaseManager(ROOT / "data", read_only=True)

    # 1. ZMQ Consumer
    zmq_config_path = ROOT / "config" / "zmq.json"
    with open(zmq_config_path, "r") as f:
        zmq_config = json.load(f)
    
    market_data = ZmqMarketDataProvider(
        args.symbols,
        zmq_host=zmq_config["host"],
        zmq_port=zmq_config["ports"]["market_data_pub"],
        db_manager=db_manager
    )
    
    # 2. Analytics
    base_analytics = LiveConfluenceProvider(db_manager)
    analytics = CachedAnalyticsProvider(base_analytics)

    # 3. Strategies
    strategies = []
    for s_id in args.strategies:
        strat = create_strategy(s_id, s_id, {})
        if strat:
            strategies.append(strat)

    # 4. Initialize Runner with Mock Execution
    runner_config = RunnerConfig(
        symbols=args.symbols,
        strategy_ids=args.strategies,
        log_trades=False,
        warn_on_missing_analytics=False
    )
    
    runner = TradingRunner(
        config=runner_config,
        db_manager=db_manager,
        market_data_provider=market_data,
        analytics_provider=analytics,
        strategies=strategies,
        execution_handler=MockExecutionHandler(),
        position_tracker=type('obj', (object,), {'get_position_quantity': lambda s: 0, 'get_all_positions': lambda: {}}),
        clock=clock
    )

    logger.info("=" * 70)
    logger.info("MARKET SCANNER NODE - Starting")
    logger.info(f"Role: Opportunity Scanning, ZMQ Data Subscriber")
    logger.info(f"DuckDB Mode: STRICT READ-ONLY")
    logger.info(f"Execution: DISABLED (Mock)")
    logger.info("=" * 70)

    try:
        runner.run()
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
    finally:
        market_data.stop()
        logger.info("System stopped.")

if __name__ == "__main__":
    main()
