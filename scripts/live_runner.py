#!/usr/bin/env python3
import sys
import os
import time
import argparse
import logging
import threading
from pathlib import Path
from datetime import datetime

# Add root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.clock import RealTimeClock
from core.runner import TradingRunner, RunnerConfig
from core.data.live_market_provider import LiveDuckDBMarketDataProvider
from core.data.cached_analytics_provider import CachedAnalyticsProvider
from core.data.duckdb_analytics_provider import DuckDBAnalyticsProvider
from core.execution.handler import ExecutionHandler, ExecutionConfig, ExecutionMode
from core.execution.position_tracker import PositionTracker
from core.brokers.paper_broker import PaperBroker
from core.brokers.upstox_adapter import UpstoxAdapter
from core.strategies.registry import create_strategy
from core.alerts.alerter import alerter
from core.auth.credentials import credentials
from ops.session_log import SessionLogger

def main():
    parser = argparse.ArgumentParser(description="Phase 8/9 Live Trading Runner")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper", help="Trading mode")
    parser.add_argument("--symbols", nargs="+", required=True, help="Symbols to trade")
    parser.add_argument("--strategies", nargs="+", required=True, help="Strategy IDs to run")
    parser.add_argument("--max-capital", type=float, default=100000.0, help="Max capital limit")
    parser.add_argument("--max-daily-loss", type=float, default=5000.0, help="Max daily loss limit")
    
    args = parser.parse_args()

    # Setup Logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger("LiveRunner")

    # 1. Initialize Time
    clock = RealTimeClock()
    
    # 2. Check Upstox Connectivity (Daily Refresh Check)
    if args.mode == "live":
        if credentials.needs_daily_refresh:
            logger.error("Upstox Access Token is missing or expired for the day.")
            api_key = credentials.get("api_key")
            redirect_uri = credentials.get("redirect_uri")
            if api_key and redirect_uri:
                from urllib.parse import urlencode
                params = {"response_type": "code", "client_id": api_key, "redirect_uri": redirect_uri}
                auth_url = f"https://api.upstox.com/v2/login/authorization/dialog?{urlencode(params)}"
                logger.info(f"Please refresh your token here: {auth_url}")
            else:
                logger.info("Please configure Upstox API keys in the dashboard or config/credentials.json.")
            sys.exit(1)

    # 3. Initialize Broker
    if args.mode == "paper":
        broker = PaperBroker(clock)
        exec_mode = ExecutionMode.PAPER
    else:
        # Live mode: check credentials.json then fallback to env
        api_key = credentials.get("api_key") or os.environ.get("UPSTOX_API_KEY")
        api_secret = credentials.get("api_secret") or os.environ.get("UPSTOX_API_SECRET")
        access_token = credentials.get("access_token") or os.environ.get("UPSTOX_ACCESS_TOKEN")
        
        if not api_key or not api_secret or not access_token:
            logger.error("Live mode requires UPSTOX credentials. Please configure via UI or environment variables.")
            sys.exit(1)
            
        broker = UpstoxAdapter(api_key, api_secret, access_token, clock)
        exec_mode = ExecutionMode.LIVE

    # 3. Initialize Execution Handler
    exec_config = ExecutionConfig(
        mode=exec_mode,
        max_position_size=args.max_capital,
        max_drawdown_limit=args.max_daily_loss / args.max_capital if args.max_capital > 0 else 0.05
    )
    execution = ExecutionHandler(clock, broker, exec_config)
    position_tracker = PositionTracker()

    # Phase 9: Initialize Session Logger
    session_logger = SessionLogger()
    session_logger.start_session({
        'mode': args.mode,
        'symbols': args.symbols,
        'strategies': args.strategies,
        'max_capital': args.max_capital,
        'max_daily_loss': args.max_daily_loss
    })

    # 4. Initialize Data Consumer
    # Note: Ingestion & Aggregation are now handled by scripts/market_ingestor.py daemon.
    # The Runner purely consumes completed bars from DuckDB.
    market_data = LiveDuckDBMarketDataProvider(args.symbols)
    
    # Analytics Provider
    base_analytics = DuckDBAnalyticsProvider()
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

    # 6. Initialize Runner
    runner_config = RunnerConfig(
        symbols=args.symbols,
        strategy_ids=args.strategies,
        warn_on_missing_analytics=True
    )
    
    runner = TradingRunner(
        config=runner_config,
        market_data_provider=market_data,
        analytics_provider=analytics,
        strategies=strategies,
        execution_handler=execution,
        position_tracker=position_tracker,
        clock=clock
    )

    logger.info(f"Starting {args.mode} trading for {args.symbols} with strategies {args.strategies}")
    alerter.info(f"System starting in {args.mode} mode.")
    session_logger.log_event("## 📈 Activity Summary", f"System starting in {args.mode} mode.")

    # Phase 8: Reconciliation Loop Thread
    def _recon_loop():
        while True:
            time.sleep(60)
            try:
                execution.reconcile_positions()
            except Exception as e:
                logger.error(f"Reconciliation error: {e}")

    threading.Thread(target=_recon_loop, daemon=True).start()

    try:
        # Main Loop
        runner.run()
        
    except KeyboardInterrupt:
        logger.info("Shutdown requested by operator.")
        alerter.warning("System shutting down gracefully.")
        session_logger.log_event("## 🚨 Alerts & Events", "Shutdown requested by operator.")
    except Exception as e:
        logger.error(f"Critical system failure: {e}", exc_info=True)
        alerter.critical(f"CRITICAL SYSTEM FAILURE: {e}")
        session_logger.log_event("## 🛑 Kill Switch Events", f"CRITICAL SYSTEM FAILURE: {e}")
    finally:
        session_logger.close_session()
        logger.info("System stopped.")

if __name__ == "__main__":
    main()
