#!/usr/bin/env python3
"""
Strategy Runner Node
--------------------
Standalone process for strategy execution and signal generation.
Uses ZMQ fast-path for market data.
READ-ONLY for DuckDB.
"""
import sys
import os
import argparse
import json
import threading
from datetime import datetime
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
from core.execution.handler import ExecutionHandler, ExecutionConfig, ExecutionMode
from core.execution.position_tracker import PositionTracker
from core.brokers.paper_broker import PaperBroker
from core.brokers.upstox_adapter import UpstoxAdapter
from core.strategies.registry import create_strategy
from core.auth.credentials import credentials
from ops.session_log import SessionLogger
from core.messaging.telemetry import TelemetryPublisher
from core.logging import setup_logger

logger = setup_logger("strategy_runner")

def main():
    parser = argparse.ArgumentParser(description="Standalone Strategy Runner Node")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper", help="Trading mode")
    parser.add_argument("--symbols", nargs="+", required=True, help="Symbols to trade")
    parser.add_argument("--strategies", nargs="+", required=True, help="Strategy IDs to run")
    parser.add_argument("--max-capital", type=float, default=100000.0, help="Max capital limit")
    parser.add_argument("--max-daily-loss", type=float, default=5000.0, help="Max daily loss limit")
    parser.add_argument("--max-bars", type=int, default=None, help="Max bars to process")

    args = parser.parse_args()

    clock = RealTimeClock()
    
    # 1. Initialize Shared Resources (READ-ONLY for DuckDB)
    db_manager = DatabaseManager(ROOT / "data", read_only=True)

    # 2. Initialize Broker
    if args.mode == "paper":
        broker = PaperBroker(clock)
        exec_mode = ExecutionMode.PAPER
    else:
        api_key = credentials.get("api_key")
        api_secret = credentials.get("api_secret")
        access_token = credentials.get("access_token")
        broker = UpstoxAdapter(api_key, api_secret, access_token, clock)
        exec_mode = ExecutionMode.LIVE

    # 3. Initialize Execution & Risk (Single-process authority within this node for Phase 2)
    exec_config = ExecutionConfig(
        mode=exec_mode,
        max_position_size=args.max_capital,
        max_drawdown_limit=args.max_daily_loss / args.max_capital if args.max_capital > 0 else 0.05
    )
    execution = ExecutionHandler(db_manager, clock, broker, exec_config)
    position_tracker = PositionTracker()

    # 4. Initialize Data Consumer (ZMQ Mandatory for Decoupled Node)
    zmq_config_path = ROOT / "config" / "zmq.json"
    with open(zmq_config_path, "r") as f:
        zmq_config = json.load(f)
    
    market_data = ZmqMarketDataProvider(
        args.symbols,
        zmq_host=zmq_config["host"],
        zmq_port=zmq_config["ports"]["market_data_pub"],
        db_manager=db_manager
    )
    
    # Analytics Provider
    base_analytics = LiveConfluenceProvider(db_manager)
    analytics = CachedAnalyticsProvider(base_analytics)

    # 5. Initialize Strategies
    strategies = []
    for s_id in args.strategies:
        strat = create_strategy(s_id, s_id, {}) 
        if strat:
            strategies.append(strat)
        else:
            logger.error(f"Failed to create strategy: {s_id}")
            sys.exit(1)

    # 6. Initialize Telemetry
    telemetry = TelemetryPublisher(
        host=zmq_config["host"],
        port=zmq_config["ports"]["telemetry_pub"],
        node_name="strategy_runner"
    )

    # 7. Initialize Runner
    runner_config = RunnerConfig(
        symbols=args.symbols,
        strategy_ids=args.strategies,
        max_bars=args.max_bars,
        warn_on_missing_analytics=True
    )
    
    runner = TradingRunner(
        config=runner_config,
        db_manager=db_manager,
        market_data_provider=market_data,
        analytics_provider=analytics,
        strategies=strategies,
        execution_handler=execution,
        position_tracker=position_tracker,
        clock=clock,
        telemetry=telemetry
    )

    def publish_telemetry():
        import threading
        import time
        while runner.is_running:
            try:
                # 1. Metrics Snapshot
                stats = execution.get_stats()
                telemetry.publish_metrics({
                    "daily_pnl": stats.get("daily_pnl", 0),
                    "drawdown_pct": stats.get("drawdown_pct", 0),
                    "trade_count": stats.get("trade_count", 0),
                    "win_rate": stats.get("win_rate", 0)
                })

                # 2. Positions Snapshot
                positions = position_tracker.get_all_positions()
                telemetry.publish_positions({
                    "count": len(positions),
                    "positions": {s: p.quantity for s, p in positions.items()}
                })

                # 3. Health Snapshot
                telemetry.publish_health({
                    "status": "TRADING",
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as e:
                # Swallowed per guardrails
                pass
            time.sleep(10)

    telemetry_thread = threading.Thread(target=publish_telemetry, daemon=True, name="TelemetryThread")
    telemetry_thread.start()

    logger.info("=" * 70)
    logger.info("STRATEGY RUNNER NODE - Starting")
    logger.info(f"Role: Strategy Logic & Execution Authority, ZMQ Data Subscriber")
    logger.info(f"DuckDB Mode: STRICT READ-ONLY")
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
