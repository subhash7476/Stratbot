"""
Resampling Diagnostic Script
Run this to identify why resampling is skipping all symbols
"""

import sys
from pathlib import Path

# Add project root
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from core.database import get_db
except ImportError:
    print("❌ Could not import database module. Make sure you're in the project directory.")
    sys.exit(1)

def diagnose():
    print("\n" + "="*70)
    print("🔍 RESAMPLING DIAGNOSTICS")
    print("="*70 + "\n")
    
    db = get_db()
    
    # 1. Check ohlcv_1m data
    print("1️⃣ Checking 1-minute data (ohlcv_1m)...")
    try:
        count_1m = db.con.execute("SELECT COUNT(*) FROM ohlcv_1m").fetchone()[0]
        print(f"   Total 1m candles: {count_1m:,}")
        
        if count_1m == 0:
            print("   ❌ NO 1-MINUTE DATA FOUND! This is why resampling skips everything.")
            print("   💡 Solution: Go to Tab 1 (Fetch Data) and download historical data first.")
            return
        
        # Get date range
        date_range = db.con.execute("""
            SELECT MIN(timestamp) as first, MAX(timestamp) as last 
            FROM ohlcv_1m
        """).fetchone()
        print(f"   Date range: {date_range[0]} to {date_range[1]}")
        
        # Get unique instruments
        instruments = db.con.execute("""
            SELECT COUNT(DISTINCT instrument_key) FROM ohlcv_1m
        """).fetchone()[0]
        print(f"   Unique instruments: {instruments}")
        
    except Exception as e:
        print(f"   ❌ Error querying ohlcv_1m: {e}")
        return
    
    # 2. Check ohlcv_resampled table
    print("\n2️⃣ Checking resampled data (ohlcv_resampled)...")
    try:
        count_resampled = db.con.execute("SELECT COUNT(*) FROM ohlcv_resampled").fetchone()[0]
        print(f"   Total resampled candles: {count_resampled:,}")
        
        if count_resampled > 0:
            # Get breakdown by timeframe
            breakdown = db.con.execute("""
                SELECT timeframe, COUNT(*) as count 
                FROM ohlcv_resampled 
                GROUP BY timeframe 
                ORDER BY timeframe
            """).df()
            print("   Breakdown by timeframe:")
            for _, row in breakdown.iterrows():
                print(f"      {row['timeframe']}: {row['count']:,}")
        else:
            print("   📭 Resampled table is empty (expected if you just cleared it)")
            
    except Exception as e:
        print(f"   ❌ Error querying ohlcv_resampled: {e}")
        print("   💡 The table might not exist. Check your database schema.")
        return
    
    # 3. Check table schema
    print("\n3️⃣ Checking table schemas...")
    try:
        schema_1m = db.con.execute("DESCRIBE ohlcv_1m").df()
        print("   ohlcv_1m columns:", schema_1m['column_name'].tolist())
        
        schema_resampled = db.con.execute("DESCRIBE ohlcv_resampled").df()
        print("   ohlcv_resampled columns:", schema_resampled['column_name'].tolist())
    except Exception as e:
        print(f"   ⚠️ Could not get schema: {e}")
    
    # 4. Sample data test
    print("\n4️⃣ Testing resampling logic with sample data...")
    
    # Get a sample instrument
    sample = db.con.execute("""
        SELECT DISTINCT i.instrument_key, i.trading_symbol, COUNT(*) as candles
        FROM ohlcv_1m o
        JOIN instruments i ON o.instrument_key = i.instrument_key
        GROUP BY i.instrument_key, i.trading_symbol
        ORDER BY candles DESC
        LIMIT 1
    """).fetchone()
    
    if sample:
        instrument_key = sample[0]
        symbol = sample[1]
        candles = sample[2]
        print(f"   Sample symbol: {symbol} ({candles:,} candles)")
        
        # Try manual resample
        print("\n   Attempting manual 15-minute resample...")
        try:
            result = db.con.execute(f"""
                SELECT 
                    instrument_key,
                    '15minute' AS timeframe,
                    time_bucket(INTERVAL '15 minutes', timestamp, TIMESTAMP '1970-01-01 09:15:00') AS bucket_ts,
                    arg_min(open, timestamp) AS open,
                    MAX(high) AS high,
                    MIN(low) AS low,
                    arg_max(close, timestamp) AS close,
                    SUM(volume) AS volume
                FROM ohlcv_1m
                WHERE instrument_key = '{instrument_key}'
                GROUP BY instrument_key, bucket_ts
                ORDER BY bucket_ts DESC
                LIMIT 5
            """).df()
            
            if not result.empty:
                print(f"   ✅ Resampling works! Got {len(result)} candles:")
                print(result.to_string(index=False))
            else:
                print("   ❌ Resampling returned empty result")
        except Exception as e:
            print(f"   ❌ Resampling failed: {e}")
    
    # 5. Check for data type issues
    print("\n5️⃣ Checking data types...")
    try:
        sample_row = db.con.execute("""
            SELECT timestamp, open, high, low, close, volume, instrument_key
            FROM ohlcv_1m
            LIMIT 1
        """).df()
        print("   Sample row:")
        print(f"      timestamp type: {sample_row['timestamp'].dtype}")
        print(f"      open type: {sample_row['open'].dtype}")
        print(f"      instrument_key: {sample_row['instrument_key'].iloc[0]}")
    except Exception as e:
        print(f"   ⚠️ Could not check data types: {e}")
    
    # 6. Recommendations
    print("\n" + "="*70)
    print("📋 RECOMMENDATIONS")
    print("="*70)
    
    if count_1m == 0:
        print("❌ CRITICAL: No 1-minute data. Fetch data first!")
    elif count_resampled == 0:
        print("✅ Your 1m data looks good. Try resampling with these steps:")
        print("   1. In Tab 2 (Resample Data), UNCHECK 'Skip Existing Data'")
        print("   2. Select 'All available data' for date range")
        print("   3. Select all timeframes you need")
        print("   4. Click 'Start Resampling'")
        print("\n   OR run this SQL directly in your code:")
        print("""
   INSERT OR REPLACE INTO ohlcv_resampled
   SELECT
       instrument_key,
       '15minute' AS timeframe,
       time_bucket(INTERVAL '15 minutes', timestamp, TIMESTAMP '1970-01-01 09:15:00') AS timestamp,
       arg_min(open, timestamp) AS open,
       MAX(high) AS high,
       MIN(low) AS low,
       arg_max(close, timestamp) AS close,
       SUM(volume) AS volume,
       0 AS oi
   FROM ohlcv_1m
   GROUP BY instrument_key, time_bucket(INTERVAL '15 minutes', timestamp, TIMESTAMP '1970-01-01 09:15:00');
        """)
    else:
        print("✅ Both tables have data. Check the specific error messages in the UI.")
    
    print("\n" + "="*70)

if __name__ == "__main__":
    diagnose()