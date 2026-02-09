#!/usr/bin/env python3
"""
Populate fo_stocks table in config.db from legacy trading_bot.duckdb
"""
import sys
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import duckdb
from core.database.manager import DatabaseManager
from core.database import schema

DATA_ROOT = ROOT / "data"
LEGACY_DB = DATA_ROOT / "trading_bot.duckdb"


def populate_fo_stocks():
    """Migrate fo_stocks_master from legacy DuckDB to new config.db"""
    print(f"Populating fo_stocks from {LEGACY_DB}...")

    if not LEGACY_DB.exists():
        print(f"Error: Legacy database not found at {LEGACY_DB}")
        return False

    # Read from legacy DuckDB
    legacy_conn = duckdb.connect(str(LEGACY_DB), read_only=True)
    try:
        result = legacy_conn.execute("""
            SELECT trading_symbol, instrument_key, name, lot_size, is_active
            FROM fo_stocks_master
        """).fetchall()
        print(f"  Found {len(result)} FO stocks in legacy database")
    except Exception as e:
        print(f"Error reading from legacy DB: {e}")
        legacy_conn.close()
        return False
    finally:
        legacy_conn.close()

    if not result:
        print("  No FO stocks found in legacy database")
        return False

    # Write to new config.db
    db = DatabaseManager(DATA_ROOT)
    with db.config_writer() as conn:
        # Create table if not exists
        conn.execute(schema.CONFIG_FO_STOCKS_SCHEMA)

        # Clear existing data
        conn.execute("DELETE FROM fo_stocks")

        # Insert new data
        conn.executemany("""
            INSERT INTO fo_stocks (trading_symbol, instrument_key, name, lot_size, is_active)
            VALUES (?, ?, ?, ?, ?)
        """, result)

        conn.commit()
        print(f"  Successfully populated {len(result)} FO stocks into config.db")

    return True


def verify_fo_stocks():
    """Verify fo_stocks table was populated correctly"""
    db = DatabaseManager(DATA_ROOT)
    with db.config_reader() as conn:
        count = conn.execute("SELECT COUNT(*) FROM fo_stocks").fetchone()[0]
        print(f"\nVerification: fo_stocks table has {count} records")

        sample = conn.execute("""
            SELECT trading_symbol, instrument_key, name
            FROM fo_stocks
            LIMIT 5
        """).fetchall()
        print("Sample records:")
        for row in sample:
            print(f"  {row[0]}: {row[2]} ({row[1]})")


if __name__ == "__main__":
    if populate_fo_stocks():
        verify_fo_stocks()
    else:
        print("Population failed!")
        sys.exit(1)
