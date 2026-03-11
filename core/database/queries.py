import pandas as pd
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
import logging

from .manager import DatabaseManager

logger = logging.getLogger(__name__)


class MarketDataQuery:
    """
    Unified query interface for historical + live data.
    Automatically handles UNION of historical (daily files) and today's buffer.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    @staticmethod
    def _has_column(conn, table: str, column: str) -> bool:
        """Return True if the given table has the requested column."""
        try:
            rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
            names = {str(r[1]).lower() for r in rows if len(r) > 1}
            return column.lower() in names
        except Exception:
            return False

    @staticmethod
    def _is_missing_instrument_key_error(exc: Exception) -> bool:
        """Detect runtime SQL errors caused by missing instrument_key in a candles table."""
        msg = str(exc).lower()
        return "instrument_key" in msg and "not found" in msg

    def get_ohlcv(
        self,
        instrument_key: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        timeframe: str = "1m",
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Compatibility wrapper for get_candles."""
        if getattr(self.db, "_legacy_db_path", None) is not None:
            with self.db.read() as conn:
                has_ikey = self._has_column(conn, "candles", "instrument_key")
                if has_ikey:
                    query = """
                        SELECT
                            COALESCE(NULLIF(symbol, ''), instrument_key) AS symbol,
                            timestamp, open, high, low, close, volume
                        FROM candles
                        WHERE (symbol = ? OR instrument_key = ?)
                    """
                    params: List[Any] = [instrument_key, instrument_key]
                else:
                    query = """
                        SELECT symbol, timestamp, open, high, low, close, volume
                        FROM candles
                        WHERE symbol = ?
                    """
                    params = [instrument_key]

                if start_time:
                    query += " AND timestamp >= ?"
                    params.append(start_time)
                if end_time:
                    query += " AND timestamp <= ?"
                    params.append(end_time)
                query += " ORDER BY timestamp ASC"
                if limit:
                    query += f" LIMIT {int(limit)}"
                return conn.execute(query, params).df()

        from .utils.symbol_utils import get_exchange_from_key

        exchange = get_exchange_from_key(instrument_key)
        return self.get_candles(instrument_key, exchange, timeframe, start_time, end_time, limit=limit)

    def get_candles(
        self,
        symbol: str,
        exchange: str,
        timeframe: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch candles across historical and live data.
        """
        today = date.today()
        if not start and not limit:
            start = datetime.now() - timedelta(days=1)

        end = end or datetime.now()
        results = []

        if end.date() >= today:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    with self.db.live_buffer_reader() as conns:
                        if "candles" in conns:
                            conn = conns["candles"]
                            has_ikey = self._has_column(conn, "candles", "instrument_key")
                            if has_ikey:
                                query = """
                                    SELECT * FROM candles
                                    WHERE (symbol = ? OR instrument_key = ?) AND timeframe = ?
                                """
                                params = [symbol, symbol, timeframe]
                            else:
                                query = """
                                    SELECT * FROM candles
                                    WHERE symbol = ? AND timeframe = ?
                                """
                                params = [symbol, timeframe]

                            if start:
                                query += " AND timestamp >= ?"
                                params.append(start)

                            query += " AND timestamp < ?"
                            params.append(end)

                            query += " ORDER BY timestamp DESC"
                            if limit:
                                query += f" LIMIT {limit}"

                            try:
                                df = conn.execute(query, params).df()
                            except Exception as exc:
                                if has_ikey and self._is_missing_instrument_key_error(exc):
                                    # Defensive fallback for mixed historical schemas.
                                    fallback_query = """
                                        SELECT * FROM candles
                                        WHERE symbol = ? AND timeframe = ?
                                    """
                                    fallback_params: List[Any] = [symbol, timeframe]
                                    if start:
                                        fallback_query += " AND timestamp >= ?"
                                        fallback_params.append(start)
                                    fallback_query += " AND timestamp < ?"
                                    fallback_params.append(end)
                                    fallback_query += " ORDER BY timestamp DESC"
                                    if limit:
                                        fallback_query += f" LIMIT {limit}"
                                    df = conn.execute(fallback_query, fallback_params).df()
                                else:
                                    raise
                            if not df.empty:
                                results.append(df)
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        import time

                        time.sleep(0.1 * (attempt + 1))
                    else:
                        logger.error(f"Error reading live buffer for {symbol} after {max_retries} attempts: {e}")

        if not limit or (len(results) > 0 and len(results[0]) < limit) or not results:
            current_date = min(end.date(), today)
            earliest_date = start.date() if start else current_date - timedelta(days=5)
            current_date -= timedelta(days=1)

            while current_date >= earliest_date:
                if limit and sum(len(r) for r in results) >= limit:
                    break

                try:
                    with self.db.historical_reader(exchange, "candles", timeframe, current_date) as conn:
                        has_ikey = self._has_column(conn, "candles", "instrument_key")
                        if has_ikey:
                            query = "SELECT * FROM candles WHERE (symbol = ? OR instrument_key = ?)"
                            params = [symbol, symbol]
                        else:
                            query = "SELECT * FROM candles WHERE symbol = ?"
                            params = [symbol]

                        if start:
                            query += " AND timestamp >= ?"
                            params.append(start)

                        query += " AND timestamp < ?"
                        params.append(end)
                        query += " ORDER BY timestamp DESC"

                        if limit:
                            remaining = limit - sum(len(r) for r in results)
                            query += f" LIMIT {remaining}"

                        try:
                            df = conn.execute(query, params).df()
                        except Exception as exc:
                            if has_ikey and self._is_missing_instrument_key_error(exc):
                                # Defensive fallback for per-day files without instrument_key.
                                fallback_query = "SELECT * FROM candles WHERE symbol = ?"
                                fallback_params = [symbol]
                                if start:
                                    fallback_query += " AND timestamp >= ?"
                                    fallback_params.append(start)
                                fallback_query += " AND timestamp < ?"
                                fallback_params.append(end)
                                fallback_query += " ORDER BY timestamp DESC"
                                if limit:
                                    remaining = limit - sum(len(r) for r in results)
                                    fallback_query += f" LIMIT {remaining}"
                                df = conn.execute(fallback_query, fallback_params).df()
                            else:
                                raise
                        if not df.empty:
                            results.append(df)
                except FileNotFoundError:
                    pass
                except Exception as e:
                    logger.error(f"Error reading historical data for {symbol} on {current_date}: {e}")

                current_date -= timedelta(days=1)

        if not results:
            return pd.DataFrame()

        combined_df = pd.concat(results, ignore_index=True)
        if not combined_df.empty:
            dedupe_col = "symbol" if "symbol" in combined_df.columns else combined_df.columns[0]
            combined_df = combined_df.drop_duplicates(subset=[dedupe_col, "timestamp"]).sort_values("timestamp")

        if limit:
            combined_df = combined_df.tail(limit)

        return combined_df

    def get_latest_bar(self, symbol: str, exchange: str = "nse", timeframe: str = "1m") -> Optional[Dict[str, Any]]:
        """Get the most recent bar."""
        try:
            with self.db.live_buffer_reader() as conns:
                if "candles" in conns:
                    conn = conns["candles"]
                    has_ikey = self._has_column(conn, "candles", "instrument_key")
                    if has_ikey:
                        try:
                            row = conn.execute(
                                """
                                SELECT * FROM candles
                                WHERE (symbol = ? OR instrument_key = ?) AND timeframe = ?
                                ORDER BY timestamp DESC LIMIT 1
                                """,
                                [symbol, symbol, timeframe],
                            ).fetchone()
                        except Exception as exc:
                            if self._is_missing_instrument_key_error(exc):
                                row = conn.execute(
                                    """
                                    SELECT * FROM candles
                                    WHERE symbol = ? AND timeframe = ?
                                    ORDER BY timestamp DESC LIMIT 1
                                    """,
                                    [symbol, timeframe],
                                ).fetchone()
                            else:
                                raise
                    else:
                        row = conn.execute(
                            """
                            SELECT * FROM candles
                            WHERE symbol = ? AND timeframe = ?
                            ORDER BY timestamp DESC LIMIT 1
                            """,
                            [symbol, timeframe],
                        ).fetchone()
                    if row:
                        return self._row_to_dict(row, conn.description)
        except Exception:
            pass

        curr = date.today()
        for _ in range(5):
            try:
                with self.db.historical_reader(exchange, "candles", timeframe, curr) as conn:
                    has_ikey = self._has_column(conn, "candles", "instrument_key")
                    if has_ikey:
                        try:
                            row = conn.execute(
                                "SELECT * FROM candles WHERE (symbol = ? OR instrument_key = ?) ORDER BY timestamp DESC LIMIT 1",
                                [symbol, symbol],
                            ).fetchone()
                        except Exception as exc:
                            if self._is_missing_instrument_key_error(exc):
                                row = conn.execute(
                                    "SELECT * FROM candles WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
                                    [symbol],
                                ).fetchone()
                            else:
                                raise
                    else:
                        row = conn.execute(
                            "SELECT * FROM candles WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
                            [symbol],
                        ).fetchone()
                    if row:
                        return self._row_to_dict(row, conn.description)
            except Exception:
                pass
            curr -= timedelta(days=1)

        if getattr(self.db, "_legacy_db_path", None) is not None:
            try:
                with self.db.read() as conn:
                    has_ikey = self._has_column(conn, "candles", "instrument_key")
                    if has_ikey:
                        try:
                            row = conn.execute(
                                """
                                SELECT COALESCE(NULLIF(symbol, ''), instrument_key) AS symbol,
                                       timestamp, open, high, low, close, volume
                                FROM candles
                                WHERE (symbol = ? OR instrument_key = ?)
                                ORDER BY timestamp DESC LIMIT 1
                                """,
                                [symbol, symbol],
                            ).fetchone()
                        except Exception as exc:
                            if self._is_missing_instrument_key_error(exc):
                                row = conn.execute(
                                    """
                                    SELECT symbol, timestamp, open, high, low, close, volume
                                    FROM candles
                                    WHERE symbol = ?
                                    ORDER BY timestamp DESC LIMIT 1
                                    """,
                                    [symbol],
                                ).fetchone()
                            else:
                                raise
                    else:
                        row = conn.execute(
                            """
                            SELECT symbol, timestamp, open, high, low, close, volume
                            FROM candles
                            WHERE symbol = ?
                            ORDER BY timestamp DESC LIMIT 1
                            """,
                            [symbol],
                        ).fetchone()
                    if row:
                        cols = [d[0] for d in conn.description]
                        return dict(zip(cols, row))
            except Exception:
                pass

        return None

    def _row_to_dict(self, row, description) -> Dict[str, Any]:
        cols = [d[0] for d in description]
        return dict(zip(cols, row))


class TradingQuery:
    """Read-only queries for trades and signals in SQLite."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def signal_exists(self, signal_id: str) -> bool:
        with self.db.trading_reader() as conn:
            row = conn.execute("SELECT 1 FROM trades WHERE signal_id = ?", [signal_id]).fetchone()
            return row is not None


class AnalyticsQuery:
    """Read-only queries for confluence insights in SQLite."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def get_latest_insight(self, symbol: str, as_of: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM confluence_insights WHERE symbol = ?"
        params = [symbol]
        if as_of is not None:
            query += " AND timestamp <= ?"
            params.append(as_of)
        query += " ORDER BY timestamp DESC LIMIT 1"

        try:
            with self.db.signals_reader() as conn:
                row = conn.execute(query, params).fetchone()
                if row:
                    cols = [d[0] for d in conn.description]
                    return dict(zip(cols, row))
        except Exception:
            pass
        return None

    def get_insights(self, symbol: str, start_time: datetime, end_time: datetime, limit: int = 100000) -> List[Dict[str, Any]]:
        query = "SELECT * FROM confluence_insights WHERE symbol = ? AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC LIMIT ?"
        try:
            with self.db.signals_reader() as conn:
                rows = conn.execute(query, [symbol, start_time, end_time, limit]).fetchall()
                cols = [d[0] for d in conn.description]
                return [dict(zip(cols, r)) for r in rows]
        except Exception:
            return []

    def get_market_regime(self, symbol: str, as_of: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM regime_insights WHERE symbol = ?"
        params = [symbol]
        if as_of is not None:
            query += " AND timestamp <= ?"
            params.append(as_of)
        query += " ORDER BY timestamp DESC LIMIT 1"

        try:
            with self.db.signals_reader() as conn:
                row = conn.execute(query, params).fetchone()
                if row:
                    cols = [d[0] for d in conn.description]
                    return dict(zip(cols, row))
        except Exception:
            pass
        return None






