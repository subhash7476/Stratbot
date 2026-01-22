"""
Quick Instruments Migration
Migrates instruments from root/instruments/ to DuckDB
"""

import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database import TradingDB

print("\n" + "="*70)
print("📋 INSTRUMENTS MIGRATION")
print("="*70)

# Initialize database
db = TradingDB()

# Try to find instruments directory
possible_paths = [
    ROOT / "instruments" / "all_instruments.parquet",
    ROOT / "data" / "instruments" / "all_instruments.parquet"
]

instruments_file = None
for path in possible_paths:
    if path.exists():
        instruments_file = path
        break

if instruments_file is None:
    print("❌ Could not find all_instruments.parquet in:")
    for path in possible_paths:
        print(f"   - {path}")
    print("\nPlease specify the correct path:")
    instruments_file = input("Path to all_instruments.parquet: ")
    instruments_file = Path(instruments_file)
    
    if not instruments_file.exists():
        print("❌ File not found!")
        sys.exit(1)

print(f"\n📥 Loading instruments from: {instruments_file}")

# Load instruments
df = pd.read_parquet(instruments_file)
print(f"   Found {len(df)} instruments")

# Show data types
print("\n📊 Column data types:")
for col in df.columns:
    print(f"   {col}: {df[col].dtype}")

# Show sample with expiry
if 'expiry' in df.columns:
    print("\n📅 Sample expiry values:")
    expiry_samples = df[df['expiry'].notna()]['expiry'].head(5)
    for val in expiry_samples:
        print(f"   {val} (type: {type(val).__name__})")

# Show sample
print("\n📋 Sample instruments:")
print(df.head(3).to_string(index=False))

# Confirm
print(f"\n❓ Migrate {len(df)} instruments to database?")
confirm = input("Type 'yes' to continue: ")

if confirm.lower() != 'yes':
    print("❌ Migration cancelled")
    sys.exit(0)

# Migrate
print("\n🚀 Migrating...")
db.upsert_instruments(df)

# Verify
count = db.con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
print(f"\n✅ Success! {count} instruments in database")

# Show segment breakdown
segment_counts = db.con.execute("""
    SELECT segment, COUNT(*) as count
    FROM instruments
    GROUP BY segment
    ORDER BY count DESC
""").df()

print("\n📊 By segment:")
print(segment_counts.to_string(index=False))

# Show sample queries
print("\n📝 Sample queries you can now run:")
print("   db.get_instruments(segment='NSE_EQ')")
print("   db.get_instruments(name='RELIANCE')")

db.close()
print("\n✅ Done!")