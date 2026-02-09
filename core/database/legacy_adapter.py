"""
Legacy Database Adapter
-----------------------
Provides backward-compatible db_cursor() for incremental migration.
Redirects to the new isolated architecture.
"""

import os
from contextlib import contextmanager
from typing import Optional, Generator, List
from pathlib import Path

import duckdb
import sqlite3

from core.database.manager import DatabaseManager

# Module-level manager instance (lazy initialized)
_manager: Optional[DatabaseManager] = None

def _get_manager() -> DatabaseManager:
    """Get or create the singleton DatabaseManager."""
    global _manager
    if _manager is None:
        _manager = DatabaseManager(Path("data"))
    return _manager

@contextmanager
def db_cursor(
    db_path: Optional[str] = None,
    read_only: bool = False,
) -> Generator:
    """
    LEGACY: Backward-compatible db_cursor context manager.
    Routes to the new CONFIG database by default.
    """
    manager = _get_manager()

    if read_only:
        with manager.config_reader() as conn:
            yield conn
    else:
        with manager.config_writer() as conn:
            yield conn

def get_connection(db_path: Optional[str] = None):
    """
    LEGACY: Returns a raw connection to config DB.
    """
    db_path = db_path or "data/config/config.db"
    return sqlite3.connect(db_path)

def get_db():
    return get_connection()

# Legacy persistence functions
def save_insight(insight, db_path: Optional[str] = None) -> None:
    from core.database.writers import AnalyticsWriter
    AnalyticsWriter(_get_manager()).save_insight(insight)

def save_insights(insights: List, db_path: Optional[str] = None) -> int:
    from core.database.writers import AnalyticsWriter
    return AnalyticsWriter(_get_manager()).save_insights_batch(insights)

def save_regime_snapshot(snapshot, db_path: Optional[str] = None) -> None:
    from core.database.writers import AnalyticsWriter
    AnalyticsWriter(_get_manager()).save_regime_snapshot(snapshot)

def save_trade(trade, db_path: Optional[str] = None) -> None:
    from core.database.writers import TradingWriter
    TradingWriter(_get_manager()).save_trade(trade)

def save_signal(signal, db_path: Optional[str] = None) -> None:
    from core.database.writers import TradingWriter
    TradingWriter(_get_manager()).save_signal(signal)

def get_latest_insights(symbol: Optional[str] = None, limit: int = 50) -> List[dict]:
    """Fetch recent confluence insights, optionally filtered by symbol."""
    try:
        with _get_manager().signals_reader() as conn:
            sql = "SELECT * FROM confluence_insights"
            params = []
            if symbol:
                sql += " WHERE symbol = ?"
                params.append(symbol)
            sql += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            cols = [d[0] for d in conn.description]
            return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []
