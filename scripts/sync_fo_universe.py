#!/usr/bin/env python3
"""
Sync F&O Universe
-----------------
Identifies stocks in the F&O segment and maps them to their NSE_EQ keys 
for consistent historical data fetching and analysis.
"""
import sys
import os
import sqlite3
import logging
from datetime import date
from pathlib import Path

# Add root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SyncFO")

def main():
    db_manager = DatabaseManager(ROOT / "data")
    
    # We use a raw connection here for complex cross-join logic if needed, 
    # but let's try to do it via the manager's readers/writers.
    
    logger.info("Starting F&O Universe Synchronization...")
    
    try:
        with db_manager.config_writer() as conn:
            # 1. Clear existing automatic entries (optional, or just use UPSERT)
            # We'll use UPSERT logic to keep manual entries safe.
            
            # 2. Identify F&O stocks and their EQ keys
            # Logic: 
            # - Find instruments in NSE_FO that are Futures
            # - Extract the base symbol (e.g., 'RELIANCE' from 'RELIANCE FUT 26 FEB 26')
            # - Match with NSE_EQ entry
            
            query = """
            WITH fo_base AS (
                -- Get unique base symbols from active futures
                SELECT 
                    DISTINCT CASE 
                        WHEN trading_symbol LIKE '% FUT %' THEN SUBSTR(trading_symbol, 1, INSTR(trading_symbol, ' FUT ') - 1)
                        ELSE trading_symbol 
                    END as base_symbol,
                    lot_size
                FROM instrument_meta 
                WHERE market_type = 'NSE_FO' 
                  AND (trading_symbol LIKE '% FUT %' OR instrument_key LIKE 'MCX_FO%')
                  AND is_active = 1
            )
            SELECT 
                eq.trading_symbol,
                eq.instrument_key,
                eq.trading_symbol as name,
                f.lot_size
            FROM fo_base f
            JOIN instrument_meta eq ON eq.trading_symbol = f.base_symbol
            WHERE eq.market_type IN ('NSE_EQ', 'MCX_FO')
              AND eq.is_active = 1
              -- Ensure we don't pick up corrupted keys (should be segment|ISIN or segment|numeric_id)
              AND (
                  (eq.market_type = 'NSE_EQ' AND eq.instrument_key LIKE 'NSE_EQ|INE%') OR
                  (eq.market_type = 'MCX_FO' AND eq.instrument_key LIKE 'MCX_FO|%')
              )
              AND length(eq.instrument_key) > 10
            """
            
            fo_universe = conn.execute(query).fetchall()
            logger.info(f"Identified {len(fo_universe)} F&O instruments with verified base keys.")
            
            # Clear and rebuild fo_stocks to ensure no legacy corruption remains
            conn.execute("DELETE FROM fo_stocks")
            
            inserted = 0
            all_keys = []
            
            for symbol, key, name, lot in fo_universe:
                conn.execute("""
                    INSERT OR REPLACE INTO fo_stocks (trading_symbol, instrument_key, name, lot_size, is_active)
                    VALUES (?, ?, ?, ?, 1)
                """, [symbol, key, name, lot])
                inserted += 1
                if key not in all_keys:
                    all_keys.append(key)
                    
            logger.info(f"Sync complete. New fo_stocks universe size: {inserted}")

            # 3. Update market_universe.json to stay in sync
            universe_path = ROOT / "config" / "market_universe.json"
            import json
            universe_data = {
                "description": "Market universe for live data ingestion - Verified NSE F&O stocks",
                "last_updated": str(date.today()),
                "symbols": sorted(all_keys)
            }
            with open(universe_path, 'w') as f:
                json.dump(universe_data, f, indent=2)
            logger.info(f"Updated {universe_path}")

    except Exception as e:
        logger.error(f"Failed to sync F&O universe: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
