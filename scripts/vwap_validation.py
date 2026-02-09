"""
Validation script for Session VWAP implementation.
This script:
1. Loads 1 trading day of 1m bars for one symbol
2. Computes Session VWAP with the new code
3. Writes CSV: timestamp, open, high, low, close, volume, vwap, aboveVWAP
4. Provides 5-10 timestamps to manually compare against TradingView
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import os
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).parent
import sys
sys.path.insert(0, str(ROOT))

from core.analytics.indicators.vwap import VWAP
from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.database.utils import MarketSession


def load_one_day_of_data(symbol: str, date_str: str):
    """
    Load 1 day of 1-minute bars for a symbol.
    """
    ist_tz = pytz.timezone('Asia/Kolkata')
    target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    
    # Start at market open (9:15 AM IST) and end at market close (3:30 PM IST)
    start_time = ist_tz.localize(datetime.combine(target_date, datetime.min.time().replace(hour=9, minute=15)))
    end_time = ist_tz.localize(datetime.combine(target_date, datetime.min.time().replace(hour=15, minute=30)))
    
    db = DatabaseManager()
    query = MarketDataQuery(db)
    
    df = query.get_ohlcv(symbol, start_time, end_time, timeframe='1m')
    
    if df.empty:
        return df

    # Ensure columns match expected legacy names
    if 'symbol' in df.columns:
        df = df.rename(columns={'symbol': 'instrument_key'})
    
    return df


def validate_vwap_implementation(symbol: str, date_str: str):

    """
    Validate the VWAP implementation by computing Session VWAP for a day of data.
    
    Args:
        symbol: Symbol to validate
        date_str: Date to validate in 'YYYY-MM-DD' format
        db_path: Path to the database
    """
    print(f"Loading 1 day of data for {symbol} on {date_str}...")
    
    # Load the data
    df = load_one_day_of_data(symbol, date_str, db_path)
    
    if df.empty:
        print(f"No data found for {symbol} on {date_str}")
        return
    
    print(f"Loaded {len(df)} 1-minute bars")
    
    # Compute Session VWAP
    print("Computing Session VWAP...")
    vwap_calculator = VWAP()
    result_df = vwap_calculator.calculate(df, anchor="Session", market="NSE", timestamp_col="timestamp")
    
    # Select 5-10 key timestamps for manual comparison
    sample_indices = np.linspace(0, len(result_df)-1, min(10, len(result_df)), dtype=int)
    sample_df = result_df.iloc[sample_indices].copy()
    
    # Print sample data for manual verification
    print("\nSample VWAP values for manual comparison:")
    print("Timestamp\t\tOpen\tHigh\tLow\tClose\tVolume\tVWAP\tAboveVWAP\tBelowVWAP")
    print("-" * 120)
    for _, row in sample_df.iterrows():
        ts_str = row['timestamp'].strftime('%H:%M:%S') if hasattr(row['timestamp'], 'strftime') else str(row['timestamp'])
        print(f"{ts_str}\t{row['open']:.2f}\t{row['high']:.2f}\t{row['low']:.2f}\t{row['close']:.2f}\t{row['volume']:.0f}\t{row['vwap']:.2f}\t{row['aboveVWAP']}\t{row['belowVWAP']}")
    
    # Write full results to CSV
    output_file = f"vwap_validation_{symbol}_{date_str.replace('-', '')}.csv"
    result_df.to_csv(output_file, index=False)
    print(f"\nFull VWAP results saved to: {output_file}")
    
    # Additional validation: Check that VWAP resets daily
    print("\nValidating VWAP reset behavior...")
    
    # Check if VWAP starts fresh each day by looking at early bars
    early_bars = result_df.head(10)
    if not early_bars.empty:
        print(f"Early VWAP values: {early_bars['vwap'].head(5).tolist()}")
    
    # Count session vs non-session bars
    session_mask = result_df['timestamp'].apply(lambda x: MarketSession.is_in_session(x, "NSE"))
    session_bars = session_mask.sum()
    total_bars = len(result_df)
    print(f"Total bars: {total_bars}, Session bars: {session_bars}, Non-session bars: {total_bars - session_bars}")
    
    return result_df


if __name__ == "__main__":
    # Example usage
    # You can change these parameters to validate different symbols/dates
    SYMBOL = "NSE_EQ|INE001A01034"  # Example symbol - replace with actual symbol
    DATE_STR = "2026-01-29"  # Example date - replace with actual trading date
    
    # Validate the VWAP implementation
    result = validate_vwap_implementation(SYMBOL, DATE_STR)
    
    if result is not None:
        print(f"\nValidation complete! Check the output CSV for detailed results.")
        print(f"Compare the VWAP values at key timestamps with TradingView for accuracy.")
    else:
        print(f"\nValidation failed - no data found for {SYMBOL} on {DATE_STR}")
        print("Please ensure you have 1-minute bar data for the specified symbol and date in your database.")