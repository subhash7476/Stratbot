#!/usr/bin/env python3
"""
Unified Live Runner
-------------------
Runs Market Ingestor, Trading Runner, and Flask Dashboard in the same process
to avoid DuckDB file locking issues on Windows.
"""
import sys
import os
import time
import argparse
import threading
from pathlib import Path
from datetime import datetime

# Add root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.utils import MarketHours
from core.clock import RealTimeClock
from core.runner import TradingRunner, RunnerConfig
from core.database.providers import (
    LiveDuckDBMarketDataProvider,
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
from core.alerts.alerter import alerter
from core.auth.credentials import credentials
from ops.session_log import SessionLogger
from scripts.market_ingestor import MarketIngestorDaemon
from flask_app import create_app
from core.logging import setup_logger

logger = setup_logger("unified_live_runner")

def run_ingestor(db_manager: DatabaseManager):
    """Background thread for market ingestion."""
    try:
        daemon = MarketIngestorDaemon(db_manager=db_manager)
        daemon.run()
    except Exception as e:
        logger.error(f"CRITICAL: Ingestor thread crashed: {e}")

def main():
    parser = argparse.ArgumentParser(description="Unified Live Trading Runner")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper", help="Trading mode")
    parser.add_argument("--symbols", nargs="+", required=True, help="Symbols to trade")
    parser.add_argument("--strategies", nargs="+", required=True, help="Strategy IDs to run")
    parser.add_argument("--max-capital", type=float, default=100000.0, help="Max capital limit")
    parser.add_argument("--max-daily-loss", type=float, default=5000.0, help="Max daily loss limit")
    parser.add_argument("--max-bars", type=int, default=None, help="Max bars to process")
    parser.add_argument("--zmq", action="store_true", help="Use ZMQ fast-path for market data")
    parser.add_argument("--no-dashboard", action="store_true", help="Disable dashboard")
    
    args = parser.parse_args()

    # 0. Market Hours Gate
    now = MarketHours.get_ist_now()
    if not MarketHours.is_market_open(now):
        logger.info("Market is closed. Exiting.")
        # sys.exit(0) 

    # 1. Initialize Time
    clock = RealTimeClock()
    
    # 2. Check Upstox Connectivity
    if args.mode == "live":
        if credentials.needs_daily_refresh:
            logger.error("Upstox Access Token is missing or expired for the day.")
            sys.exit(1)

    # 3. Initialize Shared Resources
    os.environ['UNIFIED_MODE'] = '1'
    # Unified mode uses a single DatabaseManager with thread synchronization
    # Ingestor thread needs write access to live buffer
    # Trading thread reads from live buffer (coordinated via thread locks)
    db_manager = DatabaseManager(ROOT / "data")

    # 4. Start Ingestor Thread
    logger.info("Starting ingestor thread...")
    ingestor_thread = threading.Thread(
        target=run_ingestor, 
        args=(db_manager,),
        name="IngestorThread",
        daemon=True
    )
    ingestor_thread.start()
    
    # Wait for ingestor to start writing
    time.sleep(2)

    # 5. Initialize Broker
    if args.mode == "paper":
        broker = PaperBroker(clock)
        exec_mode = ExecutionMode.PAPER
    else:
        api_key = credentials.get("api_key")
        api_secret = credentials.get("api_secret")
        access_token = credentials.get("access_token")
        broker = UpstoxAdapter(api_key, api_secret, access_token, clock)
        exec_mode = ExecutionMode.LIVE

    # 6. Initialize Execution Handler
    exec_config = ExecutionConfig(
        mode=exec_mode,
        max_position_size=args.max_capital,
        max_drawdown_limit=args.max_daily_loss / args.max_capital if args.max_capital > 0 else 0.05
    )
    execution = ExecutionHandler(db_manager, clock, broker, exec_config)
    position_tracker = PositionTracker()

    # 7. Initialize Session Logger
    session_logger = SessionLogger()
    session_logger.start_session({
        'mode': args.mode,
        'symbols': args.symbols,
        'strategies': args.strategies,
        'max_capital': args.max_capital,
        'max_daily_loss': args.max_daily_loss,
        'zmq_enabled': args.zmq
    })

    # 8. Initialize Data Consumer
    if args.zmq:
        zmq_config_path = ROOT / "config" / "zmq.json"
        with open(zmq_config_path, "r") as f:
            import json
            zmq_config = json.load(f)
        
        market_data = ZmqMarketDataProvider(
            args.symbols,
            zmq_host=zmq_config["host"],
            zmq_port=zmq_config["ports"]["market_data_pub"],
            db_manager=db_manager
        )
        logger.info(f"Using ZMQ Market Data Provider on {zmq_config['host']}:{zmq_config['ports']['market_data_pub']}")
    else:
        base_market_data = LiveDuckDBMarketDataProvider(args.symbols, db_manager=db_manager)
        
        # Check if any strategy requires resampling (PixityAI needs 15m)
        needs_resampling = any(s_id == "pixityAI_meta" for s_id in args.strategies)
        if needs_resampling:
            from core.database.providers.resampling_wrapper import ResamplingMarketDataProvider
            logger.info("Enabling 15m resampling for PixityAI")
            market_data = ResamplingMarketDataProvider(base_market_data, target_tf="15m", db_manager=db_manager)
        else:
            market_data = base_market_data
    
    # Analytics Provider
    base_analytics = LiveConfluenceProvider(db_manager)
    analytics = CachedAnalyticsProvider(base_analytics)

    # 9. Initialize Strategies
    strategies = []
    for s_id in args.strategies:
        strat = create_strategy(s_id, s_id, {}) 
        if strat:
            strategies.append(strat)
        else:
            logger.error(f"Failed to create strategy: {s_id}")
            sys.exit(1)

    # 10. Initialize Runner
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
        clock=clock
    )

    # 11. Start Trading Runner
    logger.info(f"Starting trading runner for {args.symbols}")
    if args.no_dashboard:
        # Run in main thread to see output and exit when done
        runner.run()
        session_logger.close_session()
        logger.info("System stopped.")
    else:
        trading_thread = threading.Thread(
            target=runner.run,
            name="TradingThread",
            daemon=True
        )
        trading_thread.start()

        # 12. Start Flask App (Main Thread)
        logger.info("Starting Dashboard...")
        app = create_app()
        app.db_manager = db_manager
        
        host = os.environ.get('FLASK_HOST', '127.0.0.1')
        port = int(os.environ.get('FLASK_PORT', 5000))
        
        alerter.info(f"Unified system starting in {args.mode} mode.")
        app.run(host=host, port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
