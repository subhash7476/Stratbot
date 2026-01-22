#!/usr/bin/env python3
"""
Emergency: Fast Instruments Download
=====================================
Fast bulk insert version with NaN handling.
Completes in ~30 seconds instead of 15+ minutes.

Usage:
    python fast_download_instruments.py

Author: Trading Bot Pro
"""

import sys
from pathlib import Path
import requests
import gzip
import json
import pandas as pd
from datetime import datetime
import numpy as np

# Setup paths
ROOT = Path(__file__).parent.parent if Path(__file__).parent.name == "scripts" else Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core.database import TradingDB
from core.config import get_access_token

def main():
    print("="*70)
    print("Fast Instruments Download")
    print("="*70)
    print()
    
    # Connect to database
    print("📊 Connecting to database...")
    db = TradingDB()
    
    # Check current state
    current_count = db.con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
    print(f"Current instruments in database: {current_count:,}")
    
    if current_count > 100000:
        response = input(f"\n⚠️  Database already has {current_count:,} instruments. Re-download? (yes/no): ")
        if response.lower() != 'yes':
            print("❌ Cancelled")
            return
    
    print()
    print("📡 Downloading instruments from Upstox API...")
    
    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
    headers = {"Accept": "application/json"}
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            print(f"❌ Download failed: HTTP {response.status_code}")
            return
        
        print("✅ Download complete")
        
        # Decompress
        print("📦 Decompressing...")
        decompressed = gzip.decompress(response.content)
        data = decompressed.decode('utf-8')
        
        # Parse JSON
        print("🔍 Parsing instruments...")
        instruments = json.loads(data)
        print(f"📊 Found {len(instruments):,} instruments from API")
        
        # Convert to DataFrame
        print("🔄 Converting to database format...")
        df = pd.DataFrame(instruments)
        
        # Map columns
        column_mapping = {
            'instrument_key': 'instrument_key',
            'trading_symbol': 'trading_symbol',
            'name': 'name',
            'instrument_type': 'instrument_type',
            'exchange': 'exchange',
            'segment': 'segment',
            'lot_size': 'lot_size',
            'tick_size': 'tick_size',
            'expiry': 'expiry',
            'strike_price': 'strike_price'
        }
        
        df_clean = pd.DataFrame()
        
        for api_col, db_col in column_mapping.items():
            if api_col in df.columns:
                df_clean[db_col] = df[api_col]
            else:
                df_clean[db_col] = None
        
        # CRITICAL: Replace NaN/inf with None for nullable fields
        print("🔧 Cleaning NaN/NULL values...")
        
        # Numeric fields that can be NULL
        nullable_numeric = ['lot_size', 'tick_size', 'strike_price']
        for col in nullable_numeric:
            if col in df_clean.columns:
                # Replace NaN and inf with None
                df_clean[col] = df_clean[col].replace([np.nan, np.inf, -np.inf], None)
        
        # String fields - replace NaN with None
        string_fields = ['name', 'instrument_type', 'exchange', 'segment']
        for col in string_fields:
            if col in df_clean.columns:
                df_clean[col] = df_clean[col].replace([np.nan], None)
        
        # Convert expiry
        if 'expiry' in df_clean.columns and df_clean['expiry'].notna().any():
            try:
                # Replace NaN first
                df_clean['expiry'] = df_clean['expiry'].replace([np.nan], None)
                # Convert non-null values
                mask = df_clean['expiry'].notna()
                if mask.any():
                    df_clean.loc[mask, 'expiry'] = pd.to_datetime(
                        df_clean.loc[mask, 'expiry'], 
                        unit='ms', 
                        errors='coerce'
                    ).dt.date
            except Exception as e:
                print(f"   ⚠️  Expiry conversion warning: {e}")
                df_clean['expiry'] = None
        
        # Add timestamp
        df_clean['last_updated'] = datetime.now()
        
        # Remove duplicates
        print("🔍 Removing duplicates...")
        before_dedup = len(df_clean)
        df_clean = df_clean.drop_duplicates(subset=['instrument_key'], keep='first')
        after_dedup = len(df_clean)
        
        if before_dedup != after_dedup:
            print(f"   Removed {before_dedup - after_dedup:,} duplicate instrument_keys")
        
        # Filter NULL trading symbols
        print("🔍 Filtering invalid records...")
        before_filter = len(df_clean)
        df_clean = df_clean[df_clean['trading_symbol'].notna() & (df_clean['trading_symbol'] != '')]
        after_filter = len(df_clean)
        
        if before_filter != after_filter:
            print(f"   Filtered out {before_filter - after_filter:,} instruments with missing trading_symbol")
        
        # Ensure column order
        final_columns = [
            'instrument_key',
            'trading_symbol', 
            'name',
            'instrument_type',
            'exchange',
            'segment',
            'lot_size',
            'tick_size',
            'expiry',
            'strike_price',
            'last_updated'
        ]
        
        df_clean = df_clean[final_columns]
        
        print(f"✅ Prepared {len(df_clean):,} valid instruments")
        
        # Clear existing data
        print()
        print("🗑️  Clearing existing instruments...")
        db.con.execute("DELETE FROM instruments")
        
        # Bulk insert using DuckDB's fast path
        print("💾 Bulk inserting instruments (fast method)...")
        print("   This should take ~10-30 seconds...")
        
        # Use DuckDB's efficient DataFrame insert
        db.con.execute("""
            INSERT INTO instruments 
            SELECT * FROM df_clean
        """)
        
        # Verify
        final_count = db.con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
        
        print()
        print("="*70)
        print("✅ Download Complete!")
        print("="*70)
        print(f"📊 Instruments in database: {final_count:,}")
        
        # Show breakdown
        print()
        print("📊 Breakdown by Segment:")
        segments = db.con.execute("""
            SELECT segment, COUNT(*) as count
            FROM instruments
            GROUP BY segment
            ORDER BY count DESC
            LIMIT 10
        """).df()
        
        for _, row in segments.iterrows():
            print(f"   • {row['segment']:<15} {row['count']:>8,}")
        
        print()
        print("📌 Next Steps:")
        print("  1. Run migration to create F&O master table:")
        print("     python migrate_add_fo_stocks_table.py")
        print("  2. All pages should now work correctly")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        db.close()

if __name__ == "__main__":
    main()