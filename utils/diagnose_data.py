# diagnose_data.py
"""
Quick script to diagnose why strategies aren't working
Run this to check your data quality
"""

import pandas as pd
from pathlib import Path

# Load your INFY data
file_path = Path("data/derived/INFY/15minute/INFY_15minute_20240101_20251216.parquet")

print("=" * 70)
print("DATA QUALITY DIAGNOSTIC")
print("=" * 70)

try:
    df = pd.read_parquet(file_path)
    
    print(f"\n✅ File loaded: {file_path.name}")
    print(f"   Shape: {df.shape}")
    print(f"   Columns: {list(df.columns)}")
    
    # Check 1: Index type
    print("\n[CHECK 1] Index Type")
    if isinstance(df.index, pd.DatetimeIndex):
        print("   ✅ DateTimeIndex detected")
        print(f"   First timestamp: {df.index[0]}")
        print(f"   Last timestamp: {df.index[-1]}")
        
        # Check timezone
        if df.index.tz is None:
            print("   ⚠️  No timezone set (should be IST)")
        else:
            print(f"   Timezone: {df.index.tz}")
        
        # Check time range
        print("\n[CHECK 2] Trading Hours Coverage")
        df['time'] = df.index.time
        df['hour'] = df.index.hour
        
        unique_hours = sorted(df['hour'].unique())
        print(f"   Hours in data: {unique_hours}")
        
        if 9 in unique_hours:
            print("   ✅ Data includes market open (9:00-10:00 AM)")
        else:
            print("   ❌ Data MISSING market open! ORB won't work")
        
        # Check opening range specifically
        morning_data = df[(df['hour'] == 9) & (df['time'] >= pd.Timestamp('09:15').time())]
        print(f"   Morning candles (9:15+ AM): {len(morning_data)}")
        
        if len(morning_data) == 0:
            print("   ❌ NO DATA at 9:15 AM! This breaks ORB strategy")
            print("   FIX: Re-download data starting from market open")
    else:
        print("   ❌ NOT a DateTimeIndex! Converting...")
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            print("   ✅ Converted to DateTimeIndex")
        else:
            print("   ❌ No timestamp column found!")
    
    # Check 3: Required columns
    print("\n[CHECK 3] Required Columns")
    required = ['Open', 'High', 'Low', 'Close', 'Volume']
    for col in required:
        if col in df.columns:
            print(f"   ✅ {col}: Present (range: {df[col].min():.2f} - {df[col].max():.2f})")
        else:
            # Try case-insensitive match
            matches = [c for c in df.columns if c.lower() == col.lower()]
            if matches:
                print(f"   ⚠️  {col}: Found as '{matches[0]}' (rename recommended)")
            else:
                print(f"   ❌ {col}: MISSING!")
    
    # Check 4: Data quality issues
    print("\n[CHECK 4] Data Quality")
    
    # Missing values
    missing = df.isnull().sum()
    if missing.sum() > 0:
        print("   ⚠️  Missing values detected:")
        for col in missing[missing > 0].index:
            print(f"      - {col}: {missing[col]} missing")
    else:
        print("   ✅ No missing values")
    
    # Duplicate timestamps
    dupes = df.index.duplicated().sum()
    if dupes > 0:
        print(f"   ⚠️  {dupes} duplicate timestamps found")
    else:
        print("   ✅ No duplicate timestamps")
    
    # Check for gaps
    expected_freq = '15min'
    time_diffs = df.index.to_series().diff()
    expected_diff = pd.Timedelta(expected_freq)
    gaps = (time_diffs > expected_diff * 1.5).sum()  # Allow 50% tolerance
    
    if gaps > 10:
        print(f"   ⚠️  {gaps} time gaps detected (might be holidays/weekends)")
    else:
        print(f"   ✅ Time continuity looks good ({gaps} small gaps)")
    
    # Check 5: Price action characteristics
    print("\n[CHECK 5] Price Action Characteristics")
    
    df['daily_return'] = df['Close'].pct_change()
    df['intraday_range'] = (df['High'] - df['Low']) / df['Close'] * 100
    
    avg_return = df['daily_return'].mean() * 100
    avg_volatility = df['daily_return'].std() * 100
    avg_range = df['intraday_range'].mean()
    
    print(f"   Avg 15-min return: {avg_return:.4f}%")
    print(f"   Volatility (std): {avg_volatility:.4f}%")
    print(f"   Avg intraday range: {avg_range:.3f}%")
    
    if avg_range < 0.2:
        print("   ⚠️  Very low volatility - strategies might struggle")
    elif avg_range > 1.5:
        print("   ⚠️  Very high volatility - increase stop losses")
    else:
        print("   ✅ Normal volatility range")
    
    # Check 6: Sample first day for ORB analysis
    print("\n[CHECK 6] Sample Day Analysis (for ORB)")
    
    first_date = df.index[0].date()
    day_data = df[df.index.date == first_date]
    
    print(f"   Date: {first_date}")
    print(f"   Candles: {len(day_data)}")
    print(f"   Time range: {day_data.index[0].time()} to {day_data.index[-1].time()}")
    
    # Opening range calculation
    or_start = pd.Timestamp('09:15').time()
    or_end = pd.Timestamp('09:30').time()
    
    or_data = day_data[(day_data.index.time >= or_start) & (day_data.index.time <= or_end)]
    
    if len(or_data) > 0:
        or_high = or_data['High'].max()
        or_low = or_data['Low'].min()
        or_range = (or_high - or_low) / or_low * 100
        
        print(f"   Opening Range (9:15-9:30):")
        print(f"      High: ₹{or_high:.2f}")
        print(f"      Low: ₹{or_low:.2f}")
        print(f"      Range: {or_range:.2f}%")
        
        # Check for breakouts
        post_or = day_data[day_data.index.time > or_end]
        if len(post_or) > 0:
            breakout_up = (post_or['Close'] > or_high).any()
            breakout_down = (post_or['Close'] < or_low).any()
            
            if breakout_up:
                print(f"   ✅ Upside breakout occurred")
            if breakout_down:
                print(f"   ✅ Downside breakout occurred")
            if not breakout_up and not breakout_down:
                print(f"   ⚠️  No breakouts (price stayed in range all day)")
    else:
        print(f"   ❌ NO OPENING RANGE DATA! ORB will fail")
    
    # RECOMMENDATIONS
    print("\n" + "=" * 70)
    print("RECOMMENDATIONS")
    print("=" * 70)
    
    recommendations = []
    
    if 9 not in unique_hours:
        recommendations.append("❌ CRITICAL: Re-download data with market open (9:15 AM)")
    
    if avg_range < 0.2:
        recommendations.append("⚠️  Low volatility - Try VWAP strategy instead of MR/ORB")
    
    if avg_range > 1.5:
        recommendations.append("⚠️  High volatility - Increase SL to 0.8-1.0%")
    
    if len(or_data) == 0:
        recommendations.append("❌ CRITICAL: ORB won't work - missing opening range data")
    
    if gaps > 20:
        recommendations.append("⚠️  Many gaps - Consider filling missing data")
    
    if not recommendations:
        recommendations.append("✅ Data looks good! Issue is likely strategy parameters")
    
    for rec in recommendations:
        print(f"\n{rec}")
    
    print("\n" + "=" * 70)
    
except FileNotFoundError:
    print(f"\n❌ File not found: {file_path}")
    print("\nAvailable files:")
    
    derived_dir = Path("data/derived")
    if derived_dir.exists():
        for f in derived_dir.rglob("*.parquet"):
            print(f"   - {f}")
    else:
        print("   No data/derived folder found")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()