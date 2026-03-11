"""
Options Structural Engine - Background Runner
---------------------------------------------
Starts the options data poller that fetches and publishes structural data.

Usage:
    python scripts/run_options_engine.py

This runs in the background and:
1. Fetches option chain from Upstox V3 every 5 seconds
2. Calculates structural metrics (PCR, GEX, OI analysis)
3. Publishes via ZMQ for Flask SSE distribution
4. Caches in DuckDB for historical analysis
"""

import sys
import os
import signal
import time
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.logging import setup_logger
from core.messaging.options_publisher import OptionsPoller

logger = setup_logger("options_runner")

# Global poller instance
poller = None


def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    logger.info("Shutdown signal received...")
    if poller:
        poller.stop()
    sys.exit(0)


def main():
    global poller
    
    logger.info("=" * 60)
    logger.info("Options Structural Engine - Starting")
    logger.info("=" * 60)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Get indices to track from command line
    indices = sys.argv[1:] if len(sys.argv) > 1 else ["NIFTY", "BANKNIFTY"]
    
    logger.info(f"Tracking indices: {indices}")
    logger.info("Starting background poller...")
    
    # Create and start poller
    # ZMQ port 5557 for options data
    poller = OptionsPoller(
        zmq_host="127.0.0.1",
        zmq_port=5557,
        indices=indices
    )
    
    poller.start()
    
    logger.info("Options engine running. Press Ctrl+C to stop.")
    logger.info("Access dashboard at: http://127.0.0.1:5000/options/")
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        if poller:
            poller.stop()


if __name__ == "__main__":
    main()
