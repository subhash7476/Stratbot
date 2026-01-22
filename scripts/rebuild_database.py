"""
Database Rebuild Script
Creates fresh database and optionally imports from exported data
"""

from core.database import get_db
import sys
from pathlib import Path
from datetime import datetime
import duckdb
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


DB_PATH = ROOT / "data" / "trading_bot.duckdb"
OLD_DB_PATH = ROOT / "data" / "trading_bot_OLD.duckdb"


def rebuild_database(import_from_export=False, export_dir=None):
    """Rebuild database from scratch"""

    print("=" * 80)
    print("🔨 DATABASE REBUILD")
    print("=" * 80)
    print()

    # Step 1: Rename old database
    print("STEP 1: Backing up corrupted database...")
    if DB_PATH.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = ROOT / "data" / \
            f"trading_bot_CORRUPTED_{timestamp}.duckdb"
        DB_PATH.rename(backup_path)
        print(f"  ✅ Moved to: {backup_path.name}")
    else:
        print("  ℹ️  No existing database found")

    # Step 2: Create fresh database
    print("\nSTEP 2: Creating fresh database...")
    try:
        db = get_db()
        print("  ✅ New database created with all tables")
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False

    # Step 3: Import instruments if available
    if import_from_export and export_dir:
        print(f"\nSTEP 3: Importing from export: {export_dir}")

        export_path = Path(export_dir)

        # Import instruments
        instruments_file = export_path / "instruments.parquet"
        if instruments_file.exists():
            print("  📋 Importing instruments...")
            try:
                instruments = pd.read_parquet(instruments_file)
                db.con.execute("""
                    INSERT INTO instruments 
                    SELECT * FROM instruments
                """)
                print(f"     ✅ Imported {len(instruments):,} instruments")
            except Exception as e:
                print(f"     ❌ Failed: {e}")

        # Import F&O master
        fo_file = export_path / "fo_stocks_master.parquet"
        if fo_file.exists():
            print("  📋 Importing F&O master list...")
            try:
                fo_master = pd.read_parquet(fo_file)
                db.con.execute("""
                    INSERT INTO fo_stocks_master 
                    SELECT * FROM fo_master
                """)
                print(f"     ✅ Imported {len(fo_master):,} F&O stocks")
            except Exception as e:
                print(f"     ❌ Failed: {e}")

        # Import OHLCV data (if exists)
        ohlcv_dir = export_path / "ohlcv_1m"
        if ohlcv_dir.exists():
            print("  📊 Importing OHLCV data...")
            parquet_files = list(ohlcv_dir.glob("*.parquet"))

            if parquet_files:
                success = 0
                for idx, pq_file in enumerate(parquet_files):
                    try:
                        df = pd.read_parquet(pq_file)
                        db.con.execute("""
                            INSERT INTO ohlcv_1m 
                            SELECT * FROM df
                        """)
                        success += 1
                        print(
                            f"     [{idx+1}/{len(parquet_files)}] {pq_file.stem}", end='\r')
                    except Exception as e:
                        pass  # Skip errors

                print(
                    f"\n     ✅ Imported {success}/{len(parquet_files)} symbols")

    db.con.close()

    print("\n" + "=" * 80)
    print("✅ DATABASE REBUILD COMPLETE")
    print("=" * 80)
    print()
    print("📋 NEXT STEPS:")
    print()
    print("1. Download instruments:")
    print("   → Go to Page 1: Login & Instruments")
    print("   → Click 'Download Instruments'")
    print()
    print("2. Fetch historical data:")
    print("   → Go to Page 2: Fetch & Manage Data")
    print("   → Select F&O Stocks (Auto)")
    print("   → Choose date range (last 30 days recommended)")
    print("   → Click 'Start Fetching Data'")
    print()
    print("3. Resample data:")
    print("   → Stay on Page 2, go to 'Resample Data' tab")
    print("   → Select timeframes (5min, 15min, 1day)")
    print("   → Click 'Start Resampling'")
    print()
    print("4. Verify database:")
    print("   → python check_database.py")
    print()

    return True


def quick_rebuild():
    """Quick rebuild without import"""
    print("\n🔨 QUICK REBUILD (No Import)")
    print()
    print("This will:")
    print("  • Backup corrupted database")
    print("  • Create fresh empty database")
    print("  • You'll need to re-fetch all data")
    print()

    response = input("Continue? (yes/no): ")

    if response.lower() in ['yes', 'y']:
        return rebuild_database(import_from_export=False)
    else:
        print("\nCancelled.")
        return False


def rebuild_with_import():
    """Rebuild and import from export"""
    print("\n🔨 REBUILD WITH IMPORT")
    print()

    export_dir = input("Enter export directory path (or 'skip'): ").strip()

    if export_dir.lower() == 'skip':
        return quick_rebuild()

    export_path = Path(export_dir)
    if not export_path.exists():
        print(f"❌ Directory not found: {export_dir}")
        return False

    return rebuild_database(import_from_export=True, export_dir=export_dir)


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("DATABASE REBUILD OPTIONS")
    print("=" * 80)
    print()
    print("1. Quick rebuild (empty database)")
    print("2. Rebuild with import (from exported data)")
    print("3. Cancel")
    print()

    choice = input("Select option (1/2/3): ").strip()

    if choice == '1':
        quick_rebuild()
    elif choice == '2':
        rebuild_with_import()
    else:
        print("\nCancelled.")
