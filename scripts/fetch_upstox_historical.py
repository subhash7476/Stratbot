import argparse
import json
import sys
import os
import time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import pandas as pd

# Add the root directory to the path so we can import core modules
ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, ROOT)

from core.api.upstox_client import UpstoxClient
from core.database.manager import DatabaseManager
from core.database import schema
from core.logging import setup_logger
from core.database.utils.symbol_utils import get_exchange_from_key

logger = setup_logger("historical_fetch")

# Rate limiting helper
class RateLimiter:
    def __init__(self, max_calls, period):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            self.calls = [c for c in self.calls if c > now - self.period]
            if len(self.calls) >= self.max_calls:
                sleep_time = self.calls[0] + self.period - now
                if sleep_time > 0:
                    time.sleep(sleep_time)
            self.calls.append(time.time())

# Upstox V3 API rate limit is 10 requests per second
limiter = RateLimiter(max_calls=8, period=1.0) 

def validate_parameters(instrument_key: str, unit: str, interval: int, from_date: str, to_date: str):
    """Validate input parameters according to Upstox API rules."""
    if instrument_key != 'ALL' and '|' not in instrument_key:
        raise ValueError(f"Instrument key must be in format 'EXCHANGE_SEGMENT|ISIN'. Got: '{instrument_key}'.")

    if unit.lower() == 'minutes':
        if not (1 <= interval <= 300):
            raise ValueError("For minutes unit, interval must be between 1 and 300")
    elif unit.lower() == 'hours':
        if not (1 <= interval <= 5):
            raise ValueError("For hours unit, interval must be between 1 and 5")
    elif unit.lower() in ['days', 'weeks', 'months']:
        if interval != 1:
            raise ValueError(f"For {unit} unit, interval must be 1")
    else:
        raise ValueError("Unit must be one of: minutes, hours, days, weeks, months")
    
    try:
        datetime.strptime(from_date, '%Y-%m-%d')
        datetime.strptime(to_date, '%Y-%m-%d')
    except ValueError:
        raise ValueError("Dates must be in YYYY-MM-DD format")
    
    from_dt = datetime.strptime(from_date, '%Y-%m-%d')
    to_dt = datetime.strptime(to_date, '%Y-%m-%d')
    
    if from_dt > to_dt:
        raise ValueError("From date must be before or equal to to date")

def get_date_chunks(from_date: str, to_date: str, unit: str) -> list:
    """Split date range into chunks compatible with Upstox API limits."""
    from_dt = datetime.strptime(from_date, '%Y-%m-%d')
    to_dt = datetime.strptime(to_date, '%Y-%m-%d')
    
    chunks = []
    if unit.lower() == 'minutes':
        max_days = 29
    elif unit.lower() == 'hours':
        max_days = 89
    else:
        max_days = 3650
        
    current_to = to_dt
    while current_to >= from_dt:
        current_from = max(from_dt, current_to - timedelta(days=max_days))
        chunks.append((current_from.strftime('%Y-%m-%d'), current_to.strftime('%Y-%m-%d')))
        current_to = current_from - timedelta(days=1)
        
    return chunks

def fetch_symbol_data(client, symbol, unit, interval, from_date, to_date):
    """Fetch all chunks for a single symbol."""
    chunks = get_date_chunks(from_date, to_date, unit)
    all_candles = []
    
    for start_chunk, end_chunk in chunks:
        limiter.wait()
        try:
            candles = client.fetch_historical_candles_v3(
                instrument_key=symbol,
                unit=unit,
                interval=interval,
                to_date=end_chunk,
                from_date=start_chunk
            )
            if candles:
                all_candles.extend(candles)
        except Exception as e:
            logger.error(f"[{symbol}] Failed to fetch chunk {start_chunk} to {end_chunk}: {e}")
            
    return symbol, all_candles

