"""
DuckDB Database Manager for Trading Bot
Handles all database operations with ACID transactions

Version: 3.0 - Robust Connection Manager with Auto-Recovery
- Single connection per process with automatic health checks
- Thread-safe execute with retry logic and auto-reconnection
- Graceful handling of connection failures across Streamlit pages
- All existing TradingDB functionality preserved

ARCHITECTURE:
- Uses a single shared DuckDB connection per Streamlit session
- All database operations go through safe_execute() with retry logic
- Automatic connection recovery on failure
- Thread-safe for WebSocket callbacks
"""

import threading
import duckdb
import pandas as pd
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, date
import json
import uuid
import time
import logging

# Setup logging
logger = logging.getLogger(__name__)

# ============================================================================
# ROBUST CONNECTION MANAGEMENT
# ============================================================================

# Default database path
_DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "trading_bot.duckdb"

# Global connection storage (singleton pattern)
_global_connection: Optional[duckdb.DuckDBPyConnection] = None
_global_db_path: Optional[Path] = None

# Thread lock for safe concurrent access
_db_lock = threading.RLock()  # Use RLock to allow re-entrant locking

# Connection health check interval - INCREASED to reduce reconnection spam
_last_health_check: float = 0
_HEALTH_CHECK_INTERVAL = 60.0  # seconds - only check every 60 seconds
_connection_valid = False  # Track if we've verified the connection at least once


def _get_streamlit_session_state():
    """
    Safely get Streamlit session_state if available.
    Returns None if not in Streamlit context.
    """
    try:
        import streamlit as st
        # Check if we're in a Streamlit context
        if hasattr(st, 'session_state'):
            return st.session_state
    except ImportError:
        pass
    except Exception:
        pass
    return None


def _is_connection_healthy(con: duckdb.DuckDBPyConnection) -> bool:
    """
    Check if a DuckDB connection is healthy and usable.

    Args:
        con: DuckDB connection to check

    Returns:
        True if connection is healthy, False otherwise
    """
    if con is None:
        return False

    try:
        result = con.execute("SELECT 1").fetchone()
        return result is not None and result[0] == 1
    except Exception:
        return False


def _create_connection(db_path: Path, read_only: bool = False) -> Optional[duckdb.DuckDBPyConnection]:
    """
    Create a new DuckDB connection with optimized settings.

    Args:
        db_path: Path to database file
        read_only: If True, open in read-only mode (allows concurrent access)

    Returns:
        New DuckDB connection or None on failure
    """
    try:
        # Ensure directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)

        config = {
            'threads': 4,
            'max_memory': '4GB',
            'default_order': 'ASC'
        }

        if read_only:
            config['access_mode'] = 'READ_ONLY'

        con = duckdb.connect(
            str(db_path),
            read_only=read_only,
            config=config
        )
        mode_str = " (READ_ONLY)" if read_only else ""
        logger.info(f"✅ Created new DuckDB connection{mode_str}: {db_path}")
        return con
    except Exception as e:
        logger.error(f"❌ Failed to create DuckDB connection: {e}")
        return None


def force_reset_database():
    """
    Force reset the database connection after a fatal error.
    This completely destroys and recreates the connection.
    """
    global _global_connection, _global_db_path, _connection_valid

    with _db_lock:
        # Force close existing connection
        if _global_connection is not None:
            try:
                _global_connection.close()
            except:
                pass  # Ignore errors during close

        _global_connection = None
        _global_db_path = None
        _connection_valid = False

        logger.info("🔄 Database connection force reset - will reconnect on next access")


