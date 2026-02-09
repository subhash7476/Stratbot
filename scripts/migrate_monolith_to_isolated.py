#!/usr/bin/env python3
import sys
import os
import sqlite3
import duckdb
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date, datetime

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager
from core.database import schema

DATA_ROOT = ROOT / "data"
MONOLITH_PATH = DATA_ROOT / "trading_bot.duckdb"

def migrate():
    print(f"Starting migration from {MONOLITH_PATH}...")
    
    if not MONOLITH_PATH.exists():
        print(f"Error: Monolith database not found at {MONOLITH_PATH}")
        return

    db_manager = DatabaseManager(DATA_ROOT)
    monolith = duckdb.connect(str(MONOLITH_PATH), read_only=True)

    def manual_migrate_table(duck_conn, table_name, sqlite_conn):
        try:
            # 1. Get data from DuckDB
            res = duck_conn.execute(f"SELECT * FROM {table_name}")
            cols = [desc[0] for desc in res.description]
            rows = res.fetchall()
            
            if not rows:
                print(f"  {table_name} is empty, skipping.")
                return

            # 2. Get target columns in SQLite
            sqlite_cols_info = sqlite_conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            sqlite_cols = [c[1] for c in sqlite_cols_info]
            
            # 3. Filter and map columns
            common_cols = [c for c in cols if c in sqlite_cols]
            col_indices = [cols.index(c) for c in common_cols]
            
            if not common_cols:
                print(f"  No common columns found for {table_name}, skipping.")
                return

            # 4. Prepare insertion
            placeholders = ", ".join(["?" for _ in common_cols])
            target_cols_str = ", ".join(common_cols)
            insert_sql = f"INSERT INTO {table_name} ({target_cols_str}) VALUES ({placeholders})"
            
            # 5. Clean data (convert UUIDs/Objects to string)
            cleaned_rows = []
            for row in rows:
                cleaned_row = []
                for idx in col_indices:
                    val = row[idx]
                    # Convert UUID or other complex types to string
                    if val is not None and not isinstance(val, (int, float, str, bytes, bool)):
                        val = str(val)
                    cleaned_row.append(val)
                cleaned_rows.append(tuple(cleaned_row))
            
            # 6. Execute in batches
            sqlite_conn.execute(f"DELETE FROM {table_name}")
            sqlite_conn.executemany(insert_sql, cleaned_rows)
            sqlite_conn.commit()
            print(f"  Successfully migrated {len(cleaned_rows)} rows to {table_name}")
            
        except Exception as e:
            print(f"  Failed to migrate {table_name}: {e}")

    # 1. Migrate Config (SQLite)
    print("Migrating Config data...")
    config_tables = ['users', 'roles', 'user_watchlist', 'instrument_meta']
    with db_manager.config_writer() as config_conn:
        for table in config_tables:
            manual_migrate_table(monolith, table, config_conn)

    # 2. Migrate Signals (SQLite)
    print("Migrating Signals data...")
    signals_tables = ['confluence_insights', 'regime_insights', 'signals']
    with db_manager.signals_writer() as signals_conn:
        for table in signals_tables:
            manual_migrate_table(monolith, table, signals_conn)

    # 3. Migrate Market Data (DuckDB split by day)
    print("Migrating Market Data (OHLCV)...")
    try:
        dates = monolith.execute("SELECT DISTINCT CAST(timestamp AS DATE) as d FROM candles ORDER BY d").fetchall()
        for (dt_val,) in dates:
            if dt_val is None: continue
            exchange = 'nse' 
            df = monolith.execute("SELECT * FROM candles WHERE CAST(timestamp AS DATE) = ?", [dt_val]).df()
            df['symbol'] = df['instrument_key']
            df['timeframe'] = '1m'
            
            with db_manager.historical_writer(exchange, 'candles', '1m', dt_val) as hist_conn:
                hist_conn.execute(schema.MARKET_CANDLES_SCHEMA)
                # We use DuckDB SQL for this one as it's DuckDB to DuckDB
                hist_conn.execute("""
                    INSERT OR IGNORE INTO candles (symbol, timeframe, timestamp, open, high, low, close, volume, is_synthetic)
                    SELECT symbol, timeframe, timestamp, open, high, low, close, CAST(volume AS BIGINT), is_synthetic FROM df
                """)
                print(f"  Migrated {len(df)} OHLCV rows for {dt_val}")
    except Exception as e:
        print(f"  Error migrating OHLCV: {e}")

    # 4. Migrate Live Ticks (split by day)
    print("Migrating Ticks data...")
    try:
        dates = monolith.execute("SELECT DISTINCT CAST(exchange_ts AS DATE) as d FROM live_ticks ORDER BY d").fetchall()
        for (dt_val,) in dates:
            if dt_val is None: continue
            exchange = 'nse'
            df = monolith.execute("SELECT symbol, exchange_ts as timestamp, price, volume FROM live_ticks WHERE CAST(exchange_ts AS DATE) = ?", [dt_val]).df()
            
            with db_manager.historical_writer(exchange, 'ticks', dt=dt_val) as hist_conn:
                hist_conn.execute(schema.MARKET_TICKS_SCHEMA)
                hist_conn.execute("""
                    INSERT OR IGNORE INTO ticks (symbol, timestamp, price, volume, bid, ask)
                    SELECT symbol, timestamp, price, CAST(volume AS BIGINT), NULL as bid, NULL as ask FROM df
                """)
                print(f"  Migrated {len(df)} Ticks for {dt_val}")
    except Exception as e:
        print(f"  Error migrating Ticks: {e}")

    # 5. Backtest Index (SQLite)
    print("Migrating Backtest metadata...")
    try:
        index_path = DATA_ROOT / "backtest" / "summaries" / "backtest_index.db"
        index_conn = sqlite3.connect(str(index_path))
        manual_migrate_table(monolith, 'backtest_runs', index_conn)
        index_conn.close()
    except Exception as e:
        print(f"  Skip backtest_runs: {e}")

    monolith.close()
    print("\n[SUCCESS] Migration complete.")

if __name__ == "__main__":
    migrate()