def insert_all_candles_to_db(db_manager: DatabaseManager, symbol_results: list, timeframe: str):
    """Group all results by date and write efficiently."""
    historical_groups = defaultdict(list)
    live_groups = defaultdict(list)
    
    today = date.today()
    total_count = 0
    
    for symbol, candles in symbol_results:
        if not candles:
            continue
            
        exchange = get_exchange_from_key(symbol)
        for c in candles:
            dt = c['timestamp']
            if isinstance(dt, str):
                dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
            
            ts_obj = dt.replace(tzinfo=None) if dt.tzinfo else dt
            d = dt.date()
            
            row = (symbol, timeframe, ts_obj, c['open'], c['high'], c['low'], c['close'], int(c['volume']))
            
            if d >= today:
                live_groups[d].append(row)
            else:
                historical_groups[(exchange, d)].append(row)
            total_count += 1

    # Sequential writes to avoid lock contention
    # 1. Write historical data
    for (exchange, d), rows in historical_groups.items():
        try:
            # Each file opened and locked only once
            with db_manager.historical_writer(exchange, 'candles', timeframe, d) as conn:
                conn.execute(schema.MARKET_CANDLES_SCHEMA)
                _batch_insert_rows(conn, rows)
                logger.info(f"  [DB] Written {len(rows)} rows to {d} ({exchange})")
        except Exception as e:
            logger.error(f"Error writing historical data for {d}: {e}")

    # 2. Write live buffer data
    if live_groups:
        try:
            with db_manager.live_buffer_writer() as conns:
                conn = conns['candles']
                conn.execute(schema.MARKET_CANDLES_SCHEMA)
                for d, rows in live_groups.items():
                    _batch_insert_rows(conn, rows)
                    logger.info(f"  [DB] Written {len(rows)} rows to live buffer ({d})")
        except Exception as e:
            logger.error(f"Error writing live buffer data: {e}")

    return total_count

import pandas as pd

def _batch_insert_rows(conn, rows):
    """Uses DuckDB's vectorized insertion via Pandas for maximum speed."""
    if not rows:
        return
    
    # Create DataFrame for vectorized transfer to DuckDB
    df = pd.DataFrame(rows, columns=[
        'symbol', 'timeframe', 'timestamp', 'open', 'high', 'low', 'close', 'volume'
    ])
    
    # DuckDB's Python driver allows querying Pandas DataFrames directly in SQL.
    # This is significantly faster than executemany() for large batches.
    conn.execute("""
        INSERT INTO candles 
        (symbol, timeframe, timestamp, open, high, low, close, volume, is_synthetic)
        SELECT symbol, timeframe, timestamp, open, high, low, close, volume, FALSE 
        FROM df
        ON CONFLICT (symbol, timeframe, timestamp) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            is_synthetic = FALSE
    """)

def main():
    parser = argparse.ArgumentParser(description='Fetch historical candle data from Upstox V3 API')
    parser.add_argument('--instrument_key', required=True, help='Instrument key or "ALL"')
    parser.add_argument('--unit', required=True, choices=['minutes', 'hours', 'days', 'weeks', 'months'])
    parser.add_argument('--interval', type=int, required=True)
    parser.add_argument('--from', dest='from_date', required=True)
    parser.add_argument('--to', dest='to_date', required=True)
    parser.add_argument('--workers', type=int, default=5, help='Number of parallel workers')
    
    args = parser.parse_args()
    
    tf_map = {
        ('minutes', 1): '1m', ('minutes', 5): '5m', ('minutes', 15): '15m',
        ('minutes', 30): '30m', ('minutes', 60): '1h', ('days', 1): '1d'
    }
    timeframe = tf_map.get((args.unit.lower(), args.interval), f"{args.interval}{args.unit[0].lower()}")

    try:
        from core.auth.credentials import credentials
        access_token = credentials.get('access_token')
        if not access_token:
            logger.error("Error: Upstox access token not found.")
            sys.exit(1)
            
        db_manager = DatabaseManager(os.path.join(ROOT, 'data'))
        client = UpstoxClient(access_token)
        
        symbols_to_fetch = []
        if args.instrument_key == 'ALL':
            with db_manager.config_reader() as conn:
                rows = conn.execute("SELECT instrument_key FROM fo_stocks WHERE is_active = 1").fetchall()
                symbols_to_fetch = [row[0] for row in rows]
        elif ',' in args.instrument_key:
            symbols_to_fetch = [s.strip() for s in args.instrument_key.split(',') if s.strip()]
        else:
            symbols_to_fetch = [args.instrument_key]

        logger.info(f"Starting fetch for {len(symbols_to_fetch)} symbols using {args.workers} workers...")
        
        start_total = time.time()
        
        all_results = []
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_symbol = {
                executor.submit(fetch_symbol_data, client, s, args.unit, args.interval, args.from_date, args.to_date): s 
                for s in symbols_to_fetch
            }
            
            for future in as_completed(future_to_symbol):
                symbol, candles = future.result()
                if candles:
                    all_results.append((symbol, candles))
                    logger.info(f"[{symbol}] Fetched {len(candles)} candles")
                else:
                    logger.warning(f"[{symbol}] No data found")

        if all_results:
            logger.info("Fetching complete. Starting optimized database write...")
            total_inserted = insert_all_candles_to_db(db_manager, all_results, timeframe)
            logger.info(f"DATABASE WRITE COMPLETED. Total rows processed: {total_inserted}")

        logger.info(f"TOTAL PROCESS COMPLETED in {time.time() - start_total:.2f} seconds")
        
    except Exception as e:
        logger.error(f"Critical Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
