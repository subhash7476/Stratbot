#!/usr/bin/env python3
"""
Migration Script: Add F&O Stocks Master Table
==============================================
Adds the fo_stocks_master table to existing database and populates it.

Usage:
    python migrate_add_fo_stocks_table.py

Author: Trading Bot Pro
"""

import sys
from pathlib import Path

# Setup paths
ROOT = Path(__file__).parent.parent if Path(__file__).parent.name == "scripts" else Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core.database import TradingDB
from datetime import datetime

def main():
    print("="*60)
    print("Migration: Add F&O Stocks Master Table")
    print("="*60)
    print()
    
    # Connect to database
    print("📊 Connecting to database...")
    db = TradingDB()
    
    # Check if table already exists
    tables = db.con.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = 'fo_stocks_master'
    """).fetchall()
    
    if tables:
        print("ℹ️  Table 'fo_stocks_master' already exists")
        response = input("Do you want to recreate it? (yes/no): ")
        if response.lower() != 'yes':
            print("❌ Migration cancelled")
            return
        
        print("🗑️  Dropping existing table...")
        db.con.execute("DROP TABLE fo_stocks_master")
    
    # Create table
    print("📝 Creating fo_stocks_master table...")
    
    db.con.execute("""
        CREATE TABLE fo_stocks_master (
            trading_symbol VARCHAR PRIMARY KEY,
            instrument_key VARCHAR NOT NULL,
            name VARCHAR,
            lot_size INTEGER,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
        )
    """)
    
    db.con.execute("""
        CREATE INDEX idx_fo_stocks_symbol
        ON fo_stocks_master(trading_symbol)
    """)
    
    db.con.execute("""
        CREATE INDEX idx_fo_stocks_active
        ON fo_stocks_master(is_active)
    """)
    
    print("✅ Table created successfully")
    
    # Populate with current F&O stocks
    print()
    print("🔍 Finding F&O stocks from instruments table...")
    
    query = """
    WITH fo_instruments AS (
        -- Step 1: Get F&O instruments with lot_size from NSE_FO
        SELECT DISTINCT 
            name,
            MAX(lot_size) as fo_lot_size  -- Use MAX to get the correct lot size
        FROM instruments
        WHERE segment = 'NSE_FO'
          AND instrument_type = 'FUT'
          AND name IS NOT NULL
          AND name != ''
        GROUP BY name
    ),
    ranked_instruments AS (
        -- Step 2: Match to NSE_EQ and get trading symbols
        SELECT 
            i.trading_symbol,
            i.instrument_key,
            i.name,
            f.fo_lot_size as lot_size,  -- Use lot_size from FO segment
            ROW_NUMBER() OVER (PARTITION BY i.trading_symbol ORDER BY i.instrument_key) as rn
        FROM instruments i
        JOIN fo_instruments f ON i.name = f.name
        WHERE i.segment = 'NSE_EQ'
          AND i.trading_symbol IS NOT NULL
          AND i.trading_symbol != ''
    )
    -- Step 3: Keep only first occurrence of each symbol
    SELECT 
        trading_symbol,
        instrument_key,
        name,
        lot_size
    FROM ranked_instruments
    WHERE rn = 1
    ORDER BY trading_symbol
    """
    
    fo_stocks = db.con.execute(query).df()
    
    if fo_stocks.empty:
        print("⚠️  No F&O stocks found in instruments table")
        print("💡 Please download instruments first (Page 1)")
        return
    
    print(f"📊 Found {len(fo_stocks)} unique F&O stocks")
    
    # Verify lot sizes look correct (should be > 1)
    avg_lot_size = fo_stocks['lot_size'].mean()
    print(f"   Average lot size: {avg_lot_size:.0f} (should be 100-1000 range)")
    
    if avg_lot_size < 10:
        print("   ⚠️  WARNING: Lot sizes look too small! Check data quality.")
    
    # Check for duplicates (should be none now, but verify)
    duplicates = fo_stocks['trading_symbol'].duplicated().sum()
    if duplicates > 0:
        print(f"⚠️  Warning: {duplicates} duplicate trading symbols found, keeping first occurrence")
        fo_stocks = fo_stocks.drop_duplicates(subset=['trading_symbol'], keep='first')
    
    # Insert into table
    print("💾 Populating fo_stocks_master table...")
    
    now = datetime.now()
    
    for _, row in fo_stocks.iterrows():
        db.con.execute("""
            INSERT INTO fo_stocks_master
            (trading_symbol, instrument_key, name, lot_size, last_updated, is_active)
            VALUES (?, ?, ?, ?, ?, TRUE)
        """, [row['trading_symbol'], row['instrument_key'], 
              row['name'], row['lot_size'], now])
    
    # Verify
    count = db.con.execute("SELECT COUNT(*) FROM fo_stocks_master").fetchone()[0]
    
    print()
    print("="*60)
    print("✅ Migration Complete!")
    print("="*60)
    print(f"📊 F&O Stocks Master: {count} stocks")
    print()
    
    # Show sample
    print("Sample F&O stocks:")
    sample = db.con.execute("""
        SELECT trading_symbol, name, lot_size
        FROM fo_stocks_master
        ORDER BY trading_symbol
        LIMIT 10
    """).df()
    
    for _, row in sample.iterrows():
        print(f"  • {row['trading_symbol']:<12} {row['name']:<30} (Lot: {row['lot_size']})")
    
    if count > 10:
        print(f"  ... and {count - 10} more")
    
    print()
    print("📌 Next Steps:")
    print("  1. This table will auto-update when you refresh instruments (Page 1)")
    print("  2. All pages (2, 9, etc.) can now use this table")
    print("  3. No more CSV uploads needed!")
    
    db.close()

if __name__ == "__main__":
    main()