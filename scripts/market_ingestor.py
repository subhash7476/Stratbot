#!/usr/bin/env python3
import sys
import os
import time
import json
import signal
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.ingestors.websocket_ingestor import WebSocketIngestor
from core.database.ingestors.recovery_manager import RecoveryManager
from core.database.ingestors.db_tick_aggregator import DBTickAggregator
from core.database.utils.market_hours import MarketHours
from core.api.upstox_client import UpstoxClient
from core.auth.credentials import credentials
from core.database.manager import DatabaseManager
from core.messaging.zmq_handler import ZmqPublisher
from core.messaging.telemetry import TelemetryPublisher
from core.logging import setup_logger, TelemetryHandler

import atexit

logger = setup_logger("market_ingestor")

PID_FILE = ROOT / "data" / "market_ingestor.pid"
UNIVERSE_FILE = ROOT / "config" / "market_universe.json"
ZMQ_CONFIG_FILE = ROOT / "config" / "zmq.json"

class MarketIngestorDaemon:
    def __init__(self, db_manager: Optional[DatabaseManager] = None, zmq_config_file: Optional[Path] = None):
        self._is_running = True
        self._is_stopping = False
        self.ingestor = None
        
        # Initialize Database Manager if not provided
        # Ingestor is the SOLE WRITER
        self.db_manager = db_manager or DatabaseManager(ROOT / "data", read_only=False)
        
        # Initialize ZMQ Publisher
        self.zmq_config_file = zmq_config_file or ZMQ_CONFIG_FILE
        self.zmq_config = self._load_zmq_config(self.zmq_config_file)
        self.zmq_publisher = ZmqPublisher(
            host=self.zmq_config["host"],
            port=self.zmq_config["ports"]["market_data_pub"]
        )
        
        # Initialize Telemetry Publisher
        self.telemetry = TelemetryPublisher(
            host=self.zmq_config["host"],
            port=self.zmq_config["ports"]["telemetry_pub"],
            node_name="market_data_node"
        )
        
        # Add telemetry handler to global logger
        logger.addHandler(TelemetryHandler(self.telemetry))
        
        self.aggregator = DBTickAggregator(db_manager=self.db_manager, zmq_publisher=self.zmq_publisher)
        self.symbols = self._load_universe()

        # Ensure cleanup on exit
        atexit.register(self.stop)
        
        # Setup Signal Handlers (only if main thread)
        if threading.current_thread() is threading.main_thread():
            try:
                signal.signal(signal.SIGINT, self._handle_exit)
                signal.signal(signal.SIGTERM, self._handle_exit)
            except ValueError:
                pass 

    def _load_universe(self):
        if not UNIVERSE_FILE.exists():
            logger.error(f"Universe file not found at {UNIVERSE_FILE}")
            sys.exit(1)
        with open(UNIVERSE_FILE, "r") as f:
            data = json.load(f)
            return data.get("symbols", [])

    def _load_zmq_config(self, config_file: Path):
        if not config_file.exists():
            logger.error(f"ZMQ config file not found at {config_file}")
            sys.exit(1)
        with open(config_file, "r") as f:
            return json.load(f)

    def stop(self):
        """Programmatic stop."""
        if self._is_stopping:
            return
        self._is_stopping = True
        
        logger.info("Stopping Market Ingestor Daemon...")
        self._is_running = False
        self._update_websocket_status("CLOSED")
        if self.ingestor:
            self.ingestor.stop()
        
        # Explicitly close ZMQ publishers to release ports
        if hasattr(self, 'zmq_publisher'):
            self.zmq_publisher.close()
        
        # Telemetry is last so we can log other closing steps
        if hasattr(self, 'telemetry'):
            # Give a tiny bit of time for last logs to flush
            time.sleep(0.1)
            self.telemetry.close()
        
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except:
                pass

    def _handle_exit(self, signum, frame):
        logger.info(f"Received signal {signum}. Shutting down...")
        self.stop()
        sys.exit(0)

    def _acquire_lock(self):
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text())
                # Basic check if PID is active
                try:
                    os.kill(pid, 0)
                    logger.error(f"Another instance of MarketIngestor is already running (PID: {pid})")
                    sys.exit(1)
                except OSError:
                    pass # Stale PID
            except (ValueError, Exception):
                pass 
        
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

    def _update_websocket_status(self, status: str):
        """Persists WebSocket status to config database."""
        try:
            with self.db_manager.config_writer() as conn:
                conn.execute("""
                    INSERT INTO websocket_status (key, status, updated_at, pid)
                    VALUES ('singleton', ?, ?, ?)
                    ON CONFLICT (key) DO UPDATE SET
                        status = excluded.status,
                        updated_at = excluded.updated_at,
                        pid = excluded.pid
                """, [status, datetime.now(), os.getpid()])
        except Exception as e:
            logger.error(f"Failed to update websocket_status: {e}")

    def run(self, mock: bool = False):
        # Only acquire file lock if we are NOT running unified (PID check)
        # But for simplicity, we let the unified runner manage it.
        # self._acquire_lock() 
        
        logger.info("Market Ingestor Daemon started.")
        
        if not mock:
            # 1. Recovery on startup
            token = credentials.get("access_token")
            if not token or credentials.needs_daily_refresh:
                logger.error("Fresh Upstox token required. Please login via Dashboard.")
                self._update_heartbeat("ERROR_TOKEN_EXPIRED")
                self._update_websocket_status("DISCONNECTED")
                # return
            else:
                upstox_client = UpstoxClient(access_token=token)

                try:
                    recovery = RecoveryManager(upstox_client, db_manager=self.db_manager)
                    logger.info("Running initial recovery/backfill...")
                    recovery.run_recovery(self.symbols)
                except Exception as e:
                    logger.warning(f"Recovery failed (non-blocking): {e}")

                # 2. Start Ingestor
                self.ingestor = WebSocketIngestor(self.symbols, access_token=token, db_manager=self.db_manager)
                self.ingestor.start()
                self._update_websocket_status("OPEN")
        else:
            logger.info("Running in MOCK mode (No Upstox connection)")
        
        # 3. Main Loop
        logger.info("Entering main aggregation loop (1.5s frequency).")
        
        # Immediate startup heartbeat
        self.telemetry.publish_health({
            "status": "STARTING",
            "timestamp": datetime.now().isoformat()
        })
        
        last_telemetry_ts = 0
        while self._is_running:
            now = MarketHours.get_ist_now()
            
            # Periodic Telemetry (10s)
            if time.time() - last_telemetry_ts > 10:
                try:
                    status = "RUNNING" if self.ingestor and self.ingestor.is_running else "IDLE"
                    if mock: status = "MOCK_RUNNING"
                    
                    self.telemetry.publish_health({
                        "status": status,
                        "symbols_count": len(self.symbols),
                        "timestamp": datetime.now().isoformat()
                    })
                    self.telemetry.publish_log("INFO", f"Heartbeat from market_data_node: {status}")
                except:
                    pass 
                last_telemetry_ts = time.time()

            if MarketHours.is_market_open(now):
                self.aggregator.aggregate_outstanding_ticks(self.symbols)
                self._update_heartbeat("CONNECTED")
                self._update_websocket_status("OPEN")
                time.sleep(1.5) 
            else:
                self.aggregator.aggregate_outstanding_ticks(self.symbols)
                logger.info("Market closed. Sleeping.")
                self._update_heartbeat("IDLE (Market Closed)")
                self._update_websocket_status("CLOSED")
                time.sleep(60) 

if __name__ == "__main__":
    daemon = MarketIngestorDaemon()
    daemon.run()
