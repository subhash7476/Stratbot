# check_live_cache.py
"""
Check what's in the live_ohlcv_cache table
"""

import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database import TradingDB

db = TradingDB()

print("=" * 70)
print("📊 LIVE CACHE ANALYSIS")
print("=" * 70)

# Get cache stats
query = """
    SELECT 
        symbol,
        COUNT(*) as candles,
        MIN(timestamp) as first_time,
        MAX(timestamp) as last_time,
        MIN(close) as min_price,
        MAX(close) as max_price
    FROM live_ohlcv_cache
    GROUP BY symbol
    ORDER BY symbol
"""

result = db.con.execute(query).fetchall()

print(f"\n📦 Cache Summary:")
print(f"Total symbols cached: {len(result)}")
print(f"Total rows: 437")

print("\n📋 Per Symbol Breakdown:")
print("-" * 70)
print(f"{'Symbol':<12} {'Candles':>8} {'First Time':>19} {'Last Time':>19}")
print("-" * 70)

for row in result:
    symbol, candles, first_time, last_time, min_price, max_price = row
    print(f"{symbol:<12} {candles:>8} {str(first_time)[:19]:>19} {str(last_time)[:19]:>19}")

# Check WIPRO specifically
print("\n" + "=" * 70)
print("🔍 WIPRO DETAILED CHECK")
print("=" * 70)

query = """
    SELECT *
    FROM live_ohlcv_cache
    WHERE symbol = 'WIPRO'
    ORDER BY timestamp
"""

wipro_data = db.con.execute(query).fetchall()

if wipro_data:
    print(f"\nFound {len(wipro_data)} WIPRO candles in cache:")
    print("-" * 70)
    print(f"{'Timestamp':<20} {'Open':>8} {'High':>8} {'Low':>8} {'Close':>8} {'Volume':>12}")
    print("-" * 70)
    
    for row in wipro_data[:10]:  # Show first 10
        ts, o, h, l, c, v = row[1], row[2], row[3], row[4], row[5], row[6]
        print(f"{str(ts)[:19]:<20} {o:>8.2f} {h:>8.2f} {l:>8.2f} {c:>8.2f} {v:>12,}")
    
    if len(wipro_data) > 10:
        print(f"\n... and {len(wipro_data) - 10} more candles")
else:
    print("\n❌ No WIPRO data in cache!")

# Check timeframe
print("\n" + "=" * 70)
print("⏰ TIMEFRAME DETECTION")
print("=" * 70)

query = """
    SELECT 
        timestamp,
        LAG(timestamp) OVER (ORDER BY timestamp) as prev_timestamp
    FROM live_ohlcv_cache
    WHERE symbol = 'WIPRO'
    ORDER BY timestamp
    LIMIT 5
"""

time_diffs = db.con.execute(query).fetchall()

if time_diffs and len(time_diffs) > 1:
    print("\nTime differences between consecutive candles:")
    for i, (curr_ts, prev_ts) in enumerate(time_diffs[1:], 1):
        if prev_ts:
            diff = (curr_ts - prev_ts).total_seconds() / 60
            print(f"  Candle {i}: {diff:.1f} minutes")
    
    # Guess timeframe
    if len(time_diffs) > 1:
        first_diff = (time_diffs[1][0] - time_diffs[1][1]).total_seconds() / 60
        print(f"\n💡 Detected timeframe: ~{first_diff:.0f} minute candles")
else:
    print("\nNot enough data to detect timeframe")

print("\n" + "=" * 70)