def get_shared_connection(db_path: Path = None, read_only: bool = False) -> Optional[duckdb.DuckDBPyConnection]:
    """
    Get or create a shared DuckDB connection.

    This function ensures only ONE connection exists per process,
    preventing connection conflicts across Streamlit pages.

    IMPORTANT: Health checks are now MINIMAL to prevent reconnection loops.
    We only reconnect if the connection is truly None or on explicit errors.

    Args:
        db_path: Path to database file (defaults to data/trading_bot.duckdb)
        read_only: If True, open in read-only mode (allows concurrent access from multiple processes)

    Returns:
        DuckDB connection (creates new one if needed)
    """
    global _global_connection, _global_db_path, _last_health_check, _connection_valid

    if db_path is None:
        db_path = _DEFAULT_DB_PATH

    db_path = Path(db_path)

    with _db_lock:
        # If we have a connection and it's been validated, just return it
        # Don't do health checks on every access - that causes the loop!
        if _global_connection is not None and _connection_valid:
            if _global_db_path == db_path:
                return _global_connection

        # If path changed, we need a new connection
        if _global_connection is not None and _global_db_path != db_path:
            try:
                _global_connection.close()
            except:
                pass
            _global_connection = None
            _connection_valid = False

        # Create new connection only if we don't have one
        if _global_connection is None:
            _global_connection = _create_connection(db_path, read_only=read_only)
            _global_db_path = db_path
            _connection_valid = _global_connection is not None

        return _global_connection


def close_shared_connection():
    """
    Close the shared database connection.
    Call this when you need to release the database lock.
    """
    global _global_connection, _global_db_path, _connection_valid

    with _db_lock:
        if _global_connection is not None:
            try:
                _global_connection.close()
                logger.info("✅ Closed shared DuckDB connection")
            except Exception as e:
                logger.warning(f"⚠️ Error closing connection: {e}")
            finally:
                _global_connection = None
                _global_db_path = None
                _connection_valid = False


def reset_shared_connection():
    """
    Reset the database connection.
    Useful when switching between pages or after errors.
    """
    close_shared_connection()

    # Clear Streamlit cache if available
    try:
        import streamlit as st
        st.cache_data.clear()
    except:
        pass


def safe_execute(
    query: str,
    params: Any = None,
    fetch: str = None,
    max_retries: int = 3,
    retry_delay: float = 0.1
) -> Union[Any, pd.DataFrame, None]:
    """
    Execute a SQL query with automatic retry and reconnection.

    This is the RECOMMENDED way to execute queries across all pages.
    It handles connection failures, retries on lock contention, and
    automatically reconnects if needed.

    Args:
        query: SQL query string
        params: Query parameters (optional)
        fetch: 'one' for fetchone(), 'all' for fetchall(), 'df' for df(), None for no fetch
        max_retries: Number of retry attempts
        retry_delay: Base delay between retries (uses exponential backoff)

    Returns:
        Query result based on fetch parameter, or None on failure

    Example:
        # Simple query
        result = safe_execute("SELECT COUNT(*) FROM instruments", fetch='one')
        count = result[0] if result else 0

        # With parameters
        safe_execute("INSERT INTO signals VALUES (?, ?)", [signal_id, symbol])

        # Get DataFrame
        df = safe_execute("SELECT * FROM signals WHERE status = ?", ['ACTIVE'], fetch='df')
    """
    global _global_connection, _connection_valid

    for attempt in range(max_retries):
        with _db_lock:
            try:
                # Ensure we have a valid connection
                con = get_shared_connection()
                if con is None:
                    raise Exception("Could not establish database connection")

                # Execute query
                if params:
                    result = con.execute(query, params)
                else:
                    result = con.execute(query)

                # Fetch results based on mode
                if fetch == 'one':
                    return result.fetchone()
                elif fetch == 'all':
                    return result.fetchall()
                elif fetch == 'df':
                    return result.df()
                else:
                    return result

            except Exception as e:
                error_msg = str(e).lower()

                # FATAL ERROR: Database invalidated - must force reset
                if 'invalidated' in error_msg or 'fatal' in error_msg:
                    logger.error(f"🔴 FATAL DB ERROR - forcing reset: {e}")
                    force_reset_database()
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (2 ** attempt))
                        continue
                    else:
                        raise  # Re-raise on last attempt

                # Check if it's a retryable error - be more conservative
                retryable = any(x in error_msg for x in [
                    'lock', 'busy', 'timeout'
                ])

                if retryable and attempt < max_retries - 1:
                    # Just retry without resetting connection
                    logger.debug(f"DB busy (attempt {attempt + 1}/{max_retries}): {e}")
                    time.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
                    continue

                # Only reset connection on actual connection errors
                if any(x in error_msg for x in ['connection', 'closed', 'invalid', 'process cannot access']):
                    logger.warning(f"⚠️ Connection error, will retry: {e}")
                    force_reset_database()
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (2 ** attempt))
                        continue

                # Non-retryable error or all retries exhausted
                if 'duplicate' in error_msg or 'constraint' in error_msg:
                    # Silently handle duplicate key errors
                    logger.debug(f"Duplicate key ignored: {e}")
                    return None

                logger.error(f"❌ Database error: {e}")
                raise

    return None


