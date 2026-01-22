"""
Pre-Migration Verification Script
Checks data integrity before DuckDB migration
"""

import sys
from pathlib import Path
import pandas as pd
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

print("\n" + "="*70)
print("🔍 PRE-MIGRATION VERIFICATION")
print("="*70)
print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

data_dir = ROOT / "data"

# ============================================================================
# 1. Check directory structure
# ============================================================================
print("📂 Checking directory structure...")

required_dirs = [
    data_dir / "stocks",
    data_dir / "derived",
    data_dir / "instruments"
]

for dir_path in required_dirs:
    status = "✅" if dir_path.exists() else "❌"
    print(f"  {status} {dir_path.relative_to(ROOT)}")

# ============================================================================
# 2. Count instruments
# ============================================================================
print("\n📋 Checking instruments...")

instruments_file = data_dir / "instruments" / "all_instruments.parquet"
if instruments_file.exists():
    df = pd.read_parquet(instruments_file)
    print(f"  ✅ Found {len(df)} total instruments")
    
    # Count by segment
    segment_counts = df['segment'].value_counts()
    for segment, count in segment_counts.items():
        print(f"     - {segment}: {count}")
else:
    print("  ⚠️  No all_instruments.parquet found")

# ============================================================================
# 3. Count symbols with data
# ============================================================================
print("\n📊 Checking stock data...")

stocks_dir = data_dir / "stocks"
if stocks_dir.exists():
    symbol_dirs = [d for d in stocks_dir.iterdir() if d.is_dir()]
    print(f"  ✅ Found {len(symbol_dirs)} symbols with data")
    
    # Sample 5 symbols for detailed check
    print("\n  📝 Sample check (first 5 symbols):")
    for symbol_dir in sorted(symbol_dirs)[:5]:
        symbol = symbol_dir.name
        minute_dir = symbol_dir / "1minute"
        
        if minute_dir.exists():
            # Count partition files
            partition_files = list(minute_dir.rglob("data.parquet"))
            
            if partition_files:
                # Load first partition to check structure
                sample_df = pd.read_parquet(partition_files[0])
                print(f"     {symbol}: {len(partition_files)} partitions, "
                      f"Columns: {list(sample_df.columns)}")

# ============================================================================
# 4. Check derived data
# ============================================================================
print("\n⏱️  Checking derived data...")

derived_dir = data_dir / "derived"
if derived_dir.exists():
    symbol_dirs = [d for d in derived_dir.iterdir() if d.is_dir()]
    print(f"  ✅ Found {len(symbol_dirs)} symbols with derived data")
    
    # Count by timeframe
    timeframe_counts = {}
    for symbol_dir in symbol_dirs:
        for tf_dir in symbol_dir.iterdir():
            if tf_dir.is_dir():
                tf = tf_dir.name
                timeframe_counts[tf] = timeframe_counts.get(tf, 0) + 1
    
    print("  📊 Timeframe distribution:")
    for tf, count in sorted(timeframe_counts.items()):
        print(f"     - {tf}: {count} symbols")

# ============================================================================
# 5. Estimate database size
# ============================================================================
print("\n💾 Estimating database size...")

def get_dir_size(path):
    """Calculate directory size recursively"""
    total = 0
    try:
        for item in path.rglob('*'):
            if item.is_file():
                total += item.stat().st_size
    except Exception:
        pass
    return total

stocks_size = get_dir_size(stocks_dir) if stocks_dir.exists() else 0
derived_size = get_dir_size(derived_dir) if derived_dir.exists() else 0
instruments_size = get_dir_size(data_dir / "instruments") if (data_dir / "instruments").exists() else 0

total_size = stocks_size + derived_size + instruments_size

print(f"  Current Parquet data:")
print(f"     - Raw 1m data: {stocks_size / (1024**3):.2f} GB")
print(f"     - Derived data: {derived_size / (1024**3):.2f} GB")
print(f"     - Instruments: {instruments_size / (1024**2):.2f} MB")
print(f"     - Total: {total_size / (1024**3):.2f} GB")

# DuckDB will be ~60-70% of Parquet size due to better compression
estimated_db_size = total_size * 0.65
print(f"\n  Estimated DuckDB size: {estimated_db_size / (1024**3):.2f} GB")

# ============================================================================
# 6. Check for corrupted files
# ============================================================================
print("\n🔍 Checking for corrupted files...")

corrupted_files = []
sample_size = min(20, len(symbol_dirs)) if 'symbol_dirs' in locals() else 0

if sample_size > 0:
    print(f"  Testing {sample_size} random symbols...")
    import random
    sample_symbols = random.sample(symbol_dirs, sample_size)
    
    for symbol_dir in sample_symbols:
        minute_dir = symbol_dir / "1minute"
        if not minute_dir.exists():
            continue
        
        partition_files = list(minute_dir.rglob("data.parquet"))
        
        for file in partition_files[:3]:  # Check first 3 partitions per symbol
            try:
                df = pd.read_parquet(file)
                if df.empty:
                    corrupted_files.append(str(file))
            except Exception as e:
                corrupted_files.append(str(file))
    
    if corrupted_files:
        print(f"  ⚠️  Found {len(corrupted_files)} potentially corrupted files:")
        for file in corrupted_files[:5]:
            print(f"     - {file}")
    else:
        print("  ✅ All sampled files are valid")

# ============================================================================
# 7. Final summary
# ============================================================================
print("\n" + "="*70)
print("📊 VERIFICATION SUMMARY")
print("="*70)

checks = {
    "Directory structure": all(d.exists() for d in required_dirs),
    "Instruments file": instruments_file.exists() if 'instruments_file' in locals() else False,
    "Stock data": len(symbol_dirs) > 0 if 'symbol_dirs' in locals() else False,
    "No corrupted files": len(corrupted_files) == 0 if 'corrupted_files' in locals() else True
}

all_passed = all(checks.values())

for check, passed in checks.items():
    status = "✅" if passed else "❌"
    print(f"{status} {check}")

print("\n" + "="*70)
if all_passed:
    print("✅ ALL CHECKS PASSED - Ready for migration!")
    print("="*70)
    print("\n🚀 Next step: Run migration script")
    print("   python scripts/migrate_to_duckdb.py")
else:
    print("⚠️  SOME CHECKS FAILED - Review issues before migration")
    print("="*70)

print(f"\nEnd time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")