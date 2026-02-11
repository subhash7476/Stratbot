import sqlite3
import logging
from pathlib import Path


class ExecutionStore:
    """
    Manages SQLite connection and schema for execution persistence.
    """

    def __init__(self, db_path: str = "data/execution.db"):
        self.db_path = db_path
        self.logger = logging.getLogger(__name__)
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        try:
            with self.get_connection() as conn:
                # Orders table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS orders (
                        correlation_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity REAL NOT NULL,
                        order_type TEXT NOT NULL,
                        strategy_id TEXT,
                        signal_id TEXT,
                        timestamp TEXT NOT NULL,
                        metadata TEXT
                    )
                """)

                # Fills table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS fills (
                        fill_id TEXT PRIMARY KEY,
                        order_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        quantity REAL NOT NULL,
                        price REAL NOT NULL,
                        side TEXT NOT NULL,
                        fee REAL DEFAULT 0.0,
                        timestamp TEXT NOT NULL,
                        FOREIGN KEY(order_id) REFERENCES orders(correlation_id)
                    )
                """)

                # Positions snapshot table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS positions (
                        symbol TEXT PRIMARY KEY,
                        side TEXT NOT NULL,
                        quantity REAL NOT NULL,
                        avg_price REAL NOT NULL,
                        timestamp TEXT NOT NULL
                    )
                """)
        except Exception as e:
            self.logger.error(f"Failed to initialize execution store: {e}")
            raise
