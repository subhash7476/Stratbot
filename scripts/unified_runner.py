#!/usr/bin/env python3
"""
Unified Runner
--------------
Starts both the Market Ingestor and the Flask Server in a single process.
This avoids DuckDB file lock issues on Windows.
"""
import sys
import os
import threading
import time
import logging
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from flask_app import create_app
from scripts.init_refactored_db import init_all
from scripts.market_ingestor import MarketIngestorDaemon
from core.database.manager import DatabaseManager

def run_ingestor(db_manager: DatabaseManager, stop_event: threading.Event):
    """Background thread for market ingestion."""
    daemon = None
    try:
        daemon = MarketIngestorDaemon(db_manager=db_manager)
        
        # Start a monitor thread to stop daemon when stop_event is set
        def monitor():
            stop_event.wait()
            if daemon:
                daemon.stop()
        
        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()
        
        daemon.run()
    except Exception as e:
        print(f"CRITICAL: Ingestor thread crashed: {e}")
    finally:
        if daemon:
            daemon.stop()

if __name__ == '__main__':
    print("="*60)
    print("UNIFIED TRADING BOT SERVER (Windows Mode)")
    print("="*60)
    
    # 1. Set Unified Mode for DuckDB robustness
    os.environ['UNIFIED_MODE'] = '1'
    
    # 2. Initialize Isolated Databases
    init_all()
    
    # 2. Initialize Central Database Manager
    data_root = ROOT / "data"
    db_manager = DatabaseManager(data_root)
    
    # 3. Start Ingestor Thread
    stop_event = threading.Event()
    ingestor_thread = threading.Thread(
        target=run_ingestor, 
        args=(db_manager, stop_event),
        name="IngestorThread"
    )
    ingestor_thread.start()
    print("Ingestor background thread started.")
    
    # 4. Start Flask App
    app = create_app()
    # Ensure app uses the same manager instance
    app.db_manager = db_manager
    
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_PORT', 5000))
    
    print(f"Starting Dashboard on http://{host}:{port}")
    try:
        # DISABLE reloader to prevent double-process locking issues
        app.run(host=host, port=port, debug=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received.")
    finally:
        print("Initiating shutdown...")
        stop_event.set()
        
        # Shutdown telemetry bridge if exists
        if hasattr(app, 'telemetry_bridge'):
            app.telemetry_bridge.stop()
            
        print("Waiting for background threads...")
        ingestor_thread.join(timeout=5)
        print("Shutdown complete.")
