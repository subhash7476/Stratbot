"""
Debug live data issues
"""
from core.database import get_db
from datetime import date

db = get_db()

# Check cache
print("="*60)
print("LIVE_OHLCV_CACHE (1m data)")
print("="*60)
cache_stats = db.con.execute("""
    SELECT
        COUNT(*) as total_candles,
        COUNT(DISTINCT instrument_key) as instruments,
        MIN(timestamp) as first_candle,
        MAX(timestamp) as last_candle
    FROM live_ohlcv_cache
    WHERE DATE(timestamp) = CURRENT_DATE
""").fetchone()

print(f"Total candles: {cache_stats[0]:,}")
print(f"Instruments: {cache_stats[1]}")
print(f"First candle: {cache_stats[2]}")
print(f"Last candle: {cache_stats[3]}")
print(f"Avg candles/instrument: {cache_stats[0] / max(cache_stats[1], 1):.1f}")

# Check resampled
print("\n" + "="*60)
print("OHLCV_RESAMPLED_LIVE (5m/15m/60m data)")
print("="*60)
resampled_stats = db.con.execute("""
    SELECT
        timeframe,
        COUNT(*) as candles,
        COUNT(DISTINCT instrument_key) as instruments,
        MIN(timestamp) as first_candle,
        MAX(timestamp) as last_candle
    FROM ohlcv_resampled_live
    WHERE DATE(timestamp) = CURRENT_DATE
    GROUP BY timeframe
    ORDER BY timeframe
""").fetchall()

for row in resampled_stats:
    tf, candles, instruments, first, last = row
    print(f"\n{tf}:")
    print(f"  Candles: {candles:,}")
    print(f"  Instruments: {instruments}")
    print(f"  First: {first}")
    print(f"  Last: {last}")
    print(f"  Avg/instrument: {candles / max(instruments, 1):.1f}")

# Sample one instrument
print("\n" + "="*60)
print("SAMPLE INSTRUMENT: NSE_EQ|INE669E01016 (TATACOMM)")
print("="*60)

inst_key = "NSE_EQ|INE669E01016"
today_str = date.today().strftime('%Y-%m-%d')

# Check cache
cache_sample = db.con.execute("""
    SELECT COUNT(*), MIN(timestamp), MAX(timestamp)
    FROM live_ohlcv_cache
    WHERE instrument_key = ?
    AND DATE(timestamp) = CURRENT_DATE
""", [inst_key]).fetchone()

print(
    f"Cache (1m): {cache_sample[0]} candles from {cache_sample[1]} to {cache_sample[2]}")

# Check resampled
for tf in ['5minute', '15minute', '60minute']:
    resampled_sample = db.con.execute("""
        SELECT COUNT(*), MIN(timestamp), MAX(timestamp)
        FROM ohlcv_resampled_live
        WHERE instrument_key = ?
        AND timeframe = ?
        AND DATE(timestamp) = CURRENT_DATE
    """, [inst_key, tf]).fetchone()

    print(
        f"Resampled ({tf}): {resampled_sample[0]} candles from {resampled_sample[1]} to {resampled_sample[2]}")

# Check historical
print("\n" + "="*60)
print("HISTORICAL DATA (last 60 days)")
print("="*60)
for tf in ['5minute', '15minute', '60minute']:
    hist_sample = db.con.execute(f"""
        SELECT COUNT(*), MIN(timestamp), MAX(timestamp)
        FROM ohlcv_resampled
        WHERE instrument_key = ?
        AND timeframe = ?
        AND timestamp >= DATE_SUB(CURRENT_DATE, INTERVAL 60 DAY)
        AND timestamp < '{today_str}'
    """, [inst_key, tf]).fetchone()

    print(
        f"{tf}: {hist_sample[0]} candles from {hist_sample[1]} to {hist_sample[2]}")

print("\n" + "="*60)
print("COMBINED MTF QUERY TEST")
print("="*60)

# Test the actual query used in get_live_mtf_data
cutoff = db.con.execute(
    "SELECT DATE_SUB(CURRENT_DATE, INTERVAL 60 DAY)").fetchone()[0]

for tf in ['5minute', '15minute', '60minute']:
    # Historical
    hist_query = """
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv_resampled
        WHERE instrument_key = ?
          AND timeframe = ?
          AND timestamp >= ?
          AND timestamp < ?
        ORDER BY timestamp
    """
    df_hist = db.con.execute(
        hist_query, [inst_key, tf, cutoff, today_str]).df()

    # Live
    live_query = """
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv_resampled_live
        WHERE instrument_key = ?
          AND timeframe = ?
          AND timestamp >= ?
        ORDER BY timestamp
    """
    df_live = db.con.execute(
        live_query, [inst_key, tf, today_str]).df()

    print(f"\n{tf}:")
    print(f"  Historical: {len(df_hist)} candles")
    print(f"  Live: {len(df_live)} candles")
    print(f"  Combined: {len(df_hist) + len(df_live)} candles")

    if len(df_live) > 0:
        print(
            f"  Live range: {df_live['timestamp'].min()} to {df_live['timestamp'].max()}")
