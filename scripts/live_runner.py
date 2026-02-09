#!/usr/bin/env python3
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
from core.logging import setup_logger

logger = setup_logger("live_runner")

def main():
    parser = argparse.ArgumentParser(description="Phase 8/9 Live Trading Runner")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper", help="Trading mode")
    parser.add_argument("--symbols", nargs="+", required=True, help="Symbols to trade")
    parser.add_argument("--strategies", nargs="+", required=True, help="Strategy IDs to run")
    parser.add_argument("--max-capital", type=float, default=100000.0, help="Max capital limit")
    parser.add_argument("--max-daily-loss", type=float, default=5000.0, help="Max daily loss limit")
    
    args = parser.parse_args()

    # 0. Market Hours Gate
    now = MarketHours.get_ist_now()
    if not MarketHours.is_market_open(now):
        logger.info("Market is closed. Exiting.")
        sys.exit(0)

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

    # 3. Initialize Shared Resources
    db_manager = DatabaseManager(ROOT / "data")

    # 4. Initialize Broker
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

    # 5. Initialize Execution Handler
    exec_config = ExecutionConfig(
        mode=exec_mode,
        max_position_size=args.max_capital,
        max_drawdown_limit=args.max_daily_loss / args.max_capital if args.max_capital > 0 else 0.05
    )
    execution = ExecutionHandler(db_manager, clock, broker, exec_config)
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

    # 6. Initialize Data Consumer
    market_data = LiveDuckDBMarketDataProvider(args.symbols)
    
    # Analytics Provider (Real-time confluence calculation)
    base_analytics = LiveConfluenceProvider(db_manager)
    analytics = CachedAnalyticsProvider(base_analytics)

    # 7. Initialize Strategies
    strategies = []
    for s_id in args.strategies:
        strat = create_strategy(s_id, s_id, {}) 
        if strat:
            strategies.append(strat)
        else:
            logger.error(f"Failed to create strategy: {s_id}")
            sys.exit(1)

    # 8. Initialize Runner
    runner_config = RunnerConfig(
        symbols=args.symbols,
        strategy_ids=args.strategies,
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
