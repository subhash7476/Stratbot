#!/usr/bin/env python3
"""
Market Data Node
----------------
Standalone process for market data ingestion and distribution.
SOLE writer to DuckDB.
"""
import sys
import argparse
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.market_ingestor import MarketIngestorDaemon
from core.database.manager import DatabaseManager
from core.logging import setup_logger

logger = setup_logger("market_data_node")

def main():
    parser = argparse.ArgumentParser(description="Market Data Node")
    parser.add_argument("--mock", action="store_true", help="Run in mock mode (no Upstox)")
    parser.add_argument("--zmq-config", type=str, help="Path to custom ZMQ config")
    args = parser.parse_args()

    # Ingestor node is the SOLE WRITER to DuckDB
    db_manager = DatabaseManager(ROOT / "data", read_only=False)

    zmq_config_file = Path(args.zmq_config) if args.zmq_config else None
    daemon = MarketIngestorDaemon(db_manager=db_manager, zmq_config_file=zmq_config_file)

    logger.info("=" * 70)
    logger.info("MARKET DATA NODE - Starting")
    logger.info("Role: Sole DuckDB Writer, ZMQ Data Publisher")
    if args.mock:
        logger.info("Mode: MOCK (Telemetry only)")
    logger.info("=" * 70)

    daemon.run(mock=args.mock)

if __name__ == "__main__":
    main()
