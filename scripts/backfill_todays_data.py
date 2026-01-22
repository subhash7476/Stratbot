# scripts/backfill_todays_data.py
"""
Backfill Today's Intraday Data
Fetches today's historical intraday data (9:15 AM to now) using historical API
This fills gaps when fetch_todays_data.py wasn't running continuously
"""

from core.database import TradingDB
from core.config import get_access_token
import sys
import os
from pathlib import Path
from datetime import datetime, date, time as dt_time, timedelta
import pandas as pd
import requests
from typing import List

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


LIVE_CACHE_TABLE = "live_ohlcv_cache"


def backfill_todays_intraday(symbols: List[str], db: TradingDB):
    """
    Backfill today's data using historical API
    """
    token = get_access_token()
    if not token:
        print("❌ No access token")
        return

    today = date.today()

    # Get today's date range
    # Market: 9:15 AM to 3:30 PM
    market_start = datetime.combine(today, dt_time(9, 15))
    market_end = datetime.combine(today, dt_time(15, 30))

    # Use current time if market still open
    now = datetime.now()
    if now < market_end:
        end_time = now
    else:
        end_time = market_end

    print(f"\n📊 Backfilling intraday data:")
    print(f"  Date: {today}")
    print(f"  Time range: {market_start.time()} to {end_time.time()}")
    print(f"  Symbols: {len(symbols)}")

    success_count = 0
    error_count = 0

    for symbol in symbols:
        try:
            # Get instrument key
            query = f"""
                SELECT instrument_key
                FROM instruments
                WHERE trading_symbol = '{symbol}'
                  AND segment = 'NSE_EQ'
                LIMIT 1
            """

            result = db.con.execute(query).fetchone()
            if not result:
                print(f"  ⚠️  {symbol}: No instrument key found")
                error_count += 1
                continue

            inst_key = result[0]

            # Fetch historical intraday data
            # Upstox intraday API: GET /v2/historical-candle/intraday/{instrument_key}/{interval}
            # This returns today's data automatically

            url = f"https://api.upstox.com/v2/historical-candle/intraday/{inst_key}/1minute"

            headers = {
                'Accept': 'application/json',
                'Authorization': f'Bearer {token}'
            }

            response = requests.get(url, headers=headers, timeout=30)

            if response.status_code == 200:
                data = response.json()

                if data.get('status') == 'success':
                    candles = data.get('data', {}).get('candles', [])

                    if candles:
                        # Insert candles into cache
                        inserted = 0

                        for candle in candles:
                            # Candle format: [timestamp, open, high, low, close, volume, oi]
                            ts = pd.to_datetime(candle[0])

                            # Only insert if within today's range
                            if ts.date() == today:
                                try:
                                    db.con.execute(f"""
                                        INSERT OR REPLACE INTO {LIVE_CACHE_TABLE}
                                        (symbol, timestamp, open, high, low, close, volume)
                                        VALUES (?, ?, ?, ?, ?, ?, ?)
                                    """, [
                                        symbol,
                                        ts,
                                        float(candle[1]),  # open
                                        float(candle[2]),  # high
                                        float(candle[3]),  # low
                                        float(candle[4]),  # close
                                        int(candle[5])     # volume
                                    ])
                                    inserted += 1
                                except:
                                    pass

                        print(f"  ✅ {symbol}: Inserted {inserted} candles")
                        success_count += 1
                    else:
                        print(f"  ⚠️  {symbol}: No candles in response")
                        error_count += 1
                else:
                    print(f"  ❌ {symbol}: API error - {data.get('message')}")
                    error_count += 1
            else:
                print(f"  ❌ {symbol}: HTTP {response.status_code}")
                error_count += 1

        except Exception as e:
            print(f"  ❌ {symbol}: {e}")
            error_count += 1

    return success_count, error_count


def main():
    print("=" * 70)
    print("📊 BACKFILL TODAY'S INTRADAY DATA")
    print("=" * 70)

    db = TradingDB()
    print(f"✓ Connected to: {db.db_path}")

    # Check if market is/was open today
    now = datetime.now()
    today = date.today()

    # Skip weekends
    if now.weekday() > 4:
        print("\n⚠️  Today is weekend - no market data to backfill")
        return

    market_start = datetime.combine(today, dt_time(9, 15))
    market_end = datetime.combine(today, dt_time(15, 30))

    if now < market_start:
        print("\n⚠️  Market hasn't opened yet today")
        return

    print(f"✓ Market {'is open' if now < market_end else 'closed for today'}")

    # Get symbols to backfill
    # Option 1: From daily analyzer results
    results_file = Path("data/state/shortlisted_stocks.csv")

    if results_file.exists():
        df = pd.read_csv(results_file)
        if 'Symbol' in df.columns:
            symbols = df['Symbol'].tolist()
            print(f"✓ Using {len(symbols)} symbols from Daily Analyzer")
        else:
            symbols = []
    else:
        symbols = []

    # Option 2: If no shortlist, use all F&O stocks
    if not symbols:
        query = """
            SELECT DISTINCT trading_symbol
            FROM fo_stocks_master
            ORDER BY trading_symbol
        """

        result = db.con.execute(query).fetchall()
        symbols = [row[0] for row in result]

        print(f"✓ Using all {len(symbols)} F&O stocks")

    if not symbols:
        print("\n❌ No symbols to backfill")
        return

    # Backfill
    print("\n" + "=" * 70)
    print("🔄 BACKFILLING DATA")
    print("=" * 70)

    success, errors = backfill_todays_intraday(symbols, db)

    # Summary
    print("\n" + "=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    print(f"  ✅ Success: {success}")
    print(f"  ❌ Errors:  {errors}")
    print(f"  📋 Total:   {len(symbols)}")

    # Check cache
    query = f"""
        SELECT COUNT(*) FROM {LIVE_CACHE_TABLE}
        WHERE DATE(timestamp) = '{today}'
    """

    total_candles = db.con.execute(query).fetchone()[0]

    print(f"\n📦 Today's cache now has {total_candles:,} candles")

    print("\n✅ Done! You can now refresh the Live Entry Monitor")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted")
    except Exception as e:
        print(f"\n\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
