import logging
import time
import duckdb
from datetime import datetime, date
from typing import List, Optional, Dict

from core.database.manager import DatabaseManager
from core.messaging.zmq_handler import ZmqPublisher

logger = logging.getLogger(__name__)

class DBTickAggregator:
    """
    Deterministic aggregator that reads raw ticks from today's live buffer
    and produces OHLCV bars in the same buffer.
    """

    def __init__(self, db_manager: DatabaseManager, zmq_publisher: Optional[ZmqPublisher] = None):
        self.db_manager = db_manager
        self.zmq_publisher = zmq_publisher

    def aggregate_outstanding_ticks(self, symbols: List[str]):
        """
        Scans 'ticks' for unaggregated minutes and creates bars in 'candles'.
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Open connections once for the entire batch to improve performance and reduce connection churn
                with self.db_manager.live_buffer_writer() as conns:
                    ticks_conn = conns['ticks']
                    candles_conn = conns['candles']
                    for symbol in symbols:
                        try:
                            self._aggregate_symbol(symbol, ticks_conn, candles_conn)
                        except Exception as e:
                            logger.error(f"Aggregation failed for {symbol}: {e}")
                return  # Success, exit retry loop
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Failed to acquire live buffer for aggregation batch (attempt {attempt+1}/{max_retries}): {e}")
                    time.sleep(0.3 * (attempt + 1))  # Exponential backoff
                else:
                    logger.error(f"Failed to acquire live buffer for aggregation batch after {max_retries} attempts: {e}")

    def _aggregate_symbol(self, symbol: str, ticks_conn: duckdb.DuckDBPyConnection, candles_conn: duckdb.DuckDBPyConnection):
        # 1. Find the last aggregated bar timestamp
        last_bar_ts = self._get_last_bar_timestamp(symbol, candles_conn)

        # 2. Start from the last bar OR far in the past
        start_ts = last_bar_ts if last_bar_ts else datetime(2000, 1, 1)

        # 3. Aggregate using DuckDB SQL
        query = """
            SELECT
                date_trunc('minute', timestamp) as bar_ts,
                first(price ORDER BY timestamp ASC) as op,
                max(price) as hi,
                min(price) as lo,
                last(price ORDER BY timestamp ASC) as cl,
                sum(volume) as vol
            FROM ticks
            WHERE symbol = ? AND timestamp >= ?
            GROUP BY 1
            ORDER BY 1 ASC
        """

        try:
            results = ticks_conn.execute(query, [symbol, start_ts]).fetchall()

            for row in results:
                bar_ts, op, hi, lo, cl, vol = row

                # Skip if bar is current (incomplete) minute
                if bar_ts >= datetime.now().replace(second=0, microsecond=0):
                    continue

                if op is None or cl is None:
                    logger.warning(f"Skipping null aggregate for {symbol} at {bar_ts}")
                    continue

                # Write to candles_conn
                candles_conn.execute(
                    """
                    INSERT INTO candles
                    (symbol, timeframe, timestamp, open, high, low, close, volume, is_synthetic)
                    VALUES (?, '1m', ?, ?, ?, ?, ?, ?, FALSE)
                    ON CONFLICT (symbol, timeframe, timestamp) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume,
                        is_synthetic = FALSE
                    """,
                    [symbol, bar_ts, op, hi, lo, cl, int(vol)]
                )

                # Broadcast via ZMQ if publisher is available
                if self.zmq_publisher:
                    topic = f"market.candle.1m.{symbol}"
                    data = {
                        "symbol": symbol,
                        "timeframe": "1m",
                        "timestamp": bar_ts.isoformat(),
                        "open": float(op),
                        "high": float(hi),
                        "low": float(lo),
                        "close": float(cl),
                        "volume": int(vol)
                    }
                    self.zmq_publisher.publish(topic, "market_candle", data)

            if results and len(results) > 1:
                logger.debug(f"Aggregated {len(results)} bars for {symbol}.")
        except Exception as e:
            logger.error(f"Aggregation failed for {symbol}: {e}")

    def _get_last_bar_timestamp(self, symbol: str, candles_conn: duckdb.DuckDBPyConnection) -> Optional[datetime]:
        try:
            res = candles_conn.execute(
                "SELECT MAX(timestamp) FROM candles WHERE symbol = ? AND timeframe = '1m' AND is_synthetic = FALSE",
                [symbol]
            ).fetchone()
            return res[0] if res and res[0] else None
        except:
            return None
