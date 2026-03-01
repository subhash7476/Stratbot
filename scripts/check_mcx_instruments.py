#!/usr/bin/env python3
"""Check MCX instruments in database."""
from pathlib import Path
from core.database.manager import DatabaseManager

db = DatabaseManager(Path("data"))

print("=== Checking MCX Instruments ===\n")

with db.config_reader() as conn:
    # Check if MCX instruments exist
    rows = conn.execute("""
        SELECT symbol, trading_symbol, exchange, market_type, is_active
        FROM instrument_meta
        WHERE symbol LIKE 'MCX%'
        ORDER BY symbol
        LIMIT 30
    """).fetchall()

    if rows:
        print(f"Found {len(rows)} MCX instruments:\n")
        for symbol, trading_symbol, exchange, market_type, is_active in rows:
            status = "ACTIVE" if is_active else "INACTIVE"
            print(f"{symbol:30} -> {trading_symbol:20} ({exchange}/{market_type}) [{status}]")
    else:
        print("No MCX instruments found in database!")
        print("\nSearching for instruments 449534 and 451669...")

        # Search by instrument key
        rows = conn.execute("""
            SELECT symbol, trading_symbol, exchange, market_type, is_active
            FROM instrument_meta
            WHERE symbol LIKE '%449534%' OR symbol LIKE '%451669%'
        """).fetchall()

        if rows:
            print(f"\nFound {len(rows)} matching instruments:\n")
            for symbol, trading_symbol, exchange, market_type, is_active in rows:
                status = "ACTIVE" if is_active else "INACTIVE"
                print(f"{symbol:30} -> {trading_symbol:20} ({exchange}/{market_type}) [{status}]")
        else:
            print("No matching instruments found!")

print("\n=== Checking your market_universe.json symbols ===\n")

import json
with open("config/market_universe.json", "r") as f:
    config = json.load(f)
    print(f"Configured symbols: {config['symbols']}")

print("\n=== Recommendation ===")
print("Update market_universe.json with the exact symbol format from instrument_meta table.")
