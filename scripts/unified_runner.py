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
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from flask_app import create_app
from scripts.init_refactored_db import init_all
from scripts.market_ingestor import MarketIngestorDaemon
from scripts.eod_rollover import EODRollover
from core.database.manager import DatabaseManager

IST = ZoneInfo("Asia/Kolkata")
EOD_HOUR   = 16   # 16:00 IST — 30 min after NSE close (15:30)
EOD_MINUTE = 0

# Persists the last-rolled date across restarts so a script restart after
# market close never re-triggers rollover for the same day.
_LAST_ROLLOVER_FILE = ROOT / "data" / "live_buffer" / ".last_rollover"


def _read_last_rolled() -> "date | None":
    """Read the persisted last-rollover date from disk."""
    try:
        if _LAST_ROLLOVER_FILE.exists():
            return date.fromisoformat(_LAST_ROLLOVER_FILE.read_text().strip())
    except Exception:
        pass
    return None


def _write_last_rolled(d: date) -> None:
    """Persist the last-rollover date to disk."""
    try:
        _LAST_ROLLOVER_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LAST_ROLLOVER_FILE.write_text(d.isoformat())
    except Exception as e:
        logging.getLogger("eod_scheduler").warning(f"[EOD] Could not persist last_rolled: {e}")


def run_eod_scheduler(db_manager: DatabaseManager, stop_event: threading.Event):
    """
    Background thread: fires EOD rollover once a day at 16:00 IST.
    Promotes live buffer -> historical archive and reinitialises empty buffers.
    Persists last_rolled to disk — restarts after market close never re-trigger.
    Only rolls when the OAuth token is refreshed on a new trading day.
    """
    logger = logging.getLogger("eod_scheduler")
    rollover = EODRollover(db_manager, ROOT / "data")

    # Restore from disk so a restart at 18:00 doesn't roll over again
    last_rolled: date | None = _read_last_rolled()
    if last_rolled:
        logger.info(f"[EOD] Restored last_rolled={last_rolled} from disk (no re-roll on restart).")

    while not stop_event.is_set():
        now_ist = datetime.now(IST)
        today   = now_ist.date()

        # Fire if past 16:00 IST today and not already rolled today
        if (now_ist.hour, now_ist.minute) >= (EOD_HOUR, EOD_MINUTE) and last_rolled != today:
            # Only roll on weekdays (Mon-Fri)
            if today.weekday() < 5:
                logger.info(f"[EOD] Running rollover for {today} ...")
                try:
                    rollover.execute(today)
                    last_rolled = today
                    _write_last_rolled(today)   # persist so restart won't re-roll
                    logger.info(f"[EOD] Rollover complete for {today}")
                except Exception as e:
                    logger.error(f"[EOD] Rollover FAILED for {today}: {e}")
            else:
                last_rolled = today  # weekend — mark done, skip
                _write_last_rolled(today)

        stop_event.wait(timeout=60)  # check every minute


def run_paper_trading(db_manager: DatabaseManager, stop_event: threading.Event, app=None):
    """Background thread for Stock Day-Type paper trading."""
    try:
        from scripts.stock_daytype_runner import StockDaytypeRunner
        runner = StockDaytypeRunner(db_manager, broker="paper")
        # Attach runner to Flask app so routes can access live state
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

def refresh_instrument_master():
    """Download latest NSE_FO instrument master from Upstox (best-effort, non-blocking)."""
    logger = logging.getLogger("instrument_master")
    try:
        from scripts.fetch_instrument_master import refresh
        n = refresh()
        logger.info(f"[InstrumentMaster] Refreshed: {n:,} NSE_FO instruments")
        print(f"Instrument master refreshed: {n:,} NSE_FO instruments.")
    except Exception as e:
        logger.warning(f"[InstrumentMaster] Refresh failed (non-fatal): {e}")
        print(f"WARNING: Instrument master refresh failed: {e}")


if __name__ == '__main__':
    print("="*60)
    print("UNIFIED TRADING BOT SERVER (Windows Mode)")
    print("="*60)

    # 1. Set Unified Mode for DuckDB robustness
    os.environ['UNIFIED_MODE'] = '1'

    # 2. Initialize Isolated Databases
    init_all()

    # 2b. Refresh NSE_FO instrument master (daily, after OAuth)
    refresh_instrument_master()
    
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

    # 3b. Start EOD Rollover Scheduler Thread
    eod_thread = threading.Thread(
        target=run_eod_scheduler,
        args=(db_manager, stop_event),
        name="EODRolloverThread",
        daemon=True,
    )
    eod_thread.start()
    print(f"EOD rollover scheduler started (fires daily at {EOD_HOUR:02d}:{EOD_MINUTE:02d} IST).")
    
    # 4. Start Flask App
    app = create_app()
    # Ensure app uses the same manager instance
    app.db_manager = db_manager

    # 3c. Start Stock Day-Type Paper Trading Thread
    paper_thread = threading.Thread(
        target=run_paper_trading,
        args=(db_manager, stop_event, app),
        name="PaperTradingThread",
        daemon=True,
    )
    paper_thread.start()
    print("Stock Day-Type paper trading thread started.")

    # 3d. Start V9 PM Scalper Paper Trading Thread
    v9_thread = threading.Thread(
        target=run_v9_pm_trading,
        args=(db_manager, stop_event, app),
        name="V9PMTradingThread",
        daemon=True,
    )
    v9_thread.start()
    print("V9 PM Scalper paper trading thread started.")

    # 3e. Start NiftyShield Options Selling Thread
    ns_thread = threading.Thread(
        target=run_nifty_shield,
        args=(db_manager, stop_event, app),
        name="NiftyShieldThread",
        daemon=True,
    )
    ns_thread.start()
    print("NiftyShield weekly options selling thread started.")

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
