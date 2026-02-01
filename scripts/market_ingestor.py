#!/usr/bin/env python3
import sys
import os
import time
import logging
import json
import signal
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.data.websocket_ingestor import WebSocketIngestor
from core.data.recovery_manager import RecoveryManager
from core.data.db_tick_aggregator import DBTickAggregator
from core.data.market_hours import MarketHours
from core.api.upstox_client import UpstoxClient
from core.auth.credentials import credentials
from core.data.duckdb_client import db_cursor

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(ROOT / "logs" / "market_ingestor.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("MarketIngestor")

PID_FILE = ROOT / "data" / "market_ingestor.pid"
UNIVERSE_FILE = ROOT / "config" / "market_universe.json"

class MarketIngestorDaemon:
    def __init__(self):
        self._is_running = True
        self.ingestor = None
        self.aggregator = DBTickAggregator()
        self.symbols = self._load_universe()
        
        # Setup Signal Handlers
        signal.signal(signal.SIGINT, self._handle_exit)
        signal.signal(signal.SIGTERM, self._handle_exit)

    def _load_universe(self):
        if not UNIVERSE_FILE.exists():
            logger.error(f"Universe file not found at {UNIVERSE_FILE}")
            sys.exit(1)
        with open(UNIVERSE_FILE, "r") as f:
            data = json.load(f)
            return data.get("symbols", [])

    def _handle_exit(self, signum, frame):
        logger.info(f"Received signal {signum}. Shutting down...")
        self._is_running = False
        if self.ingestor:
            self.ingestor.stop()
        if PID_FILE.exists():
            PID_FILE.unlink()
        sys.exit(0)

    def _acquire_lock(self):
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text())
                if os.path.exists(f"/proc/{pid}"): # Unix check
                    logger.error(f"Another instance of MarketIngestor is already running (PID: {pid})")
                    sys.exit(1)
            except (ValueError, ProcessLookupError, Exception):
                pass # Stale PID file
        
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))

    def _update_heartbeat(self, status: str):
        """Updates a heartbeat file for UI monitoring."""
        heartbeat_file = ROOT / "logs" / "market_ingestor_status.json"
        try:
            with open(heartbeat_file, "w") as f:
                json.dump({
                    "status": status,
                    "last_heartbeat": datetime.now().isoformat(),
                    "pid": os.getpid(),
                    "symbols": self.symbols
                }, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to update heartbeat: {e}")

    def run(self):
        self._acquire_lock()
        logger.info("Market Ingestor Daemon started.")
        
        # 1. Recovery on startup
        token = credentials.get("access_token")
        if not token or credentials.needs_daily_refresh:
            logger.error("Fresh Upstox token required. Please login via Dashboard.")
            self._update_heartbeat("ERROR_TOKEN_EXPIRED")
            return

        upstox_client = UpstoxClient(access_token=token)
        recovery = RecoveryManager(upstox_client)
        
        logger.info("Running initial recovery/backfill...")
        recovery.run_recovery(self.symbols)
        
        # 2. Start Ingestor
        self.ingestor = WebSocketIngestor(self.symbols, access_token=token)
        self.ingestor.start()
        
        # 3. Main Loop
        logger.info("Entering main aggregation loop (1s frequency).")
        while self._is_running:
            now = MarketHours.get_ist_now()
            
            if MarketHours.is_market_open(now):
                # Market is open: Aggressive aggregation
                self.aggregator.aggregate_outstanding_ticks(self.symbols)
                self._update_heartbeat("CONNECTED")
                time.sleep(1.5) # Poll every 1.5 seconds
            else:
                # Market is closed: Idle mode
                # Final aggregation to close out the session
                self.aggregator.aggregate_outstanding_ticks(self.symbols)
                
                # Check when market opens next
                logger.info("Market closed. Sleeping until next open.")
                self._update_heartbeat("IDLE (Market Closed)")
                time.sleep(60) # Wake up every minute to check gating

if __name__ == "__main__":
    daemon = MarketIngestorDaemon()
    daemon.run()
