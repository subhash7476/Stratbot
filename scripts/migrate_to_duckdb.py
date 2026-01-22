"""
Migration Script: Parquet → DuckDB
Transfers existing data from partitioned Parquet files to DuckDB database
"""

import sys
import os
from pathlib import Path
import pandas as pd
from datetime import datetime
from tqdm import tqdm

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database import TradingDB


class ParquetMigrator:
    """
    Migrates data from Parquet files to DuckDB.
    Handles instruments, 1m data, and derived data.
    """
    
    def __init__(self, data_dir: Path = None):
        """
        Initialize migrator.
        
        Args:
            data_dir: Path to data directory (default: ROOT/data)
        """
        if data_dir is None:
            data_dir = ROOT / "data"
        
        self.data_dir = Path(data_dir)
        self.db = TradingDB()
        
        print(f"📂 Data directory: {self.data_dir}")
        print(f"🗄️  Database: {self.db.db_path}")
    
    # ========================================================================
    # MIGRATE INSTRUMENTS
    # ========================================================================
    
    def migrate_instruments(self):
        """
        Migrate instrument files to database.
        Looks for: instruments/all_instruments.parquet and segment files
        """
        print("\n" + "="*70)
        print("📋 MIGRATING INSTRUMENTS")
        print("="*70)
        
        # Try multiple possible locations
        possible_dirs = [
            self.data_dir / "instruments",      # data/instruments/
            self.data_dir.parent / "instruments"  # root/instruments/
        ]
        
        instruments_dir = None
        for dir_path in possible_dirs:
            if dir_path.exists():
                instruments_dir = dir_path
                print(f"\n📂 Found instruments at: {instruments_dir}")
                break
        
        if instruments_dir is None:
            print("⚠️  No instruments directory found. Tried:")
            for dir_path in possible_dirs:
                print(f"   - {dir_path}")
            print("Skipping instruments migration...")
            return
        
        # Migrate all_instruments.parquet
        all_file = instruments_dir / "all_instruments.parquet"
        if all_file.exists():
            print(f"\n📥 Loading: {all_file.name}")
            df = pd.read_parquet(all_file)
            print(f"   Found {len(df)} instruments")
            
            self.db.upsert_instruments(df)
        
        # Migrate segment-wise files
        segment_dir = instruments_dir / "segment_wise"
        if segment_dir.exists():
            for file in segment_dir.glob("*.parquet"):
                print(f"\n📥 Loading: segment_wise/{file.name}")
                df = pd.read_parquet(file)
                print(f"   Found {len(df)} instruments")
                
                self.db.upsert_instruments(df)
        
        print("\n✅ Instruments migration complete!")
    
    # ========================================================================
    # MIGRATE 1-MINUTE DATA
    # ========================================================================
    
    def migrate_1m_data(self, symbols: list = None, max_symbols: int = None):
        """
        Migrate 1-minute OHLCV data from partitioned Parquet files.
        Structure: data/stocks/{SYMBOL}/1minute/year=*/month=*/day=*/data.parquet
        
        Args:
            symbols: List of symbols to migrate (None = all)
            max_symbols: Limit number of symbols (for testing)
        """
        print("\n" + "="*70)
        print("📊 MIGRATING 1-MINUTE DATA")
        print("="*70)
        
        stocks_dir = self.data_dir / "stocks"
        
        if not stocks_dir.exists():
            print("⚠️  No stocks directory found. Skipping...")
            return
        
        # Get all symbol directories
        symbol_dirs = [d for d in stocks_dir.iterdir() if d.is_dir()]
        
        if symbols:
            symbol_dirs = [d for d in symbol_dirs if d.name in symbols]
        
        if max_symbols:
            symbol_dirs = symbol_dirs[:max_symbols]
        
        print(f"\n📂 Found {len(symbol_dirs)} symbols to migrate")
        
        for symbol_dir in tqdm(symbol_dirs, desc="Migrating symbols"):
            symbol = symbol_dir.name
            minute_dir = symbol_dir / "1minute"
            
            if not minute_dir.exists():
                continue
            
            # Find all partition files
            partition_files = list(minute_dir.rglob("data.parquet"))
            
            if not partition_files:
                print(f"⚠️  No data files for {symbol}")
                continue
            
            # Load all partitions into one DataFrame
            dfs = []
            for file in partition_files:
                try:
                    df = pd.read_parquet(file)
                    
                    # Debug: Show structure of first file
                    if len(dfs) == 0:
                        print(f"  📋 Columns: {list(df.columns)}")
                        print(f"  📋 Index: {df.index.name if hasattr(df.index, 'name') else 'no name'}")
                    
                    dfs.append(df)
                except Exception as e:
                    print(f"❌ Error reading {file}: {e}")
            
            if dfs:
                # Combine all partitions
                full_df = pd.concat(dfs, ignore_index=False)  # Keep index if it's timestamp
                
                # Ensure timestamp is a column, not index
                if 'timestamp' not in full_df.columns:
                    if full_df.index.name == 'timestamp':
                        full_df.reset_index(inplace=True)
                    elif isinstance(full_df.index, pd.DatetimeIndex):
                        full_df['timestamp'] = full_df.index
                        full_df.reset_index(drop=True, inplace=True)
                    else:
                        print(f"⚠️  No timestamp found for {symbol}, skipping")
                        continue
                
                # Sort by timestamp
                full_df.sort_values('timestamp', inplace=True)
                
                # Standardize column names (your data might have 'Open' or 'open')
                column_mapping = {}
                for col in full_df.columns:
                    lower_col = col.lower()
                    if lower_col in ['open', 'high', 'low', 'close', 'volume', 'oi']:
                        if col != lower_col:  # Only map if it's different
                            column_mapping[col] = lower_col
                
                if column_mapping:
                    full_df.rename(columns=column_mapping, inplace=True)
                    print(f"  📝 Standardized columns: {column_mapping}")
                
                # Get instrument_key from instruments table using trading_symbol
                # The folder name (symbol) should match trading_symbol field
                instruments = self.db.con.execute("""
                    SELECT instrument_key
                    FROM instruments
                    WHERE trading_symbol = ?
                      AND segment = 'NSE_EQ'
                    LIMIT 1
                """, [symbol]).df()
                
                if instruments.empty:
                    # Try NSE_FO
                    instruments = self.db.con.execute("""
                        SELECT instrument_key
                        FROM instruments
                        WHERE trading_symbol = ?
                          AND segment = 'NSE_FO'
                        LIMIT 1
                    """, [symbol]).df()
                
                if instruments.empty:
                    # Try any segment
                    instruments = self.db.con.execute("""
                        SELECT instrument_key
                        FROM instruments
                        WHERE trading_symbol = ?
                        LIMIT 1
                    """, [symbol]).df()
                
                if instruments.empty:
                    # Use default format
                    instrument_key = f"NSE_EQ|{symbol}"
                    print(f"⚠️  Instrument key not found for {symbol}, using default: {instrument_key}")
                else:
                    instrument_key = instruments.iloc[0]['instrument_key']
                
                # Insert into database
                self.db.upsert_ohlcv_1m(full_df, instrument_key)
                
                print(f"✅ {symbol}: {len(full_df)} candles migrated")
        
        print("\n✅ 1-minute data migration complete!")
    
    # ========================================================================
    # MIGRATE DERIVED DATA
    # ========================================================================
    
    def migrate_derived_data(self, symbols: list = None, 
                            timeframes: list = None, max_symbols: int = None):
        """
        Migrate derived (resampled) data.
        Structure: data/derived/{SYMBOL}/{TIMEFRAME}/merged_*.parquet
        
        Args:
            symbols: List of symbols to migrate (None = all)
            timeframes: List of timeframes (None = all)
            max_symbols: Limit number of symbols
        """
        print("\n" + "="*70)
        print("⏱️  MIGRATING DERIVED DATA")
        print("="*70)
        
        derived_dir = self.data_dir / "derived"
        
        if not derived_dir.exists():
            print("⚠️  No derived directory found. Skipping...")
            return
        
        # Get all symbol directories
        symbol_dirs = [d for d in derived_dir.iterdir() if d.is_dir()]
        
        if symbols:
            symbol_dirs = [d for d in symbol_dirs if d.name in symbols]
        
        if max_symbols:
            symbol_dirs = symbol_dirs[:max_symbols]
        
        print(f"\n📂 Found {len(symbol_dirs)} symbols to migrate")
        
        for symbol_dir in tqdm(symbol_dirs, desc="Migrating derived data"):
            symbol = symbol_dir.name
            
            # Get instrument key using trading_symbol
            instruments = self.db.con.execute("""
                SELECT instrument_key
                FROM instruments
                WHERE trading_symbol = ?
                  AND segment = 'NSE_EQ'
                LIMIT 1
            """, [symbol]).df()
            
            if instruments.empty:
                # Try NSE_FO
                instruments = self.db.con.execute("""
                    SELECT instrument_key
                    FROM instruments
                    WHERE trading_symbol = ?
                      AND segment = 'NSE_FO'
                    LIMIT 1
                """, [symbol]).df()
            
            if instruments.empty:
                # Default
                instrument_key = f"NSE_EQ|{symbol}"
            else:
                instrument_key = instruments.iloc[0]['instrument_key']
            
            # Process each timeframe
            for tf_dir in symbol_dir.iterdir():
                if not tf_dir.is_dir():
                    continue
                
                timeframe = tf_dir.name
                
                if timeframes and timeframe not in timeframes:
                    continue
                
                # Find merged file
                merged_files = list(tf_dir.glob("merged_*.parquet"))
                
                if not merged_files:
                    continue
                
                for file in merged_files:
                    try:
                        df = pd.read_parquet(file)
                        
                        # Prepare data
                        if 'timestamp' not in df.columns and df.index.name == 'timestamp':
                            df.reset_index(inplace=True)
                        
                        # Standardize columns
                        df.columns = df.columns.str.lower()
                        
                        # Add required columns
                        df['instrument_key'] = instrument_key
                        df['timeframe'] = timeframe
                        
                        if 'oi' not in df.columns:
                            df['oi'] = 0
                        
                        # Select only needed columns
                        cols = ['instrument_key', 'timeframe', 'timestamp', 
                               'open', 'high', 'low', 'close', 'volume', 'oi']
                        df = df[[c for c in cols if c in df.columns]]
                        
                        # Insert
                        self.db.con.execute("""
                            INSERT OR REPLACE INTO ohlcv_resampled 
                            SELECT * FROM df
                        """)
                        
                        print(f"✅ {symbol}/{timeframe}: {len(df)} candles migrated")
                        
                    except Exception as e:
                        print(f"❌ Error migrating {file}: {e}")
        
        print("\n✅ Derived data migration complete!")
    
    # ========================================================================
    # FULL MIGRATION
    # ========================================================================
    
    def migrate_all(self, symbols: list = None, max_symbols: int = None,
                   skip_instruments: bool = False,
                   skip_1m: bool = False,
                   skip_derived: bool = False):
        """
        Run complete migration process.
        
        Args:
            symbols: Specific symbols to migrate (None = all)
            max_symbols: Limit number of symbols (for testing)
            skip_instruments: Skip instrument migration
            skip_1m: Skip 1-minute data migration
            skip_derived: Skip derived data migration
        """
        print("\n" + "="*70)
        print("🚀 STARTING FULL MIGRATION")
        print("="*70)
        print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        start_time = datetime.now()
        
        # Step 1: Instruments
        if not skip_instruments:
            self.migrate_instruments()
        
        # Step 2: 1-minute data
        if not skip_1m:
            self.migrate_1m_data(symbols=symbols, max_symbols=max_symbols)
        
        # Step 3: Derived data
        if not skip_derived:
            self.migrate_derived_data(symbols=symbols, max_symbols=max_symbols)
        
        # Optimize database
        print("\n🔧 Optimizing database...")
        self.db.vacuum()
        
        # Summary
        duration = datetime.now() - start_time
        print("\n" + "="*70)
        print("✅ MIGRATION COMPLETE!")
        print("="*70)
        print(f"Duration: {duration}")
        print(f"Database size: {self.db.db_path.stat().st_size / (1024**2):.2f} MB")
        
        # Show table stats
        print("\n📊 Database Statistics:")
        tables = ['instruments', 'ohlcv_1m', 'ohlcv_resampled']
        for table in tables:
            count = self.db.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  - {table}: {count:,} rows")
    
    def close(self):
        """Close database connection"""
        self.db.close()


# ============================================================================
# CLI INTERFACE
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Migrate Parquet data to DuckDB")
    parser.add_argument("--symbols", nargs="+", help="Specific symbols to migrate")
    parser.add_argument("--max-symbols", type=int, help="Limit number of symbols")
    parser.add_argument("--skip-instruments", action="store_true", help="Skip instruments")
    parser.add_argument("--skip-1m", action="store_true", help="Skip 1-minute data")
    parser.add_argument("--skip-derived", action="store_true", help="Skip derived data")
    parser.add_argument("--test", action="store_true", help="Test mode (5 symbols only)")
    
    args = parser.parse_args()
    
    # Test mode
    if args.test:
        args.max_symbols = 5
        print("🧪 TEST MODE: Migrating only 5 symbols")
    
    # Run migration
    migrator = ParquetMigrator()
    
    try:
        migrator.migrate_all(
            symbols=args.symbols,
            max_symbols=args.max_symbols,
            skip_instruments=args.skip_instruments,
            skip_1m=args.skip_1m,
            skip_derived=args.skip_derived
        )
    finally:
        migrator.close()
    
    print("\n✅ Done! You can now use the DuckDB database.")
    print(f"📍 Location: {migrator.db.db_path}")