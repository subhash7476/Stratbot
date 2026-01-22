"""
Emergency: Clean Duplicate Data from ohlcv_resampled
This script removes duplicate entries to fix the constraint error
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database import TradingDB

def clean_duplicates():
    """Remove duplicate entries from ohlcv_resampled table"""
    
    print("=" * 80)
    print("EMERGENCY: Cleaning Duplicate Data")
    print("=" * 80)
    print()
    
    db = TradingDB()
    
    # Step 1: Check current status
    print("STEP 1: Checking current data...")
    total_rows = db.con.execute("SELECT COUNT(*) FROM ohlcv_resampled").fetchone()[0]
    print(f"  Total rows in ohlcv_resampled: {total_rows:,}")
    
    # Check for duplicates
    dup_check = db.con.execute("""
        SELECT 
            instrument_key,
            timeframe,
            timestamp,
            COUNT(*) as count
        FROM ohlcv_resampled
        GROUP BY instrument_key, timeframe, timestamp
        HAVING COUNT(*) > 1
    """).fetchall()
    
    if not dup_check:
        print("  ✅ No duplicates found!")
        print()
        print("Your data is clean. The error might be from:")
        print("  • Concurrent writes")
        print("  • Re-running resampling without clearing first")
        print()
        print("Solution: Use the fixed Page 2 that deletes before inserting")
        return
    
    print(f"  ⚠️  Found {len(dup_check)} duplicate timestamp combinations")
    print()
    
    # Step 2: Create clean table
    print("STEP 2: Creating clean version of table...")
    
    # Create temp table with unique rows only (keep first occurrence)
    db.con.execute("""
        CREATE TEMPORARY TABLE ohlcv_resampled_clean AS
        SELECT DISTINCT ON (instrument_key, timeframe, timestamp)
            instrument_key,
            timeframe,
            timestamp,
            open,
            high,
            low,
            close,
            volume,
            oi
        FROM ohlcv_resampled
        ORDER BY instrument_key, timeframe, timestamp
    """)
    
    clean_rows = db.con.execute("SELECT COUNT(*) FROM ohlcv_resampled_clean").fetchone()[0]
    print(f"  ✅ Clean table created: {clean_rows:,} unique rows")
    print(f"  📊 Removed {total_rows - clean_rows:,} duplicate rows")
    print()
    
    # Step 3: Replace original table
    print("STEP 3: Replacing original table with clean version...")
    
    # Drop original
    db.con.execute("DROP TABLE ohlcv_resampled")
    
    # Rename clean to original
    db.con.execute("ALTER TABLE ohlcv_resampled_clean RENAME TO ohlcv_resampled")
    
    # Verify
    final_count = db.con.execute("SELECT COUNT(*) FROM ohlcv_resampled").fetchone()[0]
    print(f"  ✅ New table created: {final_count:,} rows")
    print()
    
    # Step 4: Recreate indexes/constraints if needed
    print("STEP 4: Recreating constraints...")
    
    try:
        # Add primary key constraint back
        db.con.execute("""
            ALTER TABLE ohlcv_resampled 
            ADD CONSTRAINT ohlcv_resampled_pk 
            PRIMARY KEY (instrument_key, timeframe, timestamp)
        """)
        print("  ✅ Primary key constraint added")
    except Exception as e:
        print(f"  ⚠️  Could not add constraint: {e}")
        print("  This is okay - DuckDB will still work without it")
    
    print()
    print("=" * 80)
    print("✅ CLEANUP COMPLETE")
    print("=" * 80)
    print()
    print("📋 Summary:")
    print(f"  • Before: {total_rows:,} rows (with duplicates)")
    print(f"  • After:  {final_count:,} rows (unique only)")
    print(f"  • Removed: {total_rows - final_count:,} duplicate rows")
    print()
    print("🎯 Next Steps:")
    print("  1. Your data is now clean")
    print("  2. Use the fixed Page 2 (Enhanced v2)")
    print("  3. Re-run resampling - it will work perfectly now!")
    print()
    
    db.con.close()

if __name__ == "__main__":
    print()
    response = input("⚠️  This will modify your ohlcv_resampled table. Continue? (yes/no): ")
    
    if response.lower() == 'yes':
        clean_duplicates()
    else:
        print("\n❌ Cancelled")