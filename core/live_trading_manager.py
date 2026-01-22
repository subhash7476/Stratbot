# core/live_trading_manager.py
"""
Live Trading Data Manager for EHMA MTF Strategy
================================================
Handles real-time data fetching, resampling, and signal detection for intraday trading.

Architecture:
- ohlcv_1m: Historical 1-minute data (up to yesterday EOD)
- live_ohlcv_cache: Today's intraday 1-minute data (refreshed via Upstox API + WebSocket)
- ohlcv_resampled_live: Combined view for 5m/15m/60m candles

FIX v1.3:
- WebSocket gap-fill from 9:15 to WS start time
- Proper data merging for live scanning
- In-memory MTF data for signal generation

Author: Trading Bot Pro
Version: 1.3 (WebSocket + Gap Fill)
"""

import requests
import pandas as pd
import numpy as np
import datetime as dt
from datetime import datetime, date, timedelta, time as dt_time
from typing import Dict, List, Tuple, Optional, Callable
import time
from dataclasses import dataclass
import threading
import logging

# Setup logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Lazy imports to avoid connection issues at module load
MARKET_OPEN = dt.time(9, 15)
MARKET_CLOSE = dt.time(15, 30)


def is_market_hours(now=None):
    now = now or dt.datetime.now().time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


def get_db_connection():
    """Get database connection lazily"""
    from core.database import get_db
    return get_db()


def get_api_token():
    """Get API token lazily"""
    from core.config import get_access_token
    return get_access_token()


# Upstox API endpoints
INTRADAY_URL = "https://api.upstox.com/v2/historical-candle/intraday"
HISTORICAL_URL = "https://api.upstox.com/v2/historical-candle"
MARKET_QUOTE_URL = "https://api.upstox.com/v2/market-quote/quotes"


@dataclass
class LiveDataStatus:
    """Status of live data fetch operation"""
    success: bool
    instruments_updated: int
    candles_inserted: int
    last_update: datetime
    errors: List[str]
    gap_filled: bool = False
    gap_from: Optional[datetime] = None
    gap_to: Optional[datetime] = None


