from contextlib import contextmanager
from pathlib import Path
from datetime import date
from typing import Dict, Any, Generator, Optional, Union, List
import duckdb
import sqlite3
import logging
import os
import threading
import time

from .locks import WriterLock

logger = logging.getLogger(__name__)

class DatabaseManager:
    """
    Central database connection manager.
    Enforces ownership rules, connection modes, and single-writer locks.
    """

    def __init__(self, data_root: Union[str, Path], read_only: bool = False):
        self.data_root = Path(data_root).resolve()
        self.read_only = read_only # Only enforced for DuckDB market data
        self._infra_locks: Dict[str, threading.Lock] = {}
        self._master_lock = threading.Lock()
        # In unified mode (same process), we should be more aggressive about RW connections
        # to avoid DuckDB configuration mismatch errors.
        self.unified_mode = os.environ.get('UNIFIED_MODE') == '1'
        
        if self.read_only:
            logger.info("DatabaseManager initialized in READ-ONLY mode for DuckDB.")

    def _check_duckdb_write_permission(self):
        if self.read_only:
            raise PermissionError("Write operation attempted on DuckDB market data from a read-only DatabaseManager.")

    def _get_thread_lock(self, name: str) -> threading.Lock:
        with self._master_lock:
            if name not in self._infra_locks:
                self._infra_locks[name] = threading.Lock()
            return self._infra_locks[name]

    def _duckdb_connect(self, path: Union[str, Path], read_only: bool = False) -> duckdb.DuckDBPyConnection:
        """
        Connect to DuckDB with retry logic for configuration mismatches in the same process.
        """
        path_str = str(path)
        last_exception = None

        # REMOVED: Unified mode hack that forced readers to open as RW
        # This was breaking reader/writer isolation and causing lock conflicts

        for i in range(20): 
            try:
                return duckdb.connect(path_str, read_only=read_only)
            except duckdb.ConnectionException as e:
                last_exception = e
                err_msg = str(e)
                if "different configuration" in err_msg:
                    # REMOVED: Forced RW mode workaround
                    # Just retry with exponential backoff
                    time.sleep(0.05 * (i + 1))  # Exponential backoff
                    continue
                
                if "could not open" in err_msg.lower() and not read_only:
                    time.sleep(0.05)
                    continue
                    
                raise e
            except Exception as e:
                raise e
        
        if last_exception:
            raise last_exception
        return duckdb.connect(path_str, read_only=read_only)

    # ─────────────────────────────────────────────────────────────
    # HISTORICAL MARKET DATA (Read-Only except for ingestor)
    # ─────────────────────────────────────────────────────────────

    @contextmanager
    def historical_reader(self, exchange: str, data_type: str,
                          timeframe: Optional[str] = None, dt: Optional[date] = None) -> Generator[duckdb.DuckDBPyConnection, None, None]:
        if dt is None:
            raise ValueError("Date (dt) is required for historical data.")

        if data_type == 'ticks':
            path = self.data_root / 'market_data' / exchange / 'ticks' / f"{dt}.duckdb"
        else:
            if not timeframe:
                raise ValueError("Timeframe is required for candles.")
            path = self.data_root / 'market_data' / exchange / 'candles' / timeframe / f"{dt}.duckdb"

        if not path.exists():
            raise FileNotFoundError(f"Historical data not found: {path}")

        conn = self._duckdb_connect(path, read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def historical_writer(self, exchange: str, data_type: str,
                          timeframe: Optional[str] = None, dt: Optional[date] = None) -> Generator[duckdb.DuckDBPyConnection, None, None]:
        self._check_duckdb_write_permission()
        if dt is None:
            raise ValueError("Date (dt) is required for historical data.")

        lock_path = self.data_root / 'market_data' / '.writer.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with self._get_thread_lock('market_data'):
            with WriterLock(str(lock_path)):
                if data_type == 'ticks':
                    path = self.data_root / 'market_data' / exchange / 'ticks' / f"{dt}.duckdb"
                else:
                    if not timeframe:
                        raise ValueError("Timeframe is required for candles.")
                    path = self.data_root / 'market_data' / exchange / 'candles' / timeframe / f"{dt}.duckdb"

                path.parent.mkdir(parents=True, exist_ok=True)
                conn = self._duckdb_connect(path, read_only=False)
                try:
                    yield conn
                finally:
                    conn.close()

    # ─────────────────────────────────────────────────────────────
    # LIVE BUFFER (Today's data)
    # ─────────────────────────────────────────────────────────────

    @contextmanager
    def live_buffer_writer(self) -> Generator[Dict[str, duckdb.DuckDBPyConnection], None, None]:
        self._check_duckdb_write_permission()
        lock_path = self.data_root / 'live_buffer' / '.writer.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with self._get_thread_lock('live_buffer'):
            with WriterLock(str(lock_path), timeout=10.0):
                ticks_path = self.data_root / 'live_buffer' / 'ticks_today.duckdb'
                candles_path = self.data_root / 'live_buffer' / 'candles_today.duckdb'

                ticks_conn = self._duckdb_connect(ticks_path, read_only=False)
                candles_conn = self._duckdb_connect(candles_path, read_only=False)
                try:
                    yield {'ticks': ticks_conn, 'candles': candles_conn}
                finally:
                    ticks_conn.close()
                    candles_conn.close()

    @contextmanager
    def live_buffer_reader(self) -> Generator[Dict[str, duckdb.DuckDBPyConnection], None, None]:
        """Read from live buffer with thread synchronization to coordinate with writers."""
        ticks_path = self.data_root / 'live_buffer' / 'ticks_today.duckdb'
        candles_path = self.data_root / 'live_buffer' / 'candles_today.duckdb'

        # CRITICAL: Use thread lock to coordinate with live_buffer_writer()
        # This prevents unlimited concurrent readers from blocking writers
        with self._get_thread_lock('live_buffer'):
            conns = {}
            try:
                if ticks_path.exists():
                    conns['ticks'] = self._duckdb_connect(ticks_path, read_only=True)
                if candles_path.exists():
                    conns['candles'] = self._duckdb_connect(candles_path, read_only=True)
                yield conns
            finally:
                for conn in conns.values():
                    conn.close()

    # ─────────────────────────────────────────────────────────────
    # TRADING DATABASE (SQLite)
    # ─────────────────────────────────────────────────────────────

    @contextmanager
    def trading_writer(self) -> Generator[sqlite3.Connection, None, None]:
        lock_path = self.data_root / 'trading' / '.writer.lock'
        db_path = self.data_root / 'trading' / 'trading.db'
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with self._get_thread_lock('trading'):
            with WriterLock(str(lock_path)):
                conn = sqlite3.connect(str(db_path))
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

    @contextmanager
    def trading_reader(self) -> Generator[sqlite3.Connection, None, None]:
        db_path = self.data_root / 'trading' / 'trading.db'
        if not db_path.exists():
            raise FileNotFoundError(f"Trading database not found: {db_path}")

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            yield conn
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────
    # SIGNALS DATABASE (SQLite)
    # ─────────────────────────────────────────────────────────────

    @contextmanager
    def signals_writer(self) -> Generator[sqlite3.Connection, None, None]:
        lock_path = self.data_root / 'signals' / '.writer.lock'
        db_path = self.data_root / 'signals' / 'signals.db'
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with self._get_thread_lock('signals'):
            with WriterLock(str(lock_path), timeout=10.0):
                conn = sqlite3.connect(str(db_path))
                conn.execute("PRAGMA journal_mode=WAL")
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

    @contextmanager
    def signals_reader(self) -> Generator[sqlite3.Connection, None, None]:
        db_path = self.data_root / 'signals' / 'signals.db'
        if not db_path.exists():
            raise FileNotFoundError(f"Signals database not found: {db_path}")

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            yield conn
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────
    # BACKTEST INDEX (SQLite)
    # ─────────────────────────────────────────────────────────────

    @contextmanager
    def backtest_index_writer(self) -> Generator[sqlite3.Connection, None, None]:
        db_path = self.data_root / "backtest" / "summaries" / "backtest_index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        with self._get_thread_lock('backtest_index'):
            conn = sqlite3.connect(str(db_path))
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @contextmanager
    def backtest_index_reader(self) -> Generator[sqlite3.Connection, None, None]:
        db_path = self.data_root / "backtest" / "summaries" / "backtest_index.db"
        if not db_path.exists():
            raise FileNotFoundError(f"Backtest index not found: {db_path}")

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            yield conn
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────
    # BACKTEST RUNS (DuckDB)
    # ─────────────────────────────────────────────────────────────

    @contextmanager
    def backtest_writer(self, run_id: str) -> Generator[duckdb.DuckDBPyConnection, None, None]:
        self._check_duckdb_write_permission()
        runs_path = self.data_root / 'backtest' / 'runs'
        runs_path.mkdir(parents=True, exist_ok=True)

        db_path = runs_path / f"{run_id}.duckdb"
        conn = self._duckdb_connect(db_path, read_only=False)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def backtest_reader(self, run_id: str) -> Generator[duckdb.DuckDBPyConnection, None, None]:
        db_path = self.data_root / 'backtest' / 'runs' / f"{run_id}.duckdb"
        if not db_path.exists():
            raise FileNotFoundError(f"Backtest run not found: {db_path}")

        conn = self._duckdb_connect(db_path, read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────
    # CONFIG DATABASE (SQLite)
    # ─────────────────────────────────────────────────────────────

    @contextmanager
    def config_writer(self) -> Generator[sqlite3.Connection, None, None]:
        lock_path = self.data_root / 'config' / '.writer.lock'
        db_path = self.data_root / 'config' / 'config.db'
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with self._get_thread_lock('config'):
            with WriterLock(str(lock_path), timeout=10.0):
                conn = sqlite3.connect(str(db_path))
                conn.execute("PRAGMA journal_mode=WAL")
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

    @contextmanager
    def config_reader(self) -> Generator[sqlite3.Connection, None, None]:
        db_path = self.data_root / 'config' / 'config.db'
        if not db_path.exists():
            raise FileNotFoundError(f"Config database not found: {db_path}")

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            yield conn
        finally:
            conn.close()
