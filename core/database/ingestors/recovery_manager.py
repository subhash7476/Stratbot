import logging
import time
from datetime import datetime, timedelta
from typing import List, Optional
import pytz

from core.api.upstox_client import UpstoxClient
from core.database.manager import DatabaseManager
from core.database.utils.market_hours import MarketHours

logger = logging.getLogger(__name__)

class RecoveryManager:
    """
    Handles data gap detection and automated backfilling into the live buffer.
    """
    
    def __init__(self, upstox_client: UpstoxClient, db_manager: DatabaseManager):
        self.client = upstox_client
        self.db = db_manager

    def run_recovery(self, symbols: List[str]):
        """Executes recovery for all symbols."""
        logger.info(f"Starting recovery for {len(symbols)} symbols...")
        for symbol in symbols:
            self._recover_symbol(symbol)

    def _recover_symbol(self, symbol: str):
        last_ts = self._get_last_bar_timestamp(symbol)
        now = MarketHours.get_ist_now()
        
        if not last_ts:
            logger.warning(f"No previous data for {symbol}. Skipping backfill.")
            return

        # Check for gap
        gap = now - last_ts
        if gap < timedelta(minutes=2):
            logger.info(f"No significant gap for {symbol} (Last: {last_ts}).")
            return

        logger.info(f"Gap detected for {symbol}: {gap}. Fetching missing data...")

        try:
            # Fetch OHLC bars from Upstox using V3 API
            # Use intraday endpoint for today's data, historical for past dates
            today = now.date()
            last_date = last_ts.date()

            if last_date == today:
                # Intraday data (today only)
                logger.debug(f"Fetching intraday data for {symbol}")
                candles = self.client.fetch_intraday_candles_v3(
                    instrument_key=symbol,
                    unit="minutes",
                    interval=1
                )
            else:
                # Historical data (past dates)
                logger.debug(f"Fetching historical data for {symbol}: {last_date} to {today}")
                candles = self.client.fetch_historical_candles_v3(
                    instrument_key=symbol,
                    unit="minutes",
                    interval=1,
                    from_date=last_date.strftime("%Y-%m-%d"),
                    to_date=today.strftime("%Y-%m-%d")
                )

            if candles:
                recovered_count = 0

                # Retry logic for DuckDB lock conflicts
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        with self.db.live_buffer_writer() as conns:
                            candles_conn = conns['candles']
                            for candle in candles:
                                # V3 API returns dict: {timestamp, open, high, low, close, volume, open_interest}
                                ts = candle['timestamp']

                                if ts > last_ts and ts < now.replace(second=0, microsecond=0):
                                    candles_conn.execute(
                                        """
                                        INSERT OR IGNORE INTO candles
                                        (symbol, timeframe, timestamp, open, high, low, close, volume, is_synthetic)
                                        VALUES (?, '1m', ?, ?, ?, ?, ?, ?, TRUE)
                                        """,
                                        [symbol, ts, candle['open'], candle['high'], candle['low'],
                                         candle['close'], int(candle['volume'])]
                                    )
                                    recovered_count += 1
                        logger.info(f"Recovered {recovered_count} bars for {symbol}.")
                        break  # Success, exit retry loop
                    except Exception as write_error:
                        if attempt < max_retries - 1:
                            logger.warning(f"Recovery write failed for {symbol} (attempt {attempt+1}/{max_retries}): {write_error}")
                            time.sleep(0.2 * (attempt + 1))  # Exponential backoff
                        else:
                            logger.error(f"Recovery failed for {symbol} after {max_retries} attempts: {write_error}")
        except Exception as e:
            logger.error(f"Recovery failed for {symbol}: {e}")

    def _get_last_bar_timestamp(self, symbol: str) -> Optional[datetime]:
        """Get last bar timestamp with retry logic for lock conflicts."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with self.db.live_buffer_reader() as conns:
                    if 'candles' not in conns: return None
                    res = conns['candles'].execute(
                        "SELECT MAX(timestamp) FROM candles WHERE symbol = ?",
                        [symbol]
                    ).fetchone()
                    ts = res[0] if res and res[0] else None
                    if ts and ts.tzinfo is None:
                        ts = pytz.timezone('Asia/Kolkata').localize(ts)
                    return ts
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.debug(f"Read failed for {symbol} timestamp (attempt {attempt+1}/{max_retries}): {e}")
                    time.sleep(0.1 * (attempt + 1))
                else:
                    logger.warning(f"Could not fetch last timestamp for {symbol} after {max_retries} attempts: {e}")
        return None
