# diagnose_tables.py
"""
Quick diagnostic to find correct table names
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database import TradingDB

db = TradingDB()

print("=" * 70)
print("📊 DATABASE TABLES")
print("=" * 70)

# Get all tables
tables = db.con.execute("SHOW TABLES").fetchall()

print(f"\nFound {len(tables)} tables:")
for table in tables:
    table_name = table[0]
    
    # Get row count
    try:
        count = db.con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"  ✓ {table_name:30s} ({count:,} rows)")
    except:
        print(f"  ✗ {table_name:30s} (error getting count)")

print("\n" + "=" * 70)
print("🔍 CHECKING FOR OHLCV TABLES")
print("=" * 70)

ohlcv_tables = [t[0] for t in tables if 'ohlcv' in t[0].lower() or 'candle' in t[0].lower()]

if ohlcv_tables:
    print(f"\nFound {len(ohlcv_tables)} OHLCV-related tables:")
    
    for table_name in ohlcv_tables:
        print(f"\n  Table: {table_name}")
        
        # Get columns
        cols = db.con.execute(f"DESCRIBE {table_name}").fetchall()
        print(f"  Columns: {', '.join([c[0] for c in cols])}")
        
        # Get sample
        try:
            sample = db.con.execute(f"SELECT * FROM {table_name} LIMIT 1").fetchone()
            if sample:
                print(f"  Sample data available: YES")
        except:
            print(f"  Sample data available: NO")
else:
    print("\n❌ No OHLCV tables found!")

print("\n" + "=" * 70)
print("💡 RECOMMENDATION")
print("=" * 70)

# Find the right table
if 'ohlcv_1m' in [t[0] for t in tables]:
    print("\n✅ Use: ohlcv_1m")
elif 'ohlcv_data_1m' in [t[0] for t in tables]:
    print("\n✅ Use: ohlcv_data_1m")
else:
    print("\n⚠️  No standard 1-minute OHLCV table found")
    print(f"Available tables: {[t[0] for t in tables]}")