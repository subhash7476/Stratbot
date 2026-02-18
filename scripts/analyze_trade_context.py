
import os
import sys
import logging
import json
import pandas as pd
import duckdb
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.analytics.indicators.atr import ATR
from core.analytics.resampler import resample_ohlcv

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TradeAnalyzer")

def analyze_specific_trade():
    """
    Reconstruct the specific trade on 2025-06-02 for INE205A01025.
    Calculate ATR and verify SL logic.
    """
    symbol = "NSE_EQ|INE205A01025"
    trade_time = datetime(2025, 6, 2, 9, 15)
    
    # 1. Fetch OHLCV Data - Extend end_time significantly
    db = DatabaseManager(Path("data"))
    query = MarketDataQuery(db)
    
    start_time = datetime(2025, 5, 25) 
    end_time = datetime(2025, 6, 5, 15, 30) # Extended to ensure June 2nd data
    
    logger.info(f"Fetching data for {symbol} around {trade_time}...")
    df_1m = query.get_ohlcv(symbol, start_time=start_time, end_time=end_time, timeframe="1m")
    
    if df_1m.empty:
        logger.error("No data found.")
        return
        
    logger.info(f"Data fetched. Range: {df_1m['timestamp'].min()} to {df_1m['timestamp'].max()}")

    # Resample to 15m
    try:
        df = resample_ohlcv(df_1m, "15m")
    except Exception as e:
        logger.error(f"Resampling failed: {e}")
        return

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    
    # 2. Calculate ATR
    df['atr'] = ATR(14).calculate(df)
    
    # 3. Get the specific bar
    try:
        row = df.loc[trade_time]
        prev_bar = df.iloc[df.index.get_loc(trade_time) - 1]
    except KeyError:
        logger.error(f"Bar at {trade_time} not found in data.")
        logger.info(f"Available nearby times: {df.index[(df.index >= datetime(2025, 6, 1)) & (df.index <= datetime(2025, 6, 3))]}")
        return

    # 4. Output Analysis
    entry_price = 429.10
    exit_price = 431.34
    
    atr_value = row['atr']
    prev_atr = prev_bar['atr']
    
    logger.info("\n" + "="*60)
    logger.info(f"TRADE ANALYSIS: {symbol} @ {trade_time}")
    logger.info("="*60)
    logger.info(f"Entry Price:   {entry_price}")
    logger.info(f"Exit Price:    {exit_price}")
    logger.info(f"Direction:     SHORT")
    logger.info("-" * 40)
    logger.info(f"ATR (14) at Trade Time: {atr_value:.4f}")
    logger.info(f"ATR (14) at Prev Bar:   {prev_atr:.4f}")
    logger.info("-" * 40)
    
    calculated_sl_CurrentATR = entry_price + (1.0 * atr_value)
    calculated_sl_PrevATR    = entry_price + (1.0 * prev_atr)
    
    actual_sl_dist = abs(exit_price - entry_price)
    
    logger.info(f"Calculated SL (Current ATR): {calculated_sl_CurrentATR:.2f} (Dist: {atr_value:.2f})")
    logger.info(f"Calculated SL (Prev ATR):    {calculated_sl_PrevATR:.2f} (Dist: {prev_atr:.2f})")
    logger.info(f"Actual Loss Distance:        {actual_sl_dist:.2f}")

    if abs(actual_sl_dist - atr_value) < 0.1:
        logger.info("\nCONCLUSION: SL was processed using Current Bar's ATR.")
    elif abs(actual_sl_dist - prev_atr) < 0.1:
        logger.info("\nCONCLUSION: SL was processed using Previous Bar's ATR.")
    else:
        logger.info(f"\nCONCLUSION: Discrepancy. Actual diff {actual_sl_dist:.2f} vs ATR {atr_value:.2f}")

    # Check immediate future bars
    future_bars = df.loc[trade_time:].iloc[1:10]
    logger.info("\nChecking subsequent bars for stop hit:")
    for ts, bar in future_bars.iterrows():
        hit_sl = bar['high'] >= calculated_sl_CurrentATR
        logger.info(f"  {ts}: High={bar['high']}, Low={bar['low']}, Close={bar['close']} | Hit SL({calculated_sl_CurrentATR:.2f})? {hit_sl}")
        if hit_sl:
            break

if __name__ == "__main__":
    analyze_specific_trade()
