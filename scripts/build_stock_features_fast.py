import argparse
import logging
import sys
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict

import pandas as pd
import numpy as np

# Path setup
ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

from core.database.manager import DatabaseManager
from core.database.utils.symbol_utils import resolve_to_instrument_key

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def compute_light_features(group: pd.DataFrame) -> Dict:
    """Core discriminative features used in Nifty DayType model."""
    if len(group) < 375: return {}
    
    # Pre-calculate
    opens = group['open'].values
    highs = group['high'].values
    lows = group['low'].values
    closes = group['close'].values
    
    full_day_ret = (closes[-1] - opens[0]) / opens[0]
    day_high = np.max(highs)
    day_low = np.min(lows)
    day_range = (day_high - day_low) / opens[0]
    
    # CLV
    clv = ((closes[-1] - day_low) - (day_high - closes[-1])) / (day_high - day_low) if day_high > day_low else 0
    
    # Linreg R2
    x = np.arange(len(closes))
    r2 = np.corrcoef(x, closes)[0, 1]**2
    
    # Flip Count
    rets_15m = closes[::15][1:] / closes[::15][:-1] - 1
    flips = np.sum(np.diff(np.sign(rets_15m)) != 0) / len(rets_15m)
    
    # --- 13:00 PM Checkpoint (Bar 225) ---
    c1300_open = opens[0]
    c1300_close = closes[225]
    c1300_high = np.max(highs[:226])
    c1300_low = np.min(lows[:226])
    
    c1300_ret = (c1300_close - c1300_open) / c1300_open
    c1300_range = (c1300_high - c1300_low) / c1300_open
    c1300_close_loc = (c1300_close - c1300_low) / (c1300_high - c1300_low) if c1300_high > c1300_low else 0.5
    
    # Target Trade Return: 13:00 PM to 15:30 PM (Close)
    target_ret = (closes[-1] / closes[225] - 1)
    
    return {
        'full_day_ret': full_day_ret,
        'day_range': day_range,
        'clv': clv,
        'linreg_r2': r2,
        'flip_count': flips,
        'c_ret': c1300_ret,
        'c_range': c1300_range,
        'c_close_loc': c1300_close_loc,
        'target_ret': target_ret
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()
    
    dm = DatabaseManager(ROOT / "data")
    csv_path = ROOT / "data" / "nifty-50-stock-list.csv"
    symbols = pd.read_csv(csv_path)['Symbol'].tolist()
    keys = [resolve_to_instrument_key(s, db_path=str(ROOT / "data")) for s in symbols if resolve_to_instrument_key(s, db_path=str(ROOT / "data"))]
    
    start_date = date(args.year, 1, 1)
    end_date = date(args.year, 12, 31)
    
    all_rows = []
    current_dt = start_date
    
    # Store last close per symbol
    last_closes = {}
    
    while current_dt <= end_date:
        try:
            with dm.historical_reader("nse", "candles", "1m", current_dt) as conn:
                placeholders = ','.join(['?'] * len(keys))
                query = f"SELECT symbol, timestamp, open, high, low, close, volume FROM candles WHERE symbol IN ({placeholders})"
                df = conn.execute(query, keys).df()
                
                if not df.empty:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    
                    # --- NEW: 9:30 AM Market Breadth Features ---
                    m930_ts = datetime.combine(current_dt, datetime.strptime("09:30:00", "%H:%M:%S").time())
                    m915_ts = datetime.combine(current_dt, datetime.strptime("09:15:00", "%H:%M:%S").time())
                    
                    m930 = df[df['timestamp'] == m930_ts].copy()
                    m915 = df[df['timestamp'] == m915_ts].copy()
                    
                    m915_opens = m915.set_index('symbol')['open']
                    m930['open_0'] = m930['symbol'].map(m915_opens)
                    m930['ret_930'] = (m930['close'] / m930['open_0'] - 1)
                    
                    market_pct_pos = (m930['ret_930'] > 0).mean()
                    market_avg_ret = m930['ret_930'].mean()
                    # --------------------------------------------

                    for symbol, group in df.groupby('symbol'):
                        group = group.sort_values('timestamp').reset_index(drop=True)
                        f = compute_light_features(group)
                        if f:
                            f['date'] = current_dt.isoformat()
                            f['symbol'] = symbol
                            f['mkt_pct_pos_930'] = market_pct_pos
                            f['mkt_avg_ret_930'] = market_avg_ret
                            
                            # Gap and Prev Day Context
                            prev_close = last_closes.get(symbol)
                            if prev_close:
                                f['gap_pct'] = (group['open'].iloc[0] / prev_close - 1) * 100
                            else:
                                f['gap_pct'] = 0.0
                            
                            all_rows.append(f)
                            last_closes[symbol] = group['close'].iloc[-1]
        except:
            pass
        current_dt += timedelta(days=1)
        
    if all_rows:
        df_out = pd.DataFrame(all_rows)
        output_path = ROOT / "data" / "features" / "day_type" / f"stocks_fast_{args.year}.csv"
        df_out.to_csv(output_path, index=False)
        logger.info(f"Saved {len(df_out)} rows to {output_path}")

if __name__ == "__main__":
    main()
