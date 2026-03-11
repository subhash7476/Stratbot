import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

from core.database.manager import DatabaseManager
from core.database.utils.symbol_utils import resolve_to_instrument_key
from core.analytics.day_features import compute_session_features, finalize_dataframe

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def get_nifty_50_keys():
    csv_path = ROOT / "data" / "nifty-50-stock-list.csv"
    symbols = pd.read_csv(csv_path)['Symbol'].tolist()
    keys = [resolve_to_instrument_key(s, db_path=str(ROOT / "data")) for s in symbols]
    return [k for k in keys if k]

def main():
    dm = DatabaseManager(ROOT / "data")
    keys = get_nifty_50_keys()
    
    # Process all years 2023-2026
    years = [2023, 2024, 2025, 2026]
    
    # We will process stock by stock to handle rolling features correctly
    for key in keys:
        logger.info(f"Processing {key}...")
        stock_rows = []
        
        # Load all dates for this stock
        for year in years:
            # We'll use a more efficient query: get all data for this symbol across the year
            # Actually, historical_reader is per date. We must loop dates.
            pass
            
        # Optimization: historical_reader is a context manager for a file.
        # It's better to open the file once and get all symbols, then store them.
        
    # Real Plan: 
    # 1. Loop through every date. 
    # 2. Extract 1m candles for all 50 stocks. 
    # 3. Compute session features. 
    # 4. Group by symbol and apply finalize_dataframe.
    
    all_data = []
    
    start_date = date(2023, 1, 1)
    end_date = date(2026, 2, 22)
    current_dt = start_date
    
    while current_dt <= end_date:
        try:
            with dm.historical_reader("nse", "candles", "1m", current_dt) as conn:
                placeholders = ','.join(['?'] * len(keys))
                query = f"SELECT * FROM candles WHERE symbol IN ({placeholders}) ORDER BY symbol, timestamp"
                df = conn.execute(query, keys).df()
                
                if not df.empty:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    for symbol, group in df.groupby('symbol'):
                        group = group.sort_values('timestamp').reset_index(drop=True)
                        if len(group) < 375: continue
                        
                        f = compute_session_features(group)
                        if f:
                            f['date'] = current_dt.isoformat()
                            f['symbol'] = symbol
                            
                            # 9:30 AM Entry Checkpoint (Bar 15)
                            # We'll add these to the flat dict
                            opens = group['open'].values
                            closes = group['close'].values
                            highs = group['high'].values
                            lows = group['low'].values
                            
                            f['c930_ret'] = (closes[15] - opens[0]) / opens[0]
                            f['c930_range'] = (np.max(highs[:16]) - np.min(lows[:16])) / opens[0]
                            f['trade_ret_930_1330'] = (closes[255] / closes[15] - 1)
                            
                            all_data.append(f)
        except:
            pass
        
        current_dt += timedelta(days=1)
        if (current_dt.day == 1):
            logger.info(f"Reached {current_dt}")

    if all_data:
        df_all = pd.DataFrame(all_data)
        
        # Apply Block A / Rolling features PER SYMBOL
        final_dfs = []
        for symbol, group in df_all.groupby('symbol'):
            group = group.sort_values('date')
            final_dfs.append(finalize_dataframe(group))
            
        df_final = pd.concat(final_dfs).sort_values(['date', 'symbol'])
        output_path = ROOT / "data" / "features" / "day_type" / "stocks_comprehensive_2023_2026.csv"
        df_final.to_csv(output_path, index=False)
        logger.info(f"Saved {len(df_final)} rows to {output_path}")

if __name__ == "__main__":
    main()