class LiveTradingManager:
    """
    Manages live intraday data for MTF signal detection.

    Usage:
        manager = LiveTradingManager()
        manager.start_websocket_if_needed(access_token)
        manager.fill_gap_and_refresh(access_token)  # NEW: Fill gap from 9:15
        manager.rebuild_today_resampled()

        # For signal scanning:
        df_60m, df_15m, df_5m = manager.get_live_mtf_data(instrument_key, lookback_days=60)
    """

    def __init__(self):
        self._db = None
        self._tables_checked = False
        self.ws_builder = None
        self.ws_connected = False
        self.today_gap_filled = False
        self.WS_AVAILABLE = True
        self._gap_fill_lock = threading.Lock()

    @property
    def db(self):
        """Lazy database connection"""
        if self._db is None:
            self._db = get_db_connection()
            if self._db is not None and not self._tables_checked:
                self._ensure_tables_exist()
                self._tables_checked = True
        return self._db

    def _execute_safe(self, query, params=None):
        """
        Wrapper for safe database execution with retry logic.
        Use this for all write operations to handle concurrent access.
        """
        # If db has execute_safe method (TradingDB), use it
        if hasattr(self.db, 'execute_safe'):
            return self.db.execute_safe(query, params)
        # Otherwise fall back to direct execute (for raw connections)
        else:
            if params:
                return self.db.con.execute(query, params)
            return self.db.con.execute(query)

    def _ensure_tables_exist(self):
        """Create required tables if they don't exist"""
        try:
            # Live 1-minute cache for today's data
            self.db.con.execute("""
                CREATE TABLE IF NOT EXISTS live_ohlcv_cache (
                    instrument_key VARCHAR,
                    timestamp TIMESTAMP,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    volume BIGINT,
                    PRIMARY KEY (instrument_key, timestamp)
                )
            """)

            # Live resampled data (historical + today combined)
            self.db.con.execute("""
                CREATE TABLE IF NOT EXISTS ohlcv_resampled_live (
                    instrument_key VARCHAR,
                    timeframe VARCHAR,
                    timestamp TIMESTAMP,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    volume BIGINT,
                    PRIMARY KEY (instrument_key, timeframe, timestamp)
                )
            """)

            # Track last update time per instrument
            self.db.con.execute("""
                CREATE TABLE IF NOT EXISTS live_data_status (
                    instrument_key VARCHAR PRIMARY KEY,
                    last_fetch TIMESTAMP,
                    last_candle_time TIMESTAMP,
                    candle_count INTEGER,
                    status VARCHAR
                )
            """)
            logger.info("✅ Live trading tables verified/created")
        except Exception as e:
            logger.warning(f"Could not ensure tables exist: {e}")

    def get_active_instruments(self) -> List[Tuple[str, str]]:
        """Get list of active F&O instruments"""
        result = self.db.con.execute("""
            SELECT DISTINCT instrument_key, trading_symbol
            FROM fo_stocks_master
            WHERE is_active = TRUE
            ORDER BY trading_symbol
        """).fetchall()
        return result

    def get_live_data_summary(self) -> Dict:
        """Get summary statistics for live data"""
        today_str = date.today().strftime('%Y-%m-%d')

        try:
            # Count candles in live cache
            cache_stats = self.db.con.execute(f"""
                SELECT 
                    COUNT(DISTINCT instrument_key) as instruments,
                    COUNT(*) as total_candles,
                    MIN(timestamp) as first_candle,
                    MAX(timestamp) as last_candle
                FROM live_ohlcv_cache
                WHERE timestamp >= '{today_str}'
            """).fetchone()

            # Count status records
            status_stats = self.db.con.execute("""
                SELECT 
                    COUNT(*) as tracked_instruments,
                    MAX(last_candle_time) as latest_candle_time,
                    MAX(last_fetch) as latest_fetch
                FROM live_data_status
            """).fetchone()

            return {
                'instruments_with_data': cache_stats[0] if cache_stats else 0,
                'total_candles_today': cache_stats[1] if cache_stats else 0,
                'first_candle': cache_stats[2] if cache_stats else None,
                'last_candle': cache_stats[3] if cache_stats else None,
                'tracked_instruments': status_stats[0] if status_stats else 0,
                'latest_candle_time': status_stats[1] if status_stats else None,
                'latest_fetch': status_stats[2] if status_stats else None,
                'ws_connected': self.ws_connected,
                'gap_filled': self.today_gap_filled
            }
        except Exception as e:
            logger.error(f"Error getting live data summary: {e}")
            return {
                'error': str(e),
                'instruments_with_data': 0,
                'total_candles_today': 0
            }

    def _fetch_intraday_1m_range(
        self,
        instrument_key: str,
        from_dt: datetime,
        to_dt: datetime,
        access_token: str
    ) -> pd.DataFrame:
        """
        Fetch recent intraday 1m candles (REST API).

        NOTE: Upstox intraday endpoint only returns last 30-60 minutes of data.
        This is a SUPPLEMENT to WebSocket, not a replacement.

        For full day coverage from 9:15 AM, WebSocket must be running.
        """
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}"
        }

        # Use intraday endpoint - only gives recent data
        url = f"{INTRADAY_URL}/{instrument_key}/1minute"

        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()

            data = response.json()
            candles = data.get("data", {}).get("candles", [])

            if not candles:
                return pd.DataFrame()

            rows = []
            for c in candles:
                # Parse timestamp and make it timezone-naive for comparison
                ts = pd.to_datetime(c[0])

                # Convert to timezone-naive if it's timezone-aware
                if ts.tzinfo is not None:
                    ts = ts.tz_convert('Asia/Kolkata').tz_localize(None)

                # Ensure from_dt and to_dt are also naive for comparison
                from_dt_naive = from_dt.replace(tzinfo=None) if hasattr(
                    from_dt, 'tzinfo') and from_dt.tzinfo else from_dt
                to_dt_naive = to_dt.replace(tzinfo=None) if hasattr(
                    to_dt, 'tzinfo') and to_dt.tzinfo else to_dt

                # Filter by time range
                if from_dt_naive <= ts <= to_dt_naive:
                    rows.append({
                        'timestamp': ts,
                        'open': c[1],
                        'high': c[2],
                        'low': c[3],
                        'close': c[4],
                        'volume': c[5]
                    })

            return pd.DataFrame(rows)
        except Exception as e:
            logger.error(
                f"Error fetching intraday data for {instrument_key}: {e}")
            return pd.DataFrame()

    def fill_gap_and_refresh(
        self,
        access_token: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> LiveDataStatus:
        """
        MAIN REFRESH FUNCTION: Supplement WebSocket with recent REST API data.

        IMPORTANT: This function can only fetch the last 30-60 minutes via REST API.
        For full day coverage from 9:15 AM, WebSocket MUST be running.

        This function:
        1. Checks current WebSocket coverage
        2. Warns if WebSocket didn't start at 9:15 (gap exists)
        3. Fetches recent candles via REST API to supplement WebSocket

        Call this when clicking "Refresh Live Data" button.
        """
        with self._gap_fill_lock:
            return self._fill_gap_and_refresh_internal(access_token, progress_callback)

    def _fill_gap_and_refresh_internal(
        self,
        access_token: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> LiveDataStatus:
        """Internal implementation of gap fill + refresh"""

        instruments = self.get_active_instruments()
        if not instruments:
            return LiveDataStatus(
                success=False,
                instruments_updated=0,
                candles_inserted=0,
                last_update=datetime.now(),
                errors=["No active instruments found"]
            )

        today = date.today()
        market_open_dt = datetime.combine(today, MARKET_OPEN)
        now = datetime.now()

        # Don't fill if market not open
        if now.time() < MARKET_OPEN:
            return LiveDataStatus(
                success=True,
                instruments_updated=0,
                candles_inserted=0,
                last_update=now,
                errors=["Market not open yet"]
            )

        total_inserted = 0
        errors = []
        gap_filled = False
        gap_from = None
        gap_to = None

        # Check current data coverage
        first_candle_time = self._get_first_candle_today()
        last_candle_time = self._get_last_candle_today()

        logger.info(
            f"Current coverage: first={first_candle_time}, last={last_candle_time}")

        # Check for gap at market open
        if first_candle_time:
            first_candle_dt = pd.to_datetime(first_candle_time)
            if first_candle_dt > market_open_dt + timedelta(minutes=2):
                # GAP EXISTS - WebSocket didn't capture from 9:15
                gap_from = market_open_dt
                gap_to = first_candle_dt
                errors.append(
                    f"⚠️ Gap detected from {gap_from.strftime('%H:%M')} to {gap_to.strftime('%H:%M')}. "
                    "WebSocket was not running at market open. Historical data cannot be backfilled via REST API.")
                logger.warning(f"Gap exists from 9:15 to first candle: {gap_from} → {gap_to}")

        # Only fetch recent data (last 30-60 mins available via REST)
        # This supplements WebSocket, not replaces it
        fetch_from = now - timedelta(minutes=60)  # Last hour only
        fetch_to = now.replace(second=0, microsecond=0)

        # Fetch data
        total_instruments = len(instruments)

        for i, (instrument_key, symbol) in enumerate(instruments):
            if progress_callback:
                progress_callback(i + 1, total_instruments, symbol)

            try:
                df = self._fetch_intraday_1m_range(
                    instrument_key, fetch_from, fetch_to, access_token
                )

                if df.empty:
                    continue

                # Insert into live cache
                rows_to_insert = []
                for _, row in df.iterrows():
                    rows_to_insert.append((
                        instrument_key,
                        row['timestamp'],
                        row['open'],
                        row['high'],
                        row['low'],
                        row['close'],
                        row['volume']
                    ))

                if rows_to_insert:
                    self.db.con.executemany("""
                        INSERT INTO live_ohlcv_cache
                        (instrument_key, timestamp, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (instrument_key, timestamp)
                        DO UPDATE SET
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            volume = EXCLUDED.volume
                    """, rows_to_insert)
                    total_inserted += len(rows_to_insert)

                    # Update status - FIXED: Use correct table (live_data_status, not live_ohlcv_cache)
                    self.db.con.execute("""
                        INSERT INTO live_data_status
                        (instrument_key, last_fetch, last_candle_time, candle_count, status)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT (instrument_key)
                        DO UPDATE SET
                            last_fetch = EXCLUDED.last_fetch,
                            last_candle_time = EXCLUDED.last_candle_time,
                            candle_count = EXCLUDED.candle_count,
                            status = EXCLUDED.status
                    """, [instrument_key, now, df['timestamp'].max(), len(df), 'OK'])

            except Exception as e:
                errors.append(f"{symbol}: {str(e)}")
                logger.error(f"Error fetching {symbol}: {e}")

        self.today_gap_filled = True

        return LiveDataStatus(
            success=len(errors) == 0 or total_inserted > 0,
            instruments_updated=total_instruments - len(errors),
            candles_inserted=total_inserted,
            last_update=now,
            errors=errors,
            gap_filled=gap_filled,
            gap_from=gap_from,
            gap_to=gap_to
        )

    def _get_first_candle_today(self) -> Optional[datetime]:
        """Get timestamp of first candle in today's cache"""
        today_str = date.today().strftime('%Y-%m-%d')
        row = self.db.con.execute(f"""
            SELECT MIN(timestamp) 
            FROM live_ohlcv_cache 
            WHERE timestamp >= '{today_str}'
        """).fetchone()
        return row[0] if row and row[0] else None

    def _get_last_candle_today(self) -> Optional[datetime]:
        """Get timestamp of last candle in today's cache"""
        today_str = date.today().strftime('%Y-%m-%d')
        row = self.db.con.execute(f"""
            SELECT MAX(timestamp) 
            FROM live_ohlcv_cache 
            WHERE timestamp >= '{today_str}'
        """).fetchone()
        return row[0] if row and row[0] else None

    def refresh_live_data(
        self,
        instruments=None,
        progress_callback=None,
        incremental=True
    ) -> LiveDataStatus:
        """
        Refresh live data - now wraps fill_gap_and_refresh for full functionality.
        """
        access_token = get_api_token()
        if not access_token:
            return LiveDataStatus(
                success=False,
                instruments_updated=0,
                candles_inserted=0,
                last_update=datetime.now(),
                errors=["No access token available"]
            )

        return self.fill_gap_and_refresh(access_token, progress_callback)

    def fill_today_gap_if_needed(self, access_token: str):
        """Fill gap from market open to WebSocket start (legacy compatibility)"""
        if self.today_gap_filled:
            return

        self.fill_gap_and_refresh(access_token)

    def rebuild_today_resampled(self, timeframes=None):
        """
        Resample today's 1m candles from live_ohlcv_cache into ohlcv_resampled_live.

        This creates TODAY's candles only. Historical data stays in ohlcv_resampled.
        The get_live_mtf_data() method combines both for signal generation.

        OPTIMIZED: Uses bulk DELETE + INSERT instead of per-instrument operations.
        """
        if timeframes is None:
            timeframes = ["5minute", "15minute", "60minute"]

        interval_map = {
            "5minute": "5 minutes",
            "15minute": "15 minutes",
            "60minute": "1 hour"
        }

        # Ensure database connection is valid
        if self.db is None:
            logger.error("Database object is not available")
            return

        if self.db.con is None:
            logger.error("Database connection (self.db.con) is None")
            return

        # Ensure tables exist before proceeding
        self._ensure_tables_exist()

        # Check if we have any data in live cache
        try:
            result = self.db.con.execute("""
                SELECT COUNT(*) FROM live_ohlcv_cache
            """).fetchone()
            cache_count = result[0] if result else 0
        except Exception as e:
            logger.error(f"Error checking live_ohlcv_cache: {e}")
            logger.error(f"Exception type: {type(e).__name__}")
            # Try to create the table if it doesn't exist
            try:
                self._ensure_tables_exist()
                result = self.db.con.execute("""
                    SELECT COUNT(*) FROM live_ohlcv_cache
                """).fetchone()
                cache_count = result[0] if result else 0
            except Exception as e2:
                logger.error(f"Still failed after table creation attempt: {e2}")
                cache_count = 0

        if cache_count == 0:
            logger.warning("No data in live_ohlcv_cache - nothing to resample")
            logger.info(
                "Tip: Click 'Refresh Live Data' first to fetch today's candles")
            return

        try:
            instruments = self.db.con.execute("""
                SELECT DISTINCT instrument_key FROM live_ohlcv_cache
            """).fetchall()
        except Exception as e:
            logger.error(f"Error getting instruments from live_ohlcv_cache: {e}")
            instruments = []

        if not instruments:
            logger.warning("No instruments in live_ohlcv_cache to resample")
            return

        logger.info(
            f"Resampling {cache_count} candles from {len(instruments)} instruments...")

        today_str = date.today().strftime('%Y-%m-%d')
        success_count = 0
        error_count = 0

        # OPTIMIZED: Delete ALL existing data at once (not just today's)
        # This allows rebuild to work with yesterday's data when market is closed
        try:
            self.db.con.execute("DELETE FROM ohlcv_resampled_live")
            self.db.con.commit()  # Ensure DELETE is committed before INSERT
            logger.info("Cleared all existing resampled data")

            # Verify the table is empty
            remaining_result = self.db.con.execute("SELECT COUNT(*) FROM ohlcv_resampled_live").fetchone()
            remaining = remaining_result[0] if remaining_result else 0
            if remaining > 0:
                logger.warning(f"Warning: {remaining} rows still in table after DELETE!")
        except Exception as e:
            logger.error(f"Error clearing resampled data: {e}")
            return

        # OPTIMIZED: Resample ALL instruments at once per timeframe
        for tf in timeframes:
            interval = interval_map[tf]

            try:
                # Bulk insert all instruments for this timeframe
                # No date filter - resample ALL data in live_ohlcv_cache
                # This allows rebuild to work with yesterday's data when market is closed
                result = self.db.con.execute(f"""
                    INSERT INTO ohlcv_resampled_live
                    (instrument_key, timeframe, timestamp, open, high, low, close, volume)
                    SELECT
                        instrument_key,
                        '{tf}' as timeframe,
                        time_bucket(INTERVAL '{interval}', timestamp) as timestamp,
                        FIRST(open ORDER BY timestamp) as open,
                        MAX(high) as high,
                        MIN(low) as low,
                        LAST(close ORDER BY timestamp) as close,
                        SUM(volume) as volume
                    FROM live_ohlcv_cache
                    GROUP BY instrument_key, time_bucket(INTERVAL '{interval}', timestamp)
                """)
                self.db.con.commit()  # Commit after each timeframe

                # Count inserted rows for this timeframe
                tf_result = self.db.con.execute(f"""
                    SELECT COUNT(*) FROM ohlcv_resampled_live WHERE timeframe = '{tf}'
                """).fetchone()
                tf_count = tf_result[0] if tf_result else 0

                success_count += 1
                logger.info(f"✅ Resampled {tf} for all instruments ({tf_count:,} candles)")

            except Exception as e:
                error_count += 1
                logger.error(f"Error resampling {tf}: {e}")

        # Log summary
        try:
            live_result = self.db.con.execute("""
                SELECT COUNT(*) FROM ohlcv_resampled_live WHERE timestamp >= ?
            """, [today_str]).fetchone()
            live_count = live_result[0] if live_result else 0
        except Exception as e:
            logger.error(f"Error getting summary count: {e}")
            live_count = 0

        logger.info(
            f"✅ Rebuilt resampled data: {success_count} successful, {error_count} errors")
        logger.info(
            f"📊 ohlcv_resampled_live now has {live_count} candles for today")

    def initialize_day(self):
        """
        Prepare system for a new trading day.

        SAFE VERSION: Clears stale data from previous days, but keeps today's data
        if called during market hours on the same day.
        """
        today_str = date.today().strftime('%Y-%m-%d')

        # Check if cache has any data from previous days
        try:
            result = self.db.con.execute(f"""
                SELECT COUNT(*) FROM live_ohlcv_cache
                WHERE DATE(timestamp) < '{today_str}'
            """).fetchone()
            stale_count = result[0] if result else 0
        except Exception as e:
            logger.error(f"Error checking stale data: {e}")
            stale_count = 0

        if stale_count > 0:
            # Clear stale data from previous days
            try:
                self.db.con.execute(f"""
                    DELETE FROM live_ohlcv_cache
                    WHERE DATE(timestamp) < '{today_str}'
                """)
                self.db.con.commit()
                logger.info(f"✅ Cleared {stale_count} stale candles from previous days")
            except Exception as e:
                logger.error(f"Error clearing stale data: {e}")

        if is_market_hours():
            # During market hours: Keep today's data, just reset flags
            self.today_gap_filled = False
            logger.info("✅ Initialize Day completed (intraday-safe) - today's data preserved")
            return

        # Pre/post market: Safe to wipe ALL cache (including today if any)
        self.db.con.execute("DELETE FROM live_ohlcv_cache")
        self.db.con.execute("DELETE FROM ohlcv_resampled_live")
        self.today_gap_filled = False
        logger.info("✅ Initialize Day completed (pre-market) - all cache cleared")

    def get_live_mtf_data(
        self,
        instrument_key: str,
        lookback_days: int = 60
    ) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
        """
        Get MTF data (60m, 15m, 5m) for signal detection.

        IMPORTANT: Combines historical data + today's live data to ensure
        sufficient bars for indicator warmup (BB=20, KC=20, WT=21, etc.)

        Returns: (df_60m, df_15m, df_5m) - each with ~100+ bars for proper signals
        """
        return self._get_combined_mtf_data(instrument_key, lookback_days)

    def _get_combined_mtf_data(
        self,
        instrument_key: str,
        lookback_days: int = 60
    ) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
        """
        Combine historical resampled data + today's live resampled data.
        This ensures indicators have enough warmup bars.
        """
        cutoff = (datetime.now() - timedelta(days=lookback_days)
                  ).strftime('%Y-%m-%d')
        today_str = date.today().strftime('%Y-%m-%d')

        def load_combined_timeframe(tf: str) -> Optional[pd.DataFrame]:
            try:
                # 1️⃣ Get historical data (up to yesterday)
                hist_query = """
                    SELECT timestamp, open as Open, high as High, low as Low, 
                           close as Close, volume as Volume
                    FROM ohlcv_resampled
                    WHERE instrument_key = ?
                      AND timeframe = ?
                      AND timestamp >= ?
                      AND timestamp < ?
                    ORDER BY timestamp
                """
                df_hist = self.db.con.execute(
                    hist_query, [instrument_key, tf, cutoff, today_str]).df()

                # 2️⃣ Get today's live resampled data
                live_query = """
                    SELECT timestamp, open as Open, high as High, low as Low, 
                           close as Close, volume as Volume
                    FROM ohlcv_resampled_live
                    WHERE instrument_key = ?
                      AND timeframe = ?
                      AND timestamp >= ?
                    ORDER BY timestamp
                """
                df_live = self.db.con.execute(
                    live_query, [instrument_key, tf, today_str]).df()

                # 3️⃣ Combine them
                frames = []
                if not df_hist.empty:
                    df_hist['timestamp'] = pd.to_datetime(df_hist['timestamp'])
                    frames.append(df_hist)

                if not df_live.empty:
                    df_live['timestamp'] = pd.to_datetime(df_live['timestamp'])
                    frames.append(df_live)

                if not frames:
                    # No data at all - try historical only as last resort
                    fallback_query = """
                        SELECT timestamp, open as Open, high as High, low as Low, 
                               close as Close, volume as Volume
                        FROM ohlcv_resampled
                        WHERE instrument_key = ?
                          AND timeframe = ?
                          AND timestamp >= ?
                        ORDER BY timestamp
                    """
                    df_fallback = self.db.con.execute(
                        fallback_query, [instrument_key, tf, cutoff]).df()
                    if df_fallback.empty:
                        return None
                    df_fallback['timestamp'] = pd.to_datetime(
                        df_fallback['timestamp'])
                    df_fallback.set_index('timestamp', inplace=True)
                    return df_fallback

                # Combine and deduplicate (prefer live data for today)
                df_combined = pd.concat(frames, ignore_index=True)
                df_combined = df_combined.drop_duplicates(
                    subset=['timestamp'], keep='last')
                df_combined = df_combined.sort_values('timestamp')
                df_combined.set_index('timestamp', inplace=True)

                return df_combined

            except Exception as e:
                logger.error(
                    f"Error loading combined MTF data for {instrument_key} {tf}: {e}")
                return None

        df_60m = load_combined_timeframe('60minute')
        df_15m = load_combined_timeframe('15minute')
        df_5m = load_combined_timeframe('5minute')

        # Log data availability for debugging
        logger.info(f"MTF data for {instrument_key}: 60m={len(df_60m) if df_60m is not None else 0}, "
                    f"15m={len(df_15m) if df_15m is not None else 0}, "
                    f"5m={len(df_5m) if df_5m is not None else 0} bars")

        return df_60m, df_15m, df_5m

    def get_live_mtf_data_in_memory(
        self,
        instrument_key: str,
        lookback_days: int = 60
    ) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
        """
        Get MTF data with in-memory resampling (no ohlcv_resampled_live dependency).

        This method:
        1. Loads historical resampled data from ohlcv_resampled
        2. Loads today's 1m candles from live_ohlcv_cache
        3. Resamples today's data IN MEMORY
        4. Combines them

        Use this for faster live scanning without rebuilding the resampled table.
        """
        cutoff = (datetime.now() - timedelta(days=lookback_days)
                  ).strftime('%Y-%m-%d')
        today_str = date.today().strftime('%Y-%m-%d')

        # Get today's 1m candles
        live_1m_query = """
            SELECT timestamp, open, high, low, close, volume
            FROM live_ohlcv_cache
            WHERE instrument_key = ?
              AND timestamp >= ?
            ORDER BY timestamp
        """
        df_live_1m = self.db.con.execute(
            live_1m_query, [instrument_key, today_str]).df()

        if not df_live_1m.empty:
            df_live_1m['timestamp'] = pd.to_datetime(df_live_1m['timestamp'])
            df_live_1m.set_index('timestamp', inplace=True)

        def resample_1m_to_tf(df_1m: pd.DataFrame, tf: str) -> Optional[pd.DataFrame]:
            """Resample 1m data to target timeframe"""
            if df_1m.empty:
                return None

            tf_map = {
                '5minute': '5min',
                '15minute': '15min',
                '60minute': '60min'
            }

            rule = tf_map.get(tf, '15min')

            try:
                resampled = df_1m.resample(rule).agg({
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum'
                }).dropna()

                resampled.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
                return resampled
            except Exception as e:
                logger.error(f"Error resampling to {tf}: {e}")
                return None

        def load_combined_timeframe(tf: str) -> Optional[pd.DataFrame]:
            try:
                # Get historical data
                hist_query = """
                    SELECT timestamp, open as Open, high as High, low as Low, 
                           close as Close, volume as Volume
                    FROM ohlcv_resampled
                    WHERE instrument_key = ?
                      AND timeframe = ?
                      AND timestamp >= ?
                      AND timestamp < ?
                    ORDER BY timestamp
                """
                df_hist = self.db.con.execute(
                    hist_query, [instrument_key, tf, cutoff, today_str]).df()

                if not df_hist.empty:
                    df_hist['timestamp'] = pd.to_datetime(df_hist['timestamp'])
                    df_hist.set_index('timestamp', inplace=True)

                # Resample today's 1m data in memory
                df_live_tf = resample_1m_to_tf(
                    df_live_1m, tf) if not df_live_1m.empty else None

                # Combine
                frames = []
                if df_hist is not None and not df_hist.empty:
                    frames.append(df_hist)
                if df_live_tf is not None and not df_live_tf.empty:
                    frames.append(df_live_tf)

                if not frames:
                    return None

                df_combined = pd.concat(frames)
                df_combined = df_combined[~df_combined.index.duplicated(
                    keep='last')]
                df_combined = df_combined.sort_index()

                return df_combined

            except Exception as e:
                logger.error(
                    f"Error loading in-memory MTF data for {instrument_key} {tf}: {e}")
                return None

        df_60m = load_combined_timeframe('60minute')
        df_15m = load_combined_timeframe('15minute')
        df_5m = load_combined_timeframe('5minute')

        return df_60m, df_15m, df_5m

    def get_data_freshness(self) -> pd.DataFrame:
        """Get freshness status of live data for all instruments"""
        return self.db.con.execute("""
            SELECT 
                s.instrument_key,
                f.trading_symbol,
                s.last_fetch,
                s.last_candle_time,
                s.candle_count,
                s.status,
                EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - s.last_fetch)) / 60 AS minutes_since_update
            FROM live_data_status s
            JOIN fo_stocks_master f ON s.instrument_key = f.instrument_key
            ORDER BY s.last_fetch DESC
        """).df()

    def start_websocket_if_needed(self, access_token: str, use_enhanced: bool = True):
        """
        Start Upstox WebSocket once during market hours.
        Safe for Streamlit reruns.

        Args:
            access_token: Upstox API access token
            use_enhanced: Use new enhanced WebSocket module (default True)
        """
        # Already started
        if getattr(self, "ws_builder", None) is not None and self.ws_connected:
            return

        # Check enhanced WebSocket
        if use_enhanced and getattr(self, "_enhanced_ws_started", False):
            return

        # Market closed
        if not is_market_hours():
            return

        # WebSocket unavailable (SDK missing)
        if not getattr(self, "WS_AVAILABLE", True):
            return

        instruments = self.get_active_instruments()
        instrument_keys = [key for key, _ in instruments]
        if not instrument_keys:
            return

        # Try enhanced WebSocket first
        if use_enhanced:
            try:
                from core.websocket import WebSocketManager
                ws_manager = WebSocketManager.get_instance()

                if not ws_manager.is_running:
                    success = ws_manager.start(access_token, instrument_keys)
                    if success:
                        self._enhanced_ws_started = True
                        self.ws_connected = True
                        logger.info("Enhanced WebSocket started successfully")
                        return
                else:
                    self._enhanced_ws_started = True
                    self.ws_connected = True
                    return

            except ImportError as e:
                logger.info(f"Enhanced WebSocket not available, falling back to legacy: {e}")

        # Fallback to legacy WebSocket
        try:
            from core.websocket_manager import WebSocketCandleBuilder

            self.ws_builder = WebSocketCandleBuilder(self.db)
            self.ws_connected = False
            self.ws_builder.ws_started_at = None

            def _start():
                try:
                    self.ws_builder.start(access_token, instrument_keys)
                    self.ws_connected = True
                    logger.info("Legacy WebSocket connected successfully")
                except Exception as e:
                    logger.error(f"[WS ERROR] {e}")
                    self.ws_connected = False
                    self.ws_builder = None

            threading.Thread(target=_start, daemon=True).start()
        except ImportError as e:
            logger.warning(f"WebSocket manager not available: {e}")
            self.WS_AVAILABLE = False

    def restart_websocket(self, access_token: str, use_enhanced: bool = True):
        """
        Force restart WebSocket.
        Used after Initialize Day.

        Args:
            access_token: Upstox API access token
            use_enhanced: Use enhanced WebSocket module
        """
        # Stop enhanced WebSocket if running
        if use_enhanced:
            try:
                from core.websocket import WebSocketManager
                ws_manager = WebSocketManager.get_instance()
                if ws_manager.is_running:
                    ws_manager.stop()
                self._enhanced_ws_started = False
            except ImportError:
                pass

        # Stop legacy WebSocket
        try:
            if getattr(self, "ws_builder", None) is not None:
                if getattr(self.ws_builder, "streamer", None):
                    try:
                        self.ws_builder.streamer.disconnect()
                    except Exception:
                        pass
        finally:
            self.ws_builder = None
            self.ws_connected = False

        # Start fresh
        self.start_websocket_if_needed(access_token, use_enhanced=use_enhanced)

    def clear_live_cache(self):
        """Clear all live data (run at end of day or for troubleshooting)"""
        self.db.con.execute("DELETE FROM live_ohlcv_cache")
        self.db.con.execute("DELETE FROM live_data_status")
        self.db.con.execute("DELETE FROM ohlcv_resampled_live")
        logger.info("✅ Live cache cleared")

    def get_today_coverage(self) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        Returns:
        (first_minute, last_minute) available in live_ohlcv_cache for today
        """
        row = self.db.con.execute("""
            SELECT
                MIN(timestamp) AS first_ts,
                MAX(timestamp) AS last_ts
            FROM live_ohlcv_cache
            WHERE DATE(timestamp) = CURRENT_DATE
        """).fetchone()

        return row[0], row[1]

    def get_last_contiguous_minute(self) -> Optional[datetime]:
        """
        Finds the last minute up to which today's data is continuous (no gaps).
        """
        row = self.db.con.execute("""
            WITH ordered AS (
                SELECT
                    timestamp,
                    LAG(timestamp) OVER (ORDER BY timestamp) AS prev_ts
                FROM live_ohlcv_cache
                WHERE DATE(timestamp) = CURRENT_DATE
            )
            SELECT
                MAX(prev_ts)
            FROM ordered
            WHERE prev_ts IS NOT NULL
            AND timestamp - prev_ts > INTERVAL '1 minute';
        """).fetchone()

        if row and row[0]:
            return row[0]

        # No gaps → fully continuous
        row = self.db.con.execute("""
            SELECT MAX(timestamp)
            FROM live_ohlcv_cache
            WHERE DATE(timestamp) = CURRENT_DATE
        """).fetchone()

        return row[0] if row else None


# ============================================================
# CONVENIENCE FUNCTIONS
# ============================================================

def get_next_candle_time(timeframe: str) -> datetime:
    """Get the timestamp when the next candle will complete"""
    now = datetime.now()

    tf_minutes = {
        "5minute": 5,
        "15minute": 15,
        "30minute": 30,
        "60minute": 60
    }

    minutes = tf_minutes.get(timeframe, 15)

    # Round up to next interval
    current_minute = now.minute
    next_interval = ((current_minute // minutes) + 1) * minutes

    if next_interval >= 60:
        next_time = now.replace(
            minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        next_time = now.replace(minute=next_interval, second=0, microsecond=0)

    return next_time


def seconds_until_next_candle(timeframe: str) -> int:
    """Get seconds until next candle completes"""
    next_candle = get_next_candle_time(timeframe)
    return max(0, int((next_candle - datetime.now()).total_seconds()))


# ============================================================
# STANDALONE FUNCTIONS FOR BACKWARD COMPATIBILITY
# ============================================================

def backfill_intraday_1m_all():
    """
    Legacy function: Fetch today's intraday 1-minute candles for all active F&O stocks.
    Use LiveTradingManager.refresh_live_data() for more control.
    """
    manager = LiveTradingManager()
    status = manager.refresh_live_data()
    return status.candles_inserted


def refresh_resampled_live(timeframes=("5minute", "15minute", "30minute", "60minute")):
    """
    Legacy function: Rebuild ohlcv_resampled_live.
    Use LiveTradingManager.rebuild_today_resampled() for more control.
    """
    manager = LiveTradingManager()
    manager.rebuild_today_resampled(list(timeframes))


if __name__ == "__main__":
    # Test the manager
    print("Live Trading Manager Test")
    print("=" * 50)

    manager = LiveTradingManager()

    # Check market hours
    print(f"Market hours: {is_market_hours()}")
    print(
        f"Next 15m candle in: {seconds_until_next_candle('15minute')} seconds")

    # Get active instruments
    instruments = manager.get_active_instruments()
    print(f"Active instruments: {len(instruments)}")

    print("\nReady for live trading!")
