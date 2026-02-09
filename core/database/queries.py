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

    def get_ohlcv(
        self,
        instrument_key: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        timeframe: str = "1m",
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Compatibility wrapper for get_candles."""
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
        limit: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Fetch candles across historical and live data.
        """
        today = date.today()
        # Defaults
        if not start and not limit:
            start = datetime.now() - timedelta(days=1)
        
        end = end or datetime.now()
        
        results = []

        # 1. Query today's live buffer (prefer latest if limit is set) with retry logic
        if end.date() >= today:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    with self.db.live_buffer_reader() as conns:
                        if 'candles' in conns:
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

                            df = conns['candles'].execute(query, params).df()

                            if not df.empty:
                                results.append(df)
                    break  # Success, exit retry loop
                except Exception as e:
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(0.1 * (attempt + 1))  # Quick retry for reads
                    else:
                        logger.error(f"Error reading live buffer for {symbol} after {max_retries} attempts: {e}")

        # 2. Query historical data if more bars needed
        if not limit or (len(results) > 0 and len(results[0]) < limit) or not results:
            current_date = min(end.date(), today)
            if start:
                earliest_date = start.date()
            else:
                earliest_date = current_date - timedelta(days=5) # Max 5 days back if no start/limit

            # Iterate backwards from today-1
            current_date -= timedelta(days=1)
            while current_date >= earliest_date:
                if limit and sum(len(r) for r in results) >= limit:
                    break
                    
                try:
                    with self.db.historical_reader(exchange, 'candles', timeframe, current_date) as conn:
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
                            
                        df = conn.execute(query, params).df()
                        
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
            combined_df = combined_df.drop_duplicates(
                subset=['symbol', 'timestamp']
            ).sort_values('timestamp')

        if limit:
            combined_df = combined_df.tail(limit)

        return combined_df

    def get_latest_bar(self, symbol: str, exchange: str = 'nse', timeframe: str = '1m') -> Optional[Dict[str, Any]]:
        """Get the most recent bar."""
        # Try live buffer first
        try:
            with self.db.live_buffer_reader() as conns:
                if 'candles' in conns:
                    row = conns['candles'].execute("""
                        SELECT * FROM candles WHERE symbol = ? AND timeframe = ? 
                        ORDER BY timestamp DESC LIMIT 1
                    """, [symbol, timeframe]).fetchone()
                    if row:
                        return self._row_to_dict(row, conns['candles'].description)
        except:
            pass
            
        # Try historical (today and then backward)
        curr = date.today()
        for _ in range(5): # Check last 5 days
            try:
                with self.db.historical_reader(exchange, 'candles', timeframe, curr) as conn:
                    row = conn.execute("SELECT * FROM candles WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1", [symbol]).fetchone()
                    if row:
                        return self._row_to_dict(row, conn.description)
            except:
                pass
            curr -= timedelta(days=1)
            
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
        except:
            pass
        return None

    def get_insights(self, symbol: str, start_time: datetime, end_time: datetime, limit: int = 100000) -> List[Dict[str, Any]]:
        query = "SELECT * FROM confluence_insights WHERE symbol = ? AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC LIMIT ?"
        try:
            with self.db.signals_reader() as conn:
                rows = conn.execute(query, [symbol, start_time, end_time, limit]).fetchall()
                cols = [d[0] for d in conn.description]
                return [dict(zip(cols, r)) for r in rows]
        except:
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
        except:
            pass
        return None
