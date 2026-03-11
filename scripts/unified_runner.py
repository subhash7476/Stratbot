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
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from flask_app import create_app
from scripts.init_refactored_db import init_all
from scripts.market_ingestor import MarketIngestorDaemon
from core.database.manager import DatabaseManager


def run_paper_trading(db_manager: DatabaseManager, stop_event: threading.Event, app=None):
    """Background thread for Stock Day-Type paper trading."""
    try:
        from scripts.stock_daytype_runner import StockDaytypeRunner

        runner = StockDaytypeRunner(db_manager, broker="paper")
        if app is not None:
            app.paper_runner = runner
        runner.run(stop_event)
    except Exception as e:
        print(f"WARNING: Paper trading thread failed: {e}")


def run_v9_pm_trading(db_manager: DatabaseManager, stop_event: threading.Event, app=None):
    """Background thread for V9 PM Scalper paper trading."""
    try:
        from scripts.v9_pm_runner import V9PMRunner

        runner = V9PMRunner(db_manager)
        if app is not None:
            app.v9_runner = runner
        runner.run(stop_event)
    except Exception as e:
        print(f"WARNING: V9 PM trading thread failed: {e}")


def run_nifty_shield(db_manager: DatabaseManager, stop_event: threading.Event, app=None):
    """Background thread for NiftyShield weekly options selling."""
    try:
        from scripts.nifty_shield_runner import NiftyShieldRunner

        runner = NiftyShieldRunner(db_manager)
        if app is not None:
            app.nifty_shield_runner = runner
        runner.run(stop_event)
    except Exception as e:
        print(f"WARNING: NiftyShield thread failed: {e}")


def run_ingestor(db_manager: DatabaseManager, stop_event: threading.Event):
    """Background thread for market ingestion."""
    daemon = None
    try:
        daemon = MarketIngestorDaemon(db_manager=db_manager)

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


if __name__ == "__main__":
    print("=" * 60)
    print("UNIFIED TRADING BOT SERVER (Windows Mode)")
    print("=" * 60)

    os.environ["UNIFIED_MODE"] = "1"

    init_all()

    data_root = ROOT / "data"
    db_manager = DatabaseManager(data_root)

    stop_event = threading.Event()
    ingestor_thread = threading.Thread(
        target=run_ingestor,
        args=(db_manager, stop_event),
        name="IngestorThread",
    )
    ingestor_thread.start()
    print("Ingestor background thread started.")

    app = create_app()
    app.db_manager = db_manager

    paper_thread = threading.Thread(
        target=run_paper_trading,
        args=(db_manager, stop_event, app),
        name="PaperTradingThread",
        daemon=True,
    )
    paper_thread.start()
    print("Stock Day-Type paper trading thread started.")

    v9_thread = threading.Thread(
        target=run_v9_pm_trading,
        args=(db_manager, stop_event, app),
        name="V9PMTradingThread",
        daemon=True,
    )
    v9_thread.start()
    print("V9 PM Scalper paper trading thread started.")

    ns_thread = threading.Thread(
        target=run_nifty_shield,
        args=(db_manager, stop_event, app),
        name="NiftyShieldThread",
        daemon=True,
    )
    ns_thread.start()
    print("NiftyShield weekly options selling thread started.")

    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", 5000))

    print(f"Starting Dashboard on http://{host}:{port}")
    try:
        app.run(host=host, port=port, debug=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received.")
    finally:
        print("Initiating shutdown...")
        stop_event.set()

        if hasattr(app, "telemetry_bridge"):
            app.telemetry_bridge.stop()

        print("Waiting for background threads...")
        ingestor_thread.join(timeout=5)
        print("Shutdown complete.")
