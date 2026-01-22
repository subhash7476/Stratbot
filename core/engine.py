"""

DuckDB Database Manager for Trading Bot

Handles all database operations with ACID transactions



Version: 2.1 - Shared Connection Pattern

- Added shared connection management via st.session_state to prevent

  "Can't open a connection to same database file with a different configuration" errors

- All existing TradingDB functionality preserved

"""


import threading

import duckdb

import pandas as pd

from pathlib import Path

from typing import Optional, List, Dict, Any

from datetime import datetime, date

import json

import uuid


# ============================================================================

# SHARED CONNECTION MANAGEMENT

# ============================================================================


# Default database path

_DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "trading_bot.duckdb"


# Global connection storage (for non-Streamlit contexts)

_global_connection: Optional[duckdb.DuckDBPyConnection] = None


class TradingDB:

    _lock = threading.Lock()

    def execute_safe(self, query, params=None):
        """Thread-safe execute"""

        with self._lock:

            if params:

                return self.con.execute(query, params)

            return self.con.execute(query)


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


def get_shared_connection(db_path: Path = None) -> Optional[duckdb.DuckDBPyConnection]:
    """

    Get or create a shared DuckDB connection.



    This function ensures only ONE connection exists per session (Streamlit)

    or globally (non-Streamlit), preventing connection conflicts.



    Args:

        db_path: Path to database file (defaults to data/trading_bot.duckdb)



    Returns:

        DuckDB connection or None if database doesn't exist

    """

    global _global_connection

    if db_path is None:

        db_path = _DEFAULT_DB_PATH

    db_path = Path(db_path)

    if not db_path.exists():

        return None

    session_state = _get_streamlit_session_state()

    # Streamlit context - use session_state

    if session_state is not None:

        conn_key = "shared_duckdb_conn"

        # Check for existing valid connection

        if conn_key in session_state and session_state[conn_key] is not None:

            con = session_state[conn_key]

            try:

                con.execute("SELECT 1").fetchone()

                return con

            except Exception:

                # Connection is stale

                try:

                    con.close()

                except:

                    pass

                session_state[conn_key] = None

        # Create new connection with optimized settings

        try:

            con = duckdb.connect(

                str(db_path),

                config={

                    'threads': 4,

                    'max_memory': '4GB',

                    'default_order': 'ASC'

                }

            )

            session_state[conn_key] = con

            return con

        except Exception as e:

            print(f"Failed to connect to database: {e}")

            return None

    # Non-Streamlit context - use global variable

    else:

        if _global_connection is not None:

            try:

                _global_connection.execute("SELECT 1").fetchone()

                return _global_connection

            except Exception:

                try:

                    _global_connection.close()

                except:

                    pass

                _global_connection = None

        # Create new connection

        try:

            _global_connection = duckdb.connect(

                str(db_path),

                config={

                    'threads': 4,

                    'max_memory': '4GB',

                    'default_order': 'ASC'

                }

            )

            return _global_connection

        except Exception as e:

            print(f"Failed to connect to database: {e}")

            return None


def close_shared_connection():
    """

    Close the shared database connection.

    Call this when you need to release the database lock.

    """

    global _global_connection

    session_state = _get_streamlit_session_state()

    # Close Streamlit session connection

    if session_state is not None:

        con = session_state.pop("shared_duckdb_conn", None)

        if con is not None:

            try:

                con.close()

            except:

                pass

    # Close global connection

    if _global_connection is not None:

        try:

            _global_connection.close()

        except:

            pass

        _global_connection = None


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


