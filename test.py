"""
Quick Data Export - Save Critical Data to CSV/Parquet
Use this if database is too corrupted to recover directly
"""

import sys
from pathlib import Path
from datetime import datetime
import duckdb
import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "trading_bot.duckdb"
EXPORT_DIR = ROOT / "data" / "emergency_export"

def export_critical_data():
    """Export critical data to portable formats"""
    
    print("=" * 80)
    print("💾 EMERGENCY DATA EXPORT")
    print("=" * 80)
    print()
    
    # Create export directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_path = EXPORT_DIR / timestamp
    export_path.mkdir(parents=True, exist_ok=True)
    
    print(f"Export location: {export_path}")
    print()
    
    try:
        # Try read-only connection first
        print("Attempting read-only connection...")
        try:
            conn = duckdb.connect(str(DB_PATH), read_only=True)
            print("✅ Connected in read-only mode")
        except:
            print("⚠️  Read-only failed, trying normal connection...")
            conn = duckdb.connect(str(DB_PATH))
            print("✅ Connected in normal mode")
        
        print()
        
        # Export instruments (small, critical)
        print("1️⃣  Exporting instruments...")
        try:
            instruments = conn.execute("SELECT * FROM instruments").fetchdf()
            out_file = export_path / "instruments.parquet"
            instruments.to_parquet(out_file, compression='snappy')
            print(f"   ✅ Saved {len(instruments):,} rows → {out_file.name}")
        except Exception as e:
            print(f"   ❌ Failed: {e}")
        
        # Export F&O master list
        print("\n2️⃣  Exporting F&O stocks master...")
        try:
            fo_master = conn.execute("SELECT * FROM fo_stocks_master").fetchdf()
            out_file = export_path / "fo_stocks_master.parquet"
            fo_master.to_parquet(out_file, compression='snappy')
            print(f"   ✅ Saved {len(fo_master):,} rows → {out_file.name}")
        except Exception as e:
            print(f"   ❌ Failed: {e}")
        
        # Export OHLCV data (large - do in chunks)
        print("\n3️⃣  Exporting OHLCV data (this will take time)...")
        
        # Get list of instrument keys
        try:
            instruments_list = conn.execute("""
                SELECT DISTINCT instrument_key, trading_symbol
                FROM instruments
                WHERE segment = 'NSE_EQ'
                ORDER BY trading_symbol
            """).fetchdf()
            
            print(f"   Found {len(instruments_list)} symbols to export")
            
            # Create subdirectory for OHLCV
            ohlcv_dir = export_path / "ohlcv_1m"
            ohlcv_dir.mkdir(exist_ok=True)
            
            success = 0
            failed = 0
            
            for idx, row in instruments_list.iterrows():
                instrument_key = row['instrument_key']
                symbol = row['trading_symbol']
                
                try:
                    # Export last 30 days only (manageable size)
                    df = conn.execute(f"""
                        SELECT *
                        FROM ohlcv_1m
                        WHERE instrument_key = '{instrument_key}'
                          AND timestamp >= CURRENT_DATE - INTERVAL '30 days'
                        ORDER BY timestamp
                    """).fetchdf()
                    
                    if not df.empty:
                        out_file = ohlcv_dir / f"{symbol}.parquet"
                        df.to_parquet(out_file, compression='snappy')
                        success += 1
                        print(f"   [{idx+1}/{len(instruments_list)}] ✅ {symbol}: {len(df):,} rows", end='\r')
                    else:
                        failed += 1
                
                except Exception as e:
                    failed += 1
                    if failed < 5:  # Only show first few errors
                        print(f"\n   ⚠️  {symbol}: {str(e)[:50]}")
            
            print(f"\n   ✅ Exported {success} symbols, {failed} failed")
            
        except Exception as e:
            print(f"   ❌ OHLCV export failed: {e}")
        
        # Export resampled data summary
        print("\n4️⃣  Exporting resampled data summary...")
        try:
            # Just export metadata about what exists
            resampled_summary = conn.execute("""
                SELECT 
                    instrument_key,
                    timeframe,
                    MIN(timestamp) as first_date,
                    MAX(timestamp) as last_date,
                    COUNT(*) as candles
                FROM ohlcv_resampled
                GROUP BY instrument_key, timeframe
            """).fetchdf()
            
            out_file = export_path / "resampled_summary.csv"
            resampled_summary.to_csv(out_file, index=False)
            print(f"   ✅ Saved summary → {out_file.name}")
        except Exception as e:
            print(f"   ❌ Failed: {e}")
        
        conn.close()
        
        print("\n" + "=" * 80)
        print("📊 EXPORT SUMMARY")
        print("=" * 80)
        print(f"\n✅ Data exported to: {export_path}")
        
        # Calculate total size
        total_size = sum(f.stat().st_size for f in export_path.rglob('*') if f.is_file())
        print(f"📦 Total size: {total_size / (1024**2):.1f} MB")
        
        print("\n💡 WHAT TO DO WITH EXPORTED DATA:")
        print("   1. Copy to safe location (external drive)")
        print("   2. Use instruments.parquet to rebuild database")
        print("   3. Re-fetch recent OHLCV data (faster than recovery)")
        print("   4. Or use ohlcv_1m/*.parquet to import into new database")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Export failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("\n⚠️  This will export critical data to CSV/Parquet files")
    print("⚠️  Last 30 days of OHLCV data will be exported")
    print("⚠️  Ensure you have ~500 MB free disk space")
    print()
    
    response = input("Continue with export? (yes/no): ")
    
    if response.lower() in ['yes', 'y']:
        export_critical_data()
    else:
        print("\nExport cancelled.")