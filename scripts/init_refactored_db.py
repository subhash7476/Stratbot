#!/usr/bin/env python3
import sys
import os
from pathlib import Path
from datetime import date

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager
from core.database import schema

DATA_ROOT = ROOT / "data"

def init_all():
    print(f"Initializing refactored database architecture at {DATA_ROOT}...")
    
    db = DatabaseManager(DATA_ROOT)
    
    # 1. Trading DB
    print("  Initializing Trading DB...")
    with db.trading_writer() as conn:
        conn.execute(schema.TRADING_ORDERS_SCHEMA)
        conn.execute(schema.TRADING_TRADES_SCHEMA)
        conn.execute(schema.TRADING_POSITIONS_SCHEMA)
        
    # 2. Signals DB
    print("  Initializing Signals DB...")
    with db.signals_writer() as conn:
        conn.execute(schema.SIGNALS_INSIGHTS_SCHEMA)
        conn.execute(schema.SIGNALS_REGIME_SCHEMA)
        conn.execute(schema.SIGNALS_STRATEGY_SIGNALS_SCHEMA)
        
    # 3. Config DB
    print("  Initializing Config DB...")
    with db.config_writer() as conn:
        conn.execute(schema.CONFIG_USERS_SCHEMA)
        conn.execute(schema.CONFIG_ROLES_SCHEMA)
        conn.execute(schema.CONFIG_WATCHLIST_SCHEMA)
        conn.execute(schema.CONFIG_INSTRUMENT_META_SCHEMA)
        conn.execute(schema.CONFIG_RUNNER_STATE_SCHEMA)
        conn.execute(schema.CONFIG_WEBSOCKET_STATUS_SCHEMA)
        conn.execute(schema.CONFIG_FO_STOCKS_SCHEMA)

        # Seed roles
        conn.execute("INSERT OR IGNORE INTO roles (role_name, permissions) VALUES ('admin', 'read,write,execute')")
        conn.execute("INSERT OR IGNORE INTO roles (role_name, permissions) VALUES ('viewer', 'read')")
        
    # 4. Live Buffer DBs
    print("  Initializing Live Buffer DBs...")
    with db.live_buffer_writer() as conns:
        conns['ticks'].execute(schema.MARKET_TICKS_SCHEMA)
        conns['candles'].execute(schema.MARKET_CANDLES_SCHEMA)
        
    # 5. Backtest Index
    print("  Initializing Backtest Index...")
    index_path = DATA_ROOT / "backtest" / "summaries" / "backtest_index.db"
    conn = sqlite3_connect(str(index_path))
    try:
        conn.execute(schema.BACKTEST_INDEX_SCHEMA)
    finally:
        conn.close()

    print("\n[SUCCESS] Database initialization complete.")

def sqlite3_connect(path):
    import sqlite3
    conn = sqlite3.connect(path)
    return conn

if __name__ == "__main__":
    init_all()