class TradingDB:

    """

    Main database interface for the trading bot.

    Provides methods for OHLCV storage, instrument management, and backtesting.



    Now uses shared connection pattern to prevent connection conflicts

    between multiple Streamlit pages.

    """

    def __init__(self, db_path: Path = None):
        """

        Initialize database connection and create schema if needed.



        Args:

            db_path: Path to .duckdb file (default: data/trading_bot.duckdb)

        """

        if db_path is None:

            db_path = _DEFAULT_DB_PATH

        self.db_path = Path(db_path)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Use shared connection instead of creating new one

        self.con = get_shared_connection(self.db_path)

        if self.con is None:

            # Database doesn't exist yet, create it

            self.con = duckdb.connect(

                str(self.db_path),

                config={

                    'threads': 4,

                    'max_memory': '4GB',

                    'default_order': 'ASC'

                }

            )

            # Store in session state if available

            session_state = _get_streamlit_session_state()

            if session_state is not None:

                session_state["shared_duckdb_conn"] = self.con

            else:

                global _global_connection

                _global_connection = self.con

        self._create_schema()

        print(f"✅ Connected to DuckDB: {self.db_path}")

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

                strike_price DOUBLE,

                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP

            )

        """)

        self.con.execute("""

            CREATE INDEX IF NOT EXISTS idx_instruments_segment 

            ON instruments(segment)

        """)

        self.con.execute("""

            CREATE INDEX IF NOT EXISTS idx_instruments_name 

            ON instruments(name)

        """)

        # Table 2: 1-minute OHLCV

        self.con.execute("""

            CREATE TABLE IF NOT EXISTS ohlcv_1m (

                instrument_key VARCHAR NOT NULL,

                timestamp TIMESTAMP NOT NULL,

                open DOUBLE NOT NULL,

                high DOUBLE NOT NULL,

                low DOUBLE NOT NULL,

                close DOUBLE NOT NULL,

                volume BIGINT NOT NULL,

                oi BIGINT DEFAULT 0,

                PRIMARY KEY (instrument_key, timestamp)

            )

        """)

        self.con.execute("""

            CREATE INDEX IF NOT EXISTS idx_ohlcv_1m_key_time 

            ON ohlcv_1m(instrument_key, timestamp)

        """)

        # Table 3: Resampled OHLCV

        self.con.execute("""

            CREATE TABLE IF NOT EXISTS ohlcv_resampled (

                instrument_key VARCHAR NOT NULL,

                timeframe VARCHAR NOT NULL,

                timestamp TIMESTAMP NOT NULL,

                open DOUBLE NOT NULL,

                high DOUBLE NOT NULL,

                low DOUBLE NOT NULL,

                close DOUBLE NOT NULL,

                volume BIGINT NOT NULL,

                oi BIGINT DEFAULT 0,

                PRIMARY KEY (instrument_key, timeframe, timestamp)

            )

        """)

        self.con.execute("""

            CREATE INDEX IF NOT EXISTS idx_resampled_key_tf_time 

            ON ohlcv_resampled(instrument_key, timeframe, timestamp)

        """)

        # Table 4: Indicators

        self.con.execute("""

            CREATE TABLE IF NOT EXISTS indicators (

                instrument_key VARCHAR NOT NULL,

                timeframe VARCHAR NOT NULL,

                timestamp TIMESTAMP NOT NULL,

                indicator_set VARCHAR NOT NULL,

                values JSON NOT NULL,

                PRIMARY KEY (instrument_key, timeframe, timestamp, indicator_set)

            )

        """)

        # Table 5: Backtest Runs

        self.con.execute("""

            CREATE TABLE IF NOT EXISTS backtest_runs (

                run_id VARCHAR PRIMARY KEY,

                strategy_name VARCHAR NOT NULL,

                instrument_key VARCHAR NOT NULL,

                timeframe VARCHAR NOT NULL,

                start_date DATE NOT NULL,

                end_date DATE NOT NULL,

                parameters JSON NOT NULL,

                initial_capital DOUBLE NOT NULL,

                final_capital DOUBLE,

                total_trades INTEGER,

                win_rate DOUBLE,

                profit_factor DOUBLE,

                sharpe_ratio DOUBLE,

                max_drawdown_pct DOUBLE,

                metrics JSON,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

            )

        """)

        # Table 6: Trades

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

                quantity INTEGER NOT NULL,

                exit_reason VARCHAR,

                pnl DOUBLE,

                pnl_pct DOUBLE,

                commission DOUBLE DEFAULT 0,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

            )

        """)

        self.con.execute("""

            CREATE INDEX IF NOT EXISTS idx_trades_run 

            ON trades(run_id)

        """)

        # Table 7: Live Positions

        self.con.execute("""

            CREATE TABLE IF NOT EXISTS live_positions (

                position_id VARCHAR PRIMARY KEY,

                instrument_key VARCHAR NOT NULL,

                direction VARCHAR NOT NULL,

                entry_time TIMESTAMP NOT NULL,

                entry_price DOUBLE NOT NULL,

                quantity INTEGER NOT NULL,

                stop_loss DOUBLE,

                target DOUBLE,

                current_pnl DOUBLE,

                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP

            )

        """)

        # Table 8: Market Status

        self.con.execute("""

            CREATE TABLE IF NOT EXISTS market_status (

                instrument_key VARCHAR PRIMARY KEY,

                last_1m_timestamp TIMESTAMP,

                last_5m_timestamp TIMESTAMP,

                last_1d_timestamp TIMESTAMP,

                data_quality_score DOUBLE,

                last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP

            )

        """)

    # ========================================================================

    # INSTRUMENTS MANAGEMENT

    # ========================================================================

    def upsert_instruments(self, df: pd.DataFrame):
        """

        Insert or update instrument master data.



        Args:

            df: DataFrame with columns matching instruments table

        """

        # Ensure required columns

        required_cols = ['instrument_key', 'trading_symbol', 'segment']

        missing = set(required_cols) - set(df.columns)

        if missing:

            raise ValueError(f"Missing required columns: {missing}")

        # Add last_updated

        df = df.copy()

        df['last_updated'] = datetime.now()

        # Handle expiry column conversion

        if 'expiry' in df.columns:

            # Convert from timestamp (numeric) to date if needed

            if pd.api.types.is_numeric_dtype(df['expiry']):

                # Expiry is stored as Unix timestamp in milliseconds

                df['expiry'] = pd.to_datetime(

                    df['expiry'], unit='ms', errors='coerce')

            # Convert to date (remove time component)

            if pd.api.types.is_datetime64_any_dtype(df['expiry']):

                df['expiry'] = df['expiry'].dt.date

            # Replace NaT/None with None

            df['expiry'] = df['expiry'].where(pd.notna(df['expiry']), None)

        # Handle strike_price - ensure it's numeric or None

        if 'strike_price' in df.columns:

            df['strike_price'] = pd.to_numeric(

                df['strike_price'], errors='coerce')

        self.con.execute("""

            INSERT OR REPLACE INTO instruments 

            SELECT * FROM df

        """)

        print(f"✅ Upserted {len(df)} instruments")

    def get_instruments(self, segment: Optional[str] = None,

                        name: Optional[str] = None) -> pd.DataFrame:
        """

        Query instruments by segment or name.



        Args:

            segment: Filter by segment (e.g., 'NSE_EQ', 'NSE_FO')

            name: Filter by underlying name (e.g., 'RELIANCE', 'BANKNIFTY')



        Returns:

            DataFrame with matching instruments

        """

        query = "SELECT * FROM instruments WHERE 1=1"

        params = []

        if segment:

            query += " AND segment = ?"

            params.append(segment)

        if name:

            query += " AND name = ?"

            params.append(name)

        return self.con.execute(query, params).df()

    def get_symbol_mapping(self) -> Dict[str, str]:
        """

        Get mapping of trading_symbol -> instrument_key.



        Returns:

            Dict mapping trading symbols to instrument keys

        """

        try:

            result = self.con.execute("""

                SELECT trading_symbol, instrument_key

                FROM instruments

                WHERE trading_symbol IS NOT NULL

                AND instrument_key IS NOT NULL

            """).fetchall()

            return {row[0]: row[1] for row in result}

        except:

            return {}

    def get_trading_symbols(self) -> List[str]:
        """

        Get list of all trading symbols.



        Returns:

            List of trading symbols

        """

        try:

            result = self.con.execute("""

                SELECT DISTINCT trading_symbol

                FROM instruments

                WHERE trading_symbol IS NOT NULL

                ORDER BY trading_symbol

            """).fetchall()

            return [row[0] for row in result]

        except:

            return []

    # ========================================================================

    # OHLCV DATA MANAGEMENT

    # ========================================================================

    def upsert_ohlcv_1m(self, df: pd.DataFrame, instrument_key: str):
        """

        Insert/update 1-minute OHLCV data (handles duplicates).



        Args:

            df: DataFrame with columns: timestamp, Open, High, Low, Close, Volume

            instrument_key: Instrument identifier (e.g., 'NSE_EQ|INE002A01018')

        """

        # Standardize column names

        df = df.copy()

        df.columns = df.columns.str.lower()

        # Add instrument_key column

        df['instrument_key'] = instrument_key

        # Add OI column if missing

        if 'oi' not in df.columns:

            df['oi'] = 0

        # Ensure timestamp is datetime

        if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):

            df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Select only needed columns

        cols = ['instrument_key', 'timestamp', 'open',

                'high', 'low', 'close', 'volume', 'oi']

        df = df[cols]

        # Insert or replace (upsert)

        self.con.execute("""

            INSERT OR REPLACE INTO ohlcv_1m 

            SELECT * FROM df

        """)

        # Update market status

        self._update_market_status(instrument_key, '1m', df['timestamp'].max())

        print(f"✅ Upserted {len(df)} 1m candles for {instrument_key}")

    def get_ohlcv_1m(self, instrument_key: str,

                     start_date: str, end_date: str) -> pd.DataFrame:
        """

        Query 1-minute OHLCV data for date range.



        Args:

            instrument_key: Instrument identifier

            start_date: Start date (YYYY-MM-DD)

            end_date: End date (YYYY-MM-DD)



        Returns:

            DataFrame with OHLCV data

        """

        df = self.con.execute("""

            SELECT timestamp, open, high, low, close, volume, oi

            FROM ohlcv_1m

            WHERE instrument_key = ?

              AND timestamp BETWEEN ? AND ?

            ORDER BY timestamp

        """, [instrument_key, start_date, end_date]).df()

        # Capitalize columns for compatibility

        df.columns = ['timestamp', 'Open', 'High',

                      'Low', 'Close', 'Volume', 'OI']

        df.set_index('timestamp', inplace=True)

        return df

    def resample_to_timeframe(self, instrument_key: str,

                              timeframe: str,

                              start_date: Optional[str] = None):
        """

        Resample 1m data to higher timeframe and store in ohlcv_resampled.

        Uses SQL for maximum performance (10-50x faster than Pandas).



        Args:

            instrument_key: Instrument identifier

            timeframe: Target timeframe ('5minute', '15minute', '30minute', '1hour', '1day')

            start_date: Only resample from this date (for incremental updates)

        """

        # Map timeframe to SQL interval

        interval_map = {

            '5minute': '5 minutes',

            '15minute': '15 minutes',

            '30minute': '30 minutes',

            '1hour': '1 hour',

            '1day': '1 day'

        }

        if timeframe not in interval_map:

            raise ValueError(f"Unsupported timeframe: {timeframe}")

        interval = interval_map[timeframe]

        # Determine start point

        if start_date is None:

            # Get last timestamp in resampled table

            result = self.con.execute("""

                SELECT MAX(timestamp) as last_ts

                FROM ohlcv_resampled

                WHERE instrument_key = ? AND timeframe = ?

            """, [instrument_key, timeframe]).fetchone()

            if result[0] is not None:

                # Start from 1 period before last timestamp (overlap correction)

                start_date = result[0]

            else:

                # Full resample

                start_date = '1900-01-01'

        # SQL-based resampling (SUPER FAST!)

        self.con.execute(f"""

            INSERT OR REPLACE INTO ohlcv_resampled

            SELECT 

                instrument_key,

                '{timeframe}' as timeframe,

                time_bucket(INTERVAL '{interval}', timestamp) as timestamp,

                first(open) as open,

                max(high) as high,

                min(low) as low,

                last(close) as close,

                sum(volume) as volume,

                last(oi) as oi

            FROM ohlcv_1m

            WHERE instrument_key = ?

              AND timestamp >= ?

            GROUP BY instrument_key, time_bucket(INTERVAL '{interval}', timestamp)

        """, [instrument_key, start_date])

        # Update market status

        last_ts = self.con.execute("""

            SELECT MAX(timestamp) FROM ohlcv_resampled

            WHERE instrument_key = ? AND timeframe = ?

        """, [instrument_key, timeframe]).fetchone()[0]

        self._update_market_status(instrument_key, timeframe, last_ts)

        print(f"✅ Resampled {instrument_key} to {timeframe}")

    def get_ohlcv_resampled(self, instrument_key: str, timeframe: str,

                            start_date: str, end_date: str) -> pd.DataFrame:
        """

        Query resampled OHLCV data.



        Args:

            instrument_key: Instrument identifier

            timeframe: Timeframe to query

            start_date: Start date (YYYY-MM-DD)

            end_date: End date (YYYY-MM-DD)



        Returns:

            DataFrame with OHLCV data

        """

        df = self.con.execute("""

            SELECT timestamp, open, high, low, close, volume, oi

            FROM ohlcv_resampled

            WHERE instrument_key = ?

              AND timeframe = ?

              AND timestamp BETWEEN ? AND ?

            ORDER BY timestamp

        """, [instrument_key, timeframe, start_date, end_date]).df()

        # Capitalize columns

        df.columns = ['timestamp', 'Open', 'High',

                      'Low', 'Close', 'Volume', 'OI']

        df.set_index('timestamp', inplace=True)

        return df

    # ========================================================================

    # BACKTEST MANAGEMENT

    # ========================================================================

    def save_backtest_results(self, strategy_name: str, instrument_key: str,

                              timeframe: str, start_date: date, end_date: date,

                              parameters: Dict[str, Any], initial_capital: float,

                              trades_df: pd.DataFrame, metrics: Dict[str, Any]) -> str:
        """

        Save backtest results to database.



        Args:

            strategy_name: Name of strategy

            instrument_key: Tested instrument

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


# core/database.py


def get_db() -> TradingDB:
    """

    Get a SINGLE TradingDB instance per Streamlit session.

    """

    session_state = _get_streamlit_session_state()

    if session_state is not None:

        if "trading_db_instance" not in session_state or session_state["trading_db_instance"] is None:

            session_state["trading_db_instance"] = TradingDB()

        return session_state["trading_db_instance"]

    # Non-Streamlit fallback (CLI, scripts)

    global _global_connection

    return TradingDB()


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