def safe_commit():
    """
    Safely commit the current transaction.
    """
    with _db_lock:
        try:
            con = get_shared_connection()
            if con:
                con.commit()
        except Exception as e:
            logger.error(f"❌ Commit failed: {e}")


class TradingDB:
    """
    Main database interface for the trading bot.
    Provides methods for OHLCV storage, instrument management, and backtesting.

    Version 3.0: Uses robust shared connection with auto-recovery.

    Usage:
        db = get_db()  # Get singleton instance

        # Safe query execution (RECOMMENDED)
        result = db.safe_query("SELECT * FROM instruments", fetch='df')

        # Legacy access (still works, but use safe_query when possible)
        db.con.execute("SELECT 1")

    Thread-safe: All operations use RLock for concurrent access.
    """

    def __init__(self, db_path: Path = None):
        """
        Initialize database connection and create schema if needed.

        Args:
            db_path: Path to .duckdb file (default: data/trading_bot.duckdb)
        """
        global _global_connection, _global_db_path

        if db_path is None:
            db_path = _DEFAULT_DB_PATH

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Use shared connection - this handles health checks automatically
        self._con = None

        # Try to connect, with recovery on fatal errors
        try:
            self._ensure_connection()
            self._create_schema()
            logger.info(f"✅ TradingDB initialized: {self.db_path}")
        except Exception as e:
            error_msg = str(e).lower()
            if 'invalidated' in error_msg or 'fatal' in error_msg:
                logger.error(f"🔴 FATAL ERROR during init - forcing reset: {e}")
                force_reset_database()
                # Try again after reset
                self._ensure_connection()
                self._create_schema()
                logger.info(f"✅ TradingDB initialized after reset: {self.db_path}")
            else:
                raise

    def _ensure_connection(self):
        """Ensure we have a valid database connection."""
        global _global_connection, _global_db_path

        with _db_lock:
            # Get or create shared connection
            con = get_shared_connection(self.db_path)

            if con is None:
                # Create new connection
                con = _create_connection(self.db_path)
                if con is None:
                    raise Exception(f"Failed to connect to database: {self.db_path}")

                _global_connection = con
                _global_db_path = self.db_path

            self._con = con

    @property
    def con(self):
        """
        Get the database connection.

        SIMPLIFIED: No health checks on every access - that causes reconnection loops!
        We only check if the connection object is None.
        Errors during execution will trigger reconnection via safe_execute().
        """
        global _global_connection

        with _db_lock:
            # Only reconnect if we truly have no connection
            if self._con is None:
                self._con = get_shared_connection(self.db_path)

            # If global connection changed (shouldn't happen often), sync up
            if _global_connection is not None and self._con != _global_connection:
                self._con = _global_connection

            return self._con

    def safe_query(
        self,
        query: str,
        params: Any = None,
        fetch: str = None,
        max_retries: int = 3
    ) -> Union[Any, pd.DataFrame, None]:
        """
        Execute a query with automatic retry and reconnection.

        This is the SAFEST way to execute queries. It handles:
        - Connection failures
        - Lock contention
        - Automatic reconnection
        - Exponential backoff

        Args:
            query: SQL query string
            params: Query parameters (optional)
            fetch: 'one', 'all', 'df', or None
            max_retries: Number of retry attempts

        Returns:
            Query result based on fetch parameter

        Example:
            # Count query
            result = db.safe_query("SELECT COUNT(*) FROM signals", fetch='one')
            count = result[0] if result else 0

            # DataFrame query
            df = db.safe_query("SELECT * FROM signals WHERE status = ?", ['ACTIVE'], fetch='df')
        """
        return safe_execute(query, params, fetch, max_retries)

    def safe_commit(self):
        """Safely commit current transaction."""
        safe_commit()

    def execute_safe(self, query, params=None, max_retries=3, retry_delay=0.1):
        """
        Thread-safe execute for WebSocket callbacks and concurrent access.

        Use this method when writing to the database from background threads
        (like WebSocket handlers) to prevent database corruption.

        Args:
            query: SQL query string
            params: Optional query parameters
            max_retries: Number of retries on lock contention
            retry_delay: Seconds to wait between retries

        Returns:
            Query result
        """
        import time

        for attempt in range(max_retries):
            with _db_lock:
                try:
                    if params:
                        return self.con.execute(query, params)
                    return self.con.execute(query)
                except Exception as e:
                    error_msg = str(e).lower()

                    # Retry on lock/constraint errors
                    if any(x in error_msg for x in ['lock', 'constraint', 'conflict', 'duplicate']):
                        if attempt < max_retries - 1:
                            time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                            continue

                    # Don't retry on other errors
                    print(f"[DB ERROR] execute_safe failed: {e}")
                    if 'duplicate' in error_msg or 'constraint' in error_msg:
                        # Silently ignore duplicate key errors (INSERT OR IGNORE behavior)
                        return None
                    raise

        return None  # All retries exhausted

    def _create_schema(self):
        """Create all tables if they don't exist"""

        # Table 1: Instruments
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS instruments (
                instrument_key VARCHAR PRIMARY KEY,
                trading_symbol VARCHAR NOT NULL,
                name VARCHAR,
                instrument_type VARCHAR,
                exchange VARCHAR,
                segment VARCHAR NOT NULL,
                lot_size INTEGER,
                tick_size DOUBLE,
                expiry DATE,
                strike DOUBLE,
                option_type VARCHAR,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 2: OHLCV 1-minute (partitioned by date for fast queries)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv_1m (
                instrument_key VARCHAR NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                volume BIGINT DEFAULT 0,
                oi BIGINT,
                PRIMARY KEY (instrument_key, timestamp)
            )
        """)

        # Table 3: Resampled OHLCV (5m, 15m, 30m, 60m, 1D)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv_resampled (
                instrument_key VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                volume BIGINT DEFAULT 0,
                PRIMARY KEY (instrument_key, timeframe, timestamp)
            )
        """)

        # Table 4: Trades (for backtests and live)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                instrument_key VARCHAR NOT NULL,
                trade_type VARCHAR NOT NULL,
                direction VARCHAR NOT NULL,
                entry_time TIMESTAMP NOT NULL,
                entry_price DOUBLE NOT NULL,
                exit_time TIMESTAMP,
                exit_price DOUBLE,
                quantity INTEGER DEFAULT 1,
                exit_reason VARCHAR,
                pnl DOUBLE,
                pnl_pct DOUBLE,
                commission DOUBLE DEFAULT 0,
                notes VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 5: Backtest runs
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS backtest_runs (
                run_id VARCHAR PRIMARY KEY,
                strategy_name VARCHAR NOT NULL,
                instrument_key VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                parameters VARCHAR,
                initial_capital DOUBLE,
                final_capital DOUBLE,
                total_trades INTEGER,
                win_rate DOUBLE,
                profit_factor DOUBLE,
                sharpe_ratio DOUBLE,
                max_drawdown_pct DOUBLE,
                metrics VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 6: Market status tracking
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS market_status (
                instrument_key VARCHAR PRIMARY KEY,
                last_1m_timestamp TIMESTAMP,
                last_5m_timestamp TIMESTAMP,
                last_1d_timestamp TIMESTAMP,
                last_checked TIMESTAMP,
                status VARCHAR
            )
        """)

        # Table 7: F&O Stocks Master
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS fo_stocks_master (
                instrument_key VARCHAR PRIMARY KEY,
                trading_symbol VARCHAR NOT NULL,
                name VARCHAR,
                lot_size INTEGER,
                is_active BOOLEAN DEFAULT TRUE,
                added_date DATE DEFAULT CURRENT_DATE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 8: EHMA Universe (signal tracking)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS ehma_universe (
                id INTEGER PRIMARY KEY,
                signal_date DATE NOT NULL,
                symbol VARCHAR NOT NULL,
                instrument_key VARCHAR,
                signal_type VARCHAR NOT NULL,
                signal_strength DOUBLE,
                bars_ago INTEGER,
                current_price DOUBLE,
                entry_price DOUBLE,
                stop_loss DOUBLE,
                target_price DOUBLE,
                rsi DOUBLE,
                trend VARCHAR,
                reasons VARCHAR,
                status VARCHAR DEFAULT 'ACTIVE',
                scan_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (signal_date, symbol, signal_type)
            )
        """)

        # Create indexes for common queries
        try:
            self.con.execute(
                "CREATE INDEX IF NOT EXISTS idx_ohlcv_1m_ts ON ohlcv_1m (timestamp)")
            self.con.execute(
                "CREATE INDEX IF NOT EXISTS idx_ohlcv_resampled_ts ON ohlcv_resampled (timestamp)")
            self.con.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_run ON trades (run_id)")
        except:
            pass

    # ========================================================================
    # INSTRUMENT MANAGEMENT
    # ========================================================================

    def upsert_instrument(self, instrument_key: str, trading_symbol: str,
                          segment: str, **kwargs) -> bool:
        """
        Insert or update an instrument.

        Args:
            instrument_key: Unique instrument identifier (e.g., "NSE_EQ|INE002A01018")
            trading_symbol: Trading symbol (e.g., "RELIANCE")
            segment: Market segment (e.g., "NSE_EQ")
            **kwargs: Additional fields (name, lot_size, tick_size, etc.)

        Returns:
            True if successful
        """
        # Build dynamic column list
        columns = ['instrument_key', 'trading_symbol', 'segment']
        values = [instrument_key, trading_symbol, segment]

        for key, val in kwargs.items():
            if val is not None:
                columns.append(key)
                values.append(val)

        placeholders = ', '.join(['?' for _ in values])
        col_str = ', '.join(columns)

        # Use INSERT OR REPLACE for upsert
        self.con.execute(f"""
            INSERT OR REPLACE INTO instruments ({col_str})
            VALUES ({placeholders})
        """, values)

        return True

    def get_instruments(self, segment: str = None,
                        active_only: bool = True) -> pd.DataFrame:
        """
        Get instruments filtered by segment and active status.

        Args:
            segment: Filter by segment (optional)
            active_only: Only return active instruments

        Returns:
            DataFrame with instrument details
        """
        query = "SELECT * FROM instruments WHERE 1=1"
        params = []

        if segment:
            query += " AND segment = ?"
            params.append(segment)

        if active_only:
            query += " AND is_active = TRUE"

        query += " ORDER BY trading_symbol"

        return self.con.execute(query, params).df()

    # ========================================================================
    # OHLCV DATA MANAGEMENT
    # ========================================================================

    def upsert_ohlcv_1m(self, instrument_key: str, df: pd.DataFrame) -> int:
        """
        Insert 1-minute OHLCV data.

        Args:
            instrument_key: Instrument identifier
            df: DataFrame with columns [timestamp, Open, High, Low, Close, Volume]
               (or lowercase versions)

        Returns:
            Number of rows inserted
        """
        if df.empty:
            return 0

        df = df.copy()

        # Standardize column names
        col_map = {
            'Timestamp': 'timestamp', 'Time': 'timestamp', 'Date': 'timestamp',
            'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close',
            'Volume': 'volume', 'OI': 'oi', 'open_interest': 'oi'
        }
        df.rename(columns=col_map, inplace=True)

        # Ensure timestamp column exists
        if 'timestamp' not in df.columns and df.index.name in ['timestamp', 'Timestamp', None]:
            df = df.reset_index()
            if df.columns[0] != 'timestamp':
                df.rename(columns={df.columns[0]: 'timestamp'}, inplace=True)

        # Add instrument_key
        df['instrument_key'] = instrument_key

        # Select only needed columns
        cols = ['instrument_key', 'timestamp',
                'open', 'high', 'low', 'close', 'volume']
        if 'oi' in df.columns:
            cols.append('oi')
        df = df[[c for c in cols if c in df.columns]]

        # Insert with conflict handling
        initial_count = self.con.execute(
            "SELECT COUNT(*) FROM ohlcv_1m WHERE instrument_key = ?", [instrument_key]).fetchone()[0]

        self.con.execute("""
            INSERT OR IGNORE INTO ohlcv_1m 
            SELECT * FROM df
        """)

        final_count = self.con.execute(
            "SELECT COUNT(*) FROM ohlcv_1m WHERE instrument_key = ?", [instrument_key]).fetchone()[0]

        inserted = final_count - initial_count
        return inserted

    def get_ohlcv_1m(self, instrument_key: str,
                     start_date: datetime = None,
                     end_date: datetime = None) -> pd.DataFrame:
        """
        Get 1-minute OHLCV data for an instrument.

        Args:
            instrument_key: Instrument identifier
            start_date: Start datetime (optional)
            end_date: End datetime (optional)

        Returns:
            DataFrame with OHLCV data, indexed by timestamp
        """
        query = "SELECT * FROM ohlcv_1m WHERE instrument_key = ?"
        params = [instrument_key]

        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date)

        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date)

        query += " ORDER BY timestamp"

        df = self.con.execute(query, params).df()

        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)

        return df

    def insert_ohlcv_resampled(self, instrument_key: str, timeframe: str,
                               df: pd.DataFrame) -> int:
        """
        Insert resampled OHLCV data.

        Args:
            instrument_key: Instrument identifier
            timeframe: Timeframe string (e.g., '5minute', '15minute', '1day')
            df: DataFrame with OHLCV columns

        Returns:
            Number of rows inserted
        """
        if df.empty:
            return 0

        df = df.copy()

        # Standardize columns
        col_map = {
            'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close',
            'Volume': 'volume'
        }
        df.rename(columns=col_map, inplace=True)

        # Handle index
        if df.index.name in ['timestamp', 'Timestamp', None]:
            df = df.reset_index()
            if df.columns[0] != 'timestamp':
                df.rename(columns={df.columns[0]: 'timestamp'}, inplace=True)

        df['instrument_key'] = instrument_key
        df['timeframe'] = timeframe

        cols = ['instrument_key', 'timeframe', 'timestamp',
                'open', 'high', 'low', 'close', 'volume']
        df = df[[c for c in cols if c in df.columns]]

        # Insert with conflict handling
        self.con.execute("""
            INSERT OR IGNORE INTO ohlcv_resampled
            SELECT * FROM df
        """)

        return len(df)

    def get_ohlcv_resampled(self, instrument_key: str, timeframe: str,
                            start_date: datetime = None,
                            end_date: datetime = None) -> pd.DataFrame:
        """
        Get resampled OHLCV data.

        Args:
            instrument_key: Instrument identifier
            timeframe: Timeframe string
            start_date: Start datetime (optional)
            end_date: End datetime (optional)

        Returns:
            DataFrame with OHLCV data, indexed by timestamp
        """
        query = """
            SELECT timestamp, open, high, low, close, volume
            FROM ohlcv_resampled 
            WHERE instrument_key = ? AND timeframe = ?
        """
        params = [instrument_key, timeframe]

        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date)

        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date)

        query += " ORDER BY timestamp"

        df = self.con.execute(query, params).df()

        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            # Rename to Title Case for compatibility
            df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']

        return df

    # ========================================================================
    # RESAMPLING
    # ========================================================================

    def resample_and_store(self, instrument_key: str,
                           timeframes: List[str] = None) -> Dict[str, int]:
        """
        Resample 1m data to multiple timeframes and store.

        Args:
            instrument_key: Instrument to resample
            timeframes: List of target timeframes (default: ['5minute', '15minute', '60minute', '1day'])

        Returns:
            Dict with rows inserted per timeframe
        """
        if timeframes is None:
            timeframes = ['5minute', '15minute', '60minute', '1day']

        # Get all 1m data
        df_1m = self.get_ohlcv_1m(instrument_key)

        if df_1m.empty:
            return {tf: 0 for tf in timeframes}

        results = {}

        for tf in timeframes:
            rule = self._timeframe_to_rule(tf)
            if rule is None:
                continue

            # Resample
            df_resampled = df_1m.resample(rule).agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()

            if not df_resampled.empty:
                inserted = self.insert_ohlcv_resampled(
                    instrument_key, tf, df_resampled)
                results[tf] = inserted
            else:
                results[tf] = 0

        return results

    def _timeframe_to_rule(self, timeframe: str) -> str:
        """Convert timeframe string to pandas resample rule"""
        mapping = {
            '1minute': '1min',
            '5minute': '5min',
            '15minute': '15min',
            '30minute': '30min',
            '60minute': '60min',
            '1hour': '60min',
            '1day': '1D',
            'day': '1D'
        }
        return mapping.get(timeframe.lower())

    # ========================================================================
    # BACKTEST STORAGE
    # ========================================================================

    def save_backtest_results(self, strategy_name: str, instrument_key: str,
                              timeframe: str, start_date, end_date,
                              parameters: Dict, initial_capital: float,
                              trades_df: pd.DataFrame, metrics: Dict) -> str:
        """
        Save a complete backtest run with trades.

        Args:
            strategy_name: Name of strategy
            instrument_key: Instrument tested
            timeframe: Timeframe used
            start_date: Backtest start date
            end_date: Backtest end date
            parameters: Strategy parameters (dict)
            initial_capital: Starting capital
            trades_df: DataFrame with trade records
            metrics: Performance metrics (dict)

        Returns:
            run_id (UUID string)
        """
        run_id = str(uuid.uuid4())

        # Insert backtest metadata
        self.con.execute("""
            INSERT INTO backtest_runs (
                run_id, strategy_name, instrument_key, timeframe,
                start_date, end_date, parameters, initial_capital,
                final_capital, total_trades, win_rate, profit_factor,
                sharpe_ratio, max_drawdown_pct, metrics
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            run_id, strategy_name, instrument_key, timeframe,
            start_date, end_date, json.dumps(parameters), initial_capital,
            metrics.get('Final Capital'),
            metrics.get('Total Trades'),
            metrics.get('Win Rate %'),
            metrics.get('Profit Factor'),
            metrics.get('Sharpe Ratio'),
            metrics.get('Max Drawdown %'),
            json.dumps(metrics)
        ])

        # Insert trades
        if not trades_df.empty:
            trades_df = trades_df.copy()
            trades_df['trade_id'] = [str(uuid.uuid4())
                                     for _ in range(len(trades_df))]
            trades_df['run_id'] = run_id
            trades_df['instrument_key'] = instrument_key
            trades_df['trade_type'] = 'BACKTEST'

            # Rename columns to match schema
            col_map = {
                'Entry Time': 'entry_time',
                'Entry Price': 'entry_price',
                'Exit Time': 'exit_time',
                'Exit Price': 'exit_price',
                'Direction': 'direction',
                'Quantity': 'quantity',
                'PnL': 'pnl',
                'PnL %': 'pnl_pct',
                'Exit Reason': 'exit_reason',
                'Commission': 'commission'
            }
            trades_df.rename(columns=col_map, inplace=True)

            # Select only needed columns
            cols = ['trade_id', 'run_id', 'instrument_key', 'trade_type',
                    'direction', 'entry_time', 'entry_price', 'exit_time',
                    'exit_price', 'quantity', 'exit_reason', 'pnl', 'pnl_pct']
            trades_df = trades_df[[c for c in cols if c in trades_df.columns]]

            self.con.execute("""
                INSERT INTO trades 
                SELECT * FROM trades_df
            """)

        print(f"✅ Saved backtest {run_id[:8]}... with {len(trades_df)} trades")
        return run_id

    def get_backtest_history(self, instrument_key: Optional[str] = None,
                             strategy_name: Optional[str] = None,
                             limit: int = 50) -> pd.DataFrame:
        """
        Query backtest history.

        Args:
            instrument_key: Filter by instrument
            strategy_name: Filter by strategy
            limit: Max results

        Returns:
            DataFrame with backtest runs
        """
        query = "SELECT * FROM backtest_runs WHERE 1=1"
        params = []

        if instrument_key:
            query += " AND instrument_key = ?"
            params.append(instrument_key)

        if strategy_name:
            query += " AND strategy_name = ?"
            params.append(strategy_name)

        query += f" ORDER BY created_at DESC LIMIT {limit}"

        return self.con.execute(query, params).df()

    def get_trades(self, run_id: str) -> pd.DataFrame:
        """
        Get all trades for a specific backtest run.

        Args:
            run_id: Backtest run UUID

        Returns:
            DataFrame with trades
        """
        return self.con.execute("""
            SELECT * FROM trades
            WHERE run_id = ?
            ORDER BY entry_time
        """, [run_id]).df()

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    def _update_market_status(self, instrument_key: str,
                              timeframe: str, last_timestamp):
        """Update market status tracking"""
        col_map = {
            '1m': 'last_1m_timestamp',
            '5minute': 'last_5m_timestamp',
            '1day': 'last_1d_timestamp'
        }

        if timeframe not in col_map:
            return

        col = col_map[timeframe]

        # Check if row exists
        existing = self.con.execute("""
            SELECT instrument_key FROM market_status
            WHERE instrument_key = ?
        """, [instrument_key]).fetchone()

        if existing:
            # Update existing row
            self.con.execute(f"""
                UPDATE market_status 
                SET {col} = ?, last_checked = CURRENT_TIMESTAMP
                WHERE instrument_key = ?
            """, [last_timestamp, instrument_key])
        else:
            # Insert new row
            self.con.execute(f"""
                INSERT INTO market_status (
                    instrument_key, {col}, last_checked
                ) VALUES (?, ?, CURRENT_TIMESTAMP)
            """, [instrument_key, last_timestamp])

    def get_data_status(self, instrument_key: str) -> Dict[str, Any]:
        """
        Get data availability status for an instrument.

        Returns:
            Dict with last timestamps for each timeframe
        """
        result = self.con.execute("""
            SELECT * FROM market_status
            WHERE instrument_key = ?
        """, [instrument_key]).fetchone()

        if result is None:
            return {'instrument_key': instrument_key, 'status': 'No data'}

        return dict(zip([desc[0] for desc in self.con.description], result))

    def vacuum(self):
        """Optimize database (run periodically)"""
        self.con.execute("VACUUM")
        print("✅ Database optimized")

    def close(self):
        """
        Close database connection.

        Note: With shared connection pattern, this only removes the reference.
        The actual connection may still be held by session_state.
        """
        # Don't actually close the shared connection - just remove reference
        self.con = None
        print("✅ Database reference released")

    def force_close(self):
        """
        Force close the shared database connection.
        Use this when you really need to release the database lock.
        """
        close_shared_connection()
        self.con = None
        print("✅ Database connection force closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Don't close shared connection on context exit
        pass


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

_global_trading_db: Optional[TradingDB] = None


def get_db() -> TradingDB:
    """
    Get a SINGLE TradingDB instance (singleton pattern).

    This ensures consistent database access across all pages and components.
    The instance is cached in Streamlit session_state (if available) or
    as a global variable (for CLI/scripts).

    The connection is automatically health-checked and reconnected if needed.

    Usage:
        db = get_db()
        result = db.safe_query("SELECT * FROM instruments", fetch='df')
    """
    global _global_trading_db

    session_state = _get_streamlit_session_state()

    if session_state is not None:
        # Streamlit context - use session_state for persistence across reruns
        db_key = "trading_db_instance_v5"  # Bumped version for fatal error handling

        try:
            if db_key not in session_state or session_state[db_key] is None:
                session_state[db_key] = TradingDB()
            return session_state[db_key]
        except Exception as e:
            error_msg = str(e).lower()
            if 'invalidated' in error_msg or 'fatal' in error_msg:
                logger.error(f"🔴 FATAL ERROR in get_db - clearing session and retrying: {e}")
                force_reset_database()
                # Clear any cached DB instance
                if db_key in session_state:
                    session_state[db_key] = None
                session_state[db_key] = TradingDB()
                return session_state[db_key]
            raise

    # Non-Streamlit fallback (CLI, scripts)
    try:
        if _global_trading_db is None:
            _global_trading_db = TradingDB()
        return _global_trading_db
    except Exception as e:
        error_msg = str(e).lower()
        if 'invalidated' in error_msg or 'fatal' in error_msg:
            logger.error(f"🔴 FATAL ERROR in get_db - resetting: {e}")
            force_reset_database()
            _global_trading_db = TradingDB()
            return _global_trading_db
        raise


def db_exists() -> bool:
    """Check if the database file exists"""
    return _DEFAULT_DB_PATH.exists()


if __name__ == "__main__":
    # Test database creation
    db = TradingDB()
    print("✅ Database initialized successfully!")

    # Show tables
    tables = db.con.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'main'
    """).df()

    print(f"\n📊 Created tables:")
    for table in tables['table_name']:
        count = db.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  - {table}: {count} rows")
