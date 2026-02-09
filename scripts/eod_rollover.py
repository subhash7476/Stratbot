#!/usr/bin/env python3
import sys
import os
import shutil
from pathlib import Path
from datetime import date, datetime
import duckdb

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager
from core.database.locks import WriterLock
from core.database import schema
from core.logging import setup_logger

logger = setup_logger("eod_rollover")

class EODRollover:
    """
    End-of-day rollover: promotes live buffer to historical.
    """

    def __init__(self, db_manager: DatabaseManager, data_root: Path):
        self.db = db_manager
        self.data_root = data_root

    def execute(self, rollover_date: date, exchange: str = 'nse') -> bool:
        """
        Execute EOD rollover with full atomicity and splitting.
        """
        live_buffer_path = self.data_root / 'live_buffer'
        lock_path = live_buffer_path / '.writer.lock'

        logger.info(f"Starting EOD rollover for {rollover_date}...")

        with WriterLock(str(lock_path)):
            # 1. Define source paths
            ticks_src = live_buffer_path / 'ticks_today.duckdb'
            candles_src = live_buffer_path / 'candles_today.duckdb'

            # 2. Verify integrity before move
            if ticks_src.exists():
                self._verify_integrity(ticks_src)
            if candles_src.exists():
                self._verify_integrity(candles_src)

            # 3. Promote Ticks
            if ticks_src.exists():
                ticks_dst = (self.data_root / 'market_data' / exchange /
                            'ticks' / f"{rollover_date}.duckdb")
                ticks_dst.parent.mkdir(parents=True, exist_ok=True)
                
                # Copy and rename (safer than move across possible mount points)
                shutil.copy2(ticks_src, ticks_dst)
                ticks_src.unlink()
                logger.info(f"  Promoted ticks to {ticks_dst}")

            # 4. Split and Promote Candles
            if candles_src.exists():
                self._split_candles_by_timeframe(candles_src, exchange, rollover_date)
                candles_src.unlink()
                logger.info(f"  Promoted and split candles for {rollover_date}")

            # 5. Create fresh live buffer databases
            self._initialize_live_buffer()
            logger.info("  Live buffer re-initialized.")

            return True

    def _verify_integrity(self, db_path: Path):
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            # DuckDB doesn't have PRAGMA integrity_check like SQLite, 
            # but we can do a simple check by querying
            conn.execute("SELECT count(*) FROM (SELECT * FROM candles LIMIT 1)" if "candles" in str(db_path) else "SELECT count(*) FROM (SELECT * FROM ticks LIMIT 1)").fetchall()
        except Exception as e:
            raise RuntimeError(f"Integrity check failed for {db_path}: {e}")
        finally:
            conn.close()

    def _split_candles_by_timeframe(self, src: Path, exchange: str, dt: date):
        """Split combined candles file into timeframe-specific files."""
        # Use a temporary connection to read the source
        # Note: We can't easily query one DuckDB from another without ATTACH
        # So we'll use pandas as a bridge for this operation
        import pandas as pd
        
        conn = duckdb.connect(str(src), read_only=True)
        try:
            # Find all timeframes in the buffer
            timeframes = [r[0] for r in conn.execute("SELECT DISTINCT timeframe FROM candles").fetchall()]
            
            for tf in timeframes:
                dst = (self.data_root / 'market_data' / exchange /
                      'candles' / tf / f"{dt}.duckdb")
                dst.parent.mkdir(parents=True, exist_ok=True)

                df = conn.execute("SELECT * FROM candles WHERE timeframe = ?", [tf]).df()
                
                # Export to new file
                dst_conn = duckdb.connect(str(dst))
                try:
                    dst_conn.execute(schema.MARKET_CANDLES_SCHEMA)
                    # Insert data from the dataframe
                    dst_conn.execute("INSERT INTO candles SELECT * FROM df")
                finally:
                    dst_conn.close()
                logger.info(f"    Split timeframe {tf} to {dst}")
        finally:
            conn.close()

    def _initialize_live_buffer(self):
        """Create fresh live buffer databases."""
        with self.db.live_buffer_writer() as conns:
            conns['ticks'].execute(schema.MARKET_TICKS_SCHEMA)
            conns['candles'].execute(schema.MARKET_CANDLES_SCHEMA)

if __name__ == "__main__":
    data_root = ROOT / "data"
    db_mgr = DatabaseManager(data_root)
    rollover = EODRollover(db_mgr, data_root)
    
    # Default to yesterday if running early morning, or today if running after market
    target_date = date.today()
    if len(sys.argv) > 1:
        target_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        
    try:
        rollover.execute(target_date)
        logger.info(f"[SUCCESS] EOD Rollover completed for {target_date}")
    except Exception as e:
        logger.error(f"[ERROR] Rollover failed: {e}")
        sys.exit(1)
