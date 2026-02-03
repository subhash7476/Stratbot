#!/usr/bin/env python3
"""
Upstox Historical Data Fetcher
------------------------------
CLI script to fetch historical candle data from Upstox V3 API and store in DuckDB.
"""
import argparse
import json
import sys
import os
from datetime import datetime
from zoneinfo import ZoneInfo

# Add the root directory to the path so we can import core modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.api.upstox_client import UpstoxClient
from core.data.duckdb_client import db_cursor
from config.credentials import load_credentials


def validate_parameters(instrument_key: str, unit: str, interval: int, from_date: str, to_date: str):
    """Validate input parameters according to Upstox API rules."""
    
    # Validate instrument key format
    if '|' not in instrument_key:
        raise ValueError("Instrument key must be in format 'EXCHANGE_SEGMENT|ISIN'")
    
    # Validate unit and interval combinations
    if unit.lower() == 'minutes':
        if not (1 <= interval <= 300):
            raise ValueError("For minutes unit, interval must be between 1 and 300")
    elif unit.lower() == 'hours':
        if not (1 <= interval <= 5):
            raise ValueError("For hours unit, interval must be between 1 and 5")
    elif unit.lower() in ['days', 'weeks', 'months']:
        if interval != 1:
            raise ValueError(f"For {unit} unit, interval must be 1")
    else:
        raise ValueError("Unit must be one of: minutes, hours, days, weeks, months")
    
    # Validate date format (YYYY-MM-DD)
    try:
        datetime.strptime(from_date, '%Y-%m-%d')
        datetime.strptime(to_date, '%Y-%m-%d')
    except ValueError:
        raise ValueError("Dates must be in YYYY-MM-DD format")
    
    # Validate date range limits
    from_dt = datetime.strptime(from_date, '%Y-%m-%d')
    to_dt = datetime.strptime(to_date, '%Y-%m-%d')
    
    if from_dt > to_dt:
        raise ValueError("From date must be before or equal to to date")
    
    # Check date range limits based on unit
    date_diff = (to_dt - from_dt).days
    
    if unit.lower() == 'minutes':
        if date_diff > 30:  # Max 1 month for minute data
            raise ValueError("For minutes unit, date range cannot exceed 1 month")
    elif unit.lower() == 'hours':
        if date_diff > 90:  # Max 1 quarter for hour data
            raise ValueError("For hours unit, date range cannot exceed 1 quarter")
    elif unit.lower() == 'days':
        if date_diff > 3650:  # Max 10 years for day data
            raise ValueError("For days unit, date range cannot exceed 10 years")


def insert_candles_to_db(candles: list, instrument_key: str, unit: str, interval: int):
    """Insert fetched candles into DuckDB with deduplication."""
    if not candles:
        print("No candles to insert.")
        return 0

    inserted_count = 0
    duplicate_count = 0

    with db_cursor(read_only=False) as conn:
        for candle in candles:
            try:
                # Check if this record already exists to enforce idempotency
                existing = conn.execute("""
                    SELECT 1 FROM ohlcv_1m
                    WHERE instrument_key = ? AND timestamp = ?
                """, [
                    instrument_key,
                    candle['timestamp']
                ]).fetchone()

                if existing:
                    duplicate_count += 1
                    continue

                # Insert the candle data
                conn.execute("""
                    INSERT INTO ohlcv_1m
                    (instrument_key, timestamp, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, [
                    instrument_key,
                    candle['timestamp'],
                    candle['open'],
                    candle['high'],
                    candle['low'],
                    candle['close'],
                    candle['volume']
                ])
                inserted_count += 1

            except Exception as e:
                print(f"Error inserting candle for {instrument_key} at {candle['timestamp']}: {e}")
                continue

    return inserted_count


def main():
    parser = argparse.ArgumentParser(description='Fetch historical candle data from Upstox V3 API')
    parser.add_argument('--instrument_key', required=True, help='Instrument key in format "EXCHANGE_SEGMENT|ISIN"')
    parser.add_argument('--unit', required=True, choices=['minutes', 'hours', 'days', 'weeks', 'months'],
                       help='Time unit: minutes, hours, days, weeks, months')
    parser.add_argument('--interval', type=int, required=True, help='Interval number (e.g., 1, 5, 15)')
    parser.add_argument('--from', dest='from_date', required=True, help='Start date in YYYY-MM-DD format')
    parser.add_argument('--to', dest='to_date', required=True, help='End date in YYYY-MM-DD format')
    
    args = parser.parse_args()
    
    try:
        # Validate parameters
        validate_parameters(args.instrument_key, args.unit, args.interval, args.from_date, args.to_date)
        
        # Load credentials
        credentials = load_credentials()
        access_token = credentials.get('upstox', {}).get('access_token')
        
        if not access_token:
            print("Error: Upstox access token not found in credentials")
            sys.exit(1)
        
        # Initialize Upstox client
        client = UpstoxClient(access_token)
        
        print(f"Fetching historical data for {args.instrument_key}...")
        print(f"Timeframe: {args.unit} with interval {args.interval}")
        print(f"Date range: {args.from_date} to {args.to_date}")
        
        # Fetch historical candles
        candles = client.fetch_historical_candles_v3(
            instrument_key=args.instrument_key,
            unit=args.unit,
            interval=args.interval,
            to_date=args.to_date,
            from_date=args.from_date
        )
        
        print(f"Fetched {len(candles)} candles from Upstox API")
        
        # Insert into database
        inserted_count = insert_candles_to_db(candles, args.instrument_key, args.unit, args.interval)
        
        print(f"Successfully inserted {inserted_count} new candles into database")
        print(f"Skipped {len(candles) - inserted_count} duplicate candles (idempotency)")
        
    except ValueError as e:
        print(f"Validation error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()