"""
Fetch intermarket data for HMM Regime Strategy.
Fetches daily candles for: Nifty 50, Bank Nifty, India VIX.
Also fetches 1m Bank Nifty candles for intraday ratio computation.

Usage:
  python scripts/fetch_intermarket_data.py --from 2023-01-01 --to 2026-02-16
  python scripts/fetch_intermarket_data.py --from 2023-01-01 --to 2026-02-16 --include-1m
"""
import argparse
import sys
import os
import time
from datetime import datetime, date, timedelta
from collections import defaultdict
import threading
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, ROOT)

from core.api.upstox_client import UpstoxClient
from core.database.manager import DatabaseManager
from core.database import schema
from core.logging import setup_logger
from core.database.utils.symbol_utils import get_exchange_from_key

logger = setup_logger("intermarket_fetch")

# Instruments to fetch
INTERMARKET_INSTRUMENTS = {
    'NSE_INDEX|Nifty 50': 'Nifty 50',
    'NSE_INDEX|Nifty Bank': 'Bank Nifty',
    'NSE_INDEX|India VIX': 'India VIX',
}

# Rate limiter (same pattern as fetch_upstox_historical.py)
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

limiter = RateLimiter(max_calls=8, period=1.0)


def get_date_chunks(from_date: str, to_date: str, unit: str) -> list:
    """Split date range into chunks compatible with Upstox API limits."""
    from_dt = datetime.strptime(from_date, '%Y-%m-%d')
    to_dt = datetime.strptime(to_date, '%Y-%m-%d')
    chunks = []
    if unit == 'minutes':
        max_days = 29
    elif unit == 'hours':
        max_days = 89
    else:
        max_days = 3650

    current_to = to_dt
    while current_to >= from_dt:
        current_from = max(from_dt, current_to - timedelta(days=max_days))
        chunks.append((current_from.strftime('%Y-%m-%d'), current_to.strftime('%Y-%m-%d')))
        current_to = current_from - timedelta(days=1)
    return chunks


def fetch_instrument(client, instrument_key, unit, interval, from_date, to_date):
    """Fetch all chunks for a single instrument."""
    chunks = get_date_chunks(from_date, to_date, unit)
    all_candles = []

    for start_chunk, end_chunk in chunks:
        limiter.wait()
        try:
            candles = client.fetch_historical_candles_v3(
                instrument_key=instrument_key,
                unit=unit,
                interval=interval,
                to_date=end_chunk,
                from_date=start_chunk
            )
            if candles:
                all_candles.extend(candles)
        except Exception as e:
            logger.error(f"[{instrument_key}] Failed chunk {start_chunk} to {end_chunk}: {e}")

    return instrument_key, all_candles


def _batch_insert_rows(conn, rows):
    """DuckDB vectorized insertion via Pandas."""
    if not rows:
        return
    df = pd.DataFrame(rows, columns=[
        'symbol', 'timeframe', 'timestamp', 'open', 'high', 'low', 'close', 'volume'
    ])
    conn.execute("""
        INSERT INTO candles
        (symbol, timeframe, timestamp, open, high, low, close, volume, is_synthetic)
        SELECT symbol, timeframe, timestamp, open, high, low, close, volume, FALSE
        FROM df
        ON CONFLICT (symbol, timeframe, timestamp) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high,
            low = EXCLUDED.low, close = EXCLUDED.close,
            volume = EXCLUDED.volume, is_synthetic = FALSE
    """)


def insert_candles(db_manager, symbol_results, timeframe):
    """Group results by date and write to DuckDB."""
    historical_groups = defaultdict(list)
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
            if d < today:
                historical_groups[(exchange, d)].append(row)
            total_count += 1

    for (exchange, d), rows in historical_groups.items():
        try:
            with db_manager.historical_writer(exchange, 'candles', timeframe, d) as conn:
                conn.execute(schema.MARKET_CANDLES_SCHEMA)
                _batch_insert_rows(conn, rows)
        except Exception as e:
            logger.error(f"Error writing {d}: {e}")

    return total_count


def main():
    parser = argparse.ArgumentParser(description='Fetch intermarket data for HMM Regime Strategy')
    parser.add_argument('--from', dest='from_date', required=True, help='Start date YYYY-MM-DD')
    parser.add_argument('--to', dest='to_date', required=True, help='End date YYYY-MM-DD')
    parser.add_argument('--include-1m', action='store_true', help='Also fetch 1m candles (slow)')
    args = parser.parse_args()

    try:
        from core.auth.credentials import credentials
        access_token = credentials.get('access_token')
        if not access_token:
            logger.error("No Upstox access token found")
            sys.exit(1)

        db_manager = DatabaseManager(os.path.join(ROOT, 'data'))
        client = UpstoxClient(access_token)

        # Step 1: Fetch daily candles for all intermarket instruments
        logger.info("=" * 60)
        logger.info("FETCHING DAILY CANDLES")
        logger.info("=" * 60)

        daily_results = []
        for key, name in INTERMARKET_INSTRUMENTS.items():
            logger.info(f"Fetching daily {name} ({key})...")
            symbol, candles = fetch_instrument(client, key, 'days', 1, args.from_date, args.to_date)
            daily_results.append((symbol, candles))
            logger.info(f"  {name}: {len(candles)} daily candles")

        logger.info("Writing daily candles to DB...")
        daily_count = insert_candles(db_manager, daily_results, '1d')
        logger.info(f"Daily candles written: {daily_count}")

        # Step 2: Optionally fetch 1m candles for Bank Nifty
        if args.include_1m:
            logger.info("=" * 60)
            logger.info("FETCHING 1M CANDLES (Bank Nifty)")
            logger.info("=" * 60)

            key = 'NSE_INDEX|Nifty Bank'
            logger.info(f"Fetching 1m Bank Nifty...")
            symbol, candles = fetch_instrument(client, key, 'minutes', 1, args.from_date, args.to_date)
            logger.info(f"  Bank Nifty 1m: {len(candles)} candles")

            if candles:
                logger.info("Writing 1m candles to DB...")
                minute_count = insert_candles(db_manager, [(symbol, candles)], '1m')
                logger.info(f"1m candles written: {minute_count}")

        # Step 3: Summary
        logger.info("=" * 60)
        logger.info("FETCH COMPLETE")
        logger.info("=" * 60)
        for symbol, candles in daily_results:
            name = INTERMARKET_INSTRUMENTS.get(symbol, symbol)
            if candles:
                dates = [c['timestamp'].date() if hasattr(c['timestamp'], 'date') else c['timestamp'] for c in candles]
                logger.info(f"  {name}: {len(candles)} days ({min(dates)} to {max(dates)})")
            else:
                logger.info(f"  {name}: NO DATA")

    except Exception as e:
        logger.error(f"Critical error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
