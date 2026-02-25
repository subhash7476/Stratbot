import argparse
import logging
import sys
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd
import numpy as np
from scipy.stats import skew

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

from core.database.manager import DatabaseManager
from core.database.utils.symbol_utils import resolve_to_instrument_key

# ── Logging Setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
CHECKPOINT_BAR = 105        # 11:00 AM (9:15 + 105 mins)
OPEN_RANGE_END_BAR = 30     # first 30 minutes
EXPECTED_BARS = 375         # full session
MIN_SYMBOLS_REQUIRED = 40
START_DATE_STR = "2023-01-01"
END_DATE_STR = "2026-12-31"

SYMBOL_LIST_PATH = ROOT / "data" / "nifty-50-stock-list.csv"
OUTPUT_DIR = ROOT / "data" / "features" / "day_type"

# ── Implementation ───────────────────────────────────────────────────────────

def get_nifty_keys(dm: DatabaseManager) -> List[str]:
    """Load Nifty 50 symbols and resolve to instrument keys."""
    if not SYMBOL_LIST_PATH.exists():
        logger.error(f"Symbol list not found at {SYMBOL_LIST_PATH}")
        sys.exit(1)
    
    df = pd.read_csv(SYMBOL_LIST_PATH)
    symbols = df['Symbol'].tolist()
    
    keys = []
    db_path_str = str(ROOT / "data")
    for s in symbols:
        # Resolving symbol to key - ensure db_path is correct for config.db access
        key = resolve_to_instrument_key(s, db_path=db_path_str)
        if key:
            keys.append(key)
    
    logger.info(f"Resolved {len(keys)}/{len(symbols)} symbols to instrument keys.")
    return keys

def process_day(conn, keys: List[str], dt: date) -> Optional[Dict]:
    """Process a single day's data and compute cross-sectional features."""
    try:
        # Fetch data for all keys in one query
        placeholders = ','.join(['?'] * len(keys))
        query = f"""
            SELECT symbol, timestamp, open, high, low, close, volume 
            FROM candles 
            WHERE symbol IN ({placeholders})
            ORDER BY symbol, timestamp
        """
        df = conn.execute(query, keys).df()
        
        if df.empty:
            return None

        # Time filtering and indexing
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values(['symbol', 'timestamp'])
        
        valid_data = []
        
        for symbol, group in df.groupby('symbol'):
            if len(group) <= CHECKPOINT_BAR:
                continue
            
            # Extract bars 0 to 105 inclusive (106 bars)
            bars = group.iloc[:CHECKPOINT_BAR + 1].reset_index(drop=True)
            
            # Check for cumulative volume
            total_vol = bars['volume'].sum()
            if total_vol == 0:
                continue
                
            # Compute Symbol Features
            open_0 = bars.loc[0, 'open']
            close_105 = bars.loc[CHECKPOINT_BAR, 'close']
            high_105 = bars.loc[CHECKPOINT_BAR, 'high']
            low_105 = bars.loc[CHECKPOINT_BAR, 'low']
            
            # 1. Intraday Return (Close_105 vs Open_0)
            ret = (close_105 - open_0) / open_0 if open_0 != 0 else 0
            
            # 2. VWAP (True VWAP using 1m bars 0:105)
            vwap = (bars['close'] * bars['volume']).sum() / total_vol
            
            # 3. Opening Range (0 to 29 inclusive)
            opening_high = bars.loc[0:OPEN_RANGE_END_BAR-1, 'high'].max()
            opening_low = bars.loc[0:OPEN_RANGE_END_BAR-1, 'low'].min()
            
            valid_data.append({
                'ret': ret,
                'close_105': close_105,
                'vwap': vwap,
                'high_105': high_105,
                'low_105': low_105,
                'opening_high': opening_high,
                'opening_low': opening_low
            })
            
        n_symbols = len(valid_data)
        if n_symbols < MIN_SYMBOLS_REQUIRED:
            logger.warning(f"Skipping {dt}: Only {n_symbols} valid symbols found (min {MIN_SYMBOLS_REQUIRED}).")
            return None
            
        # Compute Cross-Sectional Features
        v_df = pd.DataFrame(valid_data)
        rets = v_df['ret'].values
        
        advancers = np.sum(rets > 0)
        decliners = np.sum(rets < 0)
        
        features = {
            'date': dt.isoformat(),
            'n_symbols': int(n_symbols),
            'pct_positive': float(advancers / n_symbols),
            'pct_above_vwap': float(np.sum(v_df['close_105'] > v_df['vwap']) / n_symbols),
            'adv_dec_ratio': float(advancers / decliners) if decliners != 0 else np.nan,
            'median_return': float(np.median(rets)),
            'cross_sectional_std': float(np.std(rets, ddof=1)),
            'pct_breaking_open_high': float(np.sum(v_df['high_105'] > v_df['opening_high']) / n_symbols),
            'pct_breaking_open_low': float(np.sum(v_df['low_105'] < v_df['opening_low']) / n_symbols),
            'avg_return_top10': float(np.mean(np.sort(rets)[-10:])),
            'avg_return_bottom10': float(np.mean(np.sort(rets)[:10])),
            'cross_sectional_skew': float(skew(rets, bias=False))
        }
        
        return features

    except Exception as e:
        logger.error(f"Error processing {dt}: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Generate Nifty 50 Breadth Features (11:00 AM Checkpoint)")
    parser.add_argument("--start", type=str, default=START_DATE_STR, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=END_DATE_STR, help="End date YYYY-MM-DD")
    args = parser.parse_args()

    try:
        start_dt = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_dt = datetime.strptime(args.end, "%Y-%m-%d").date()
    except ValueError as e:
        logger.error(f"Invalid date format: {e}")
        sys.exit(1)

    dm = DatabaseManager(ROOT / "data")
    keys = get_nifty_keys(dm)
    
    all_features = []
    skipped_count = 0
    
    current_dt = start_dt
    while current_dt <= end_dt:
        try:
            with dm.historical_reader("nse", "candles", "1m", current_dt) as conn:
                day_features = process_day(conn, keys, current_dt)
                if day_features:
                    all_features.append(day_features)
                else:
                    skipped_count += 1
        except FileNotFoundError:
            # Silently skip missing files (holidays or weekends)
            pass
        except Exception as e:
            logger.warning(f"Could not process {current_dt}: {e}")
            skipped_count += 1
            
        current_dt += timedelta(days=1)
        if len(all_features) % 50 == 0 and len(all_features) > 0:
            logger.info(f"Progress: {len(all_features)} valid days collected...")

    if not all_features:
        logger.error("No features generated. Check data availability and MIN_SYMBOLS_REQUIRED.")
        return

    # Create yearly CSVs
    df_all = pd.DataFrame(all_features)
    df_all['date_dt'] = pd.to_datetime(df_all['date'])
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    years = df_all['date_dt'].dt.year.unique()
    for year in sorted(years):
        year_df = df_all[df_all['date_dt'].dt.year == year].copy()
        year_df = year_df.drop(columns=['date_dt']).sort_values('date')
        
        output_file = OUTPUT_DIR / f"nifty50_breadth_{year}.csv"
        year_df.to_csv(output_file, index=False)
        logger.info(f"Saved {len(year_df)} rows → {output_file}")

    logger.info(f"Generation complete. Valid days: {len(all_features)}. Skipped/Missing: {skipped_count}.")

if __name__ == "__main__":
    main()
