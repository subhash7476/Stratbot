import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List

import pandas as pd
import numpy as np

# Path setup
ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

from core.database.manager import DatabaseManager
from core.database.utils.symbol_utils import resolve_to_instrument_key
from core.analytics.day_features import compute_session_features, finalize_dataframe

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def get_nifty_50_keys():
    csv_path = ROOT / "data" / "nifty-50-stock-list.csv"
    symbols = pd.read_csv(csv_path)['Symbol'].tolist()
    keys = [resolve_to_instrument_key(s, db_path=str(ROOT / "data")) for s in symbols]
    return [k for k in keys if k]

def process_year(year: int, keys: List[str], dm: DatabaseManager):
    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)
    
    all_rows = []
    current_dt = start_date
    
    while current_dt <= end_date:
        try:
            with dm.historical_reader("nse", "candles", "1m", current_dt) as conn:
                # Query all stocks for the day
                placeholders = ','.join(['?'] * len(keys))
                query = f"SELECT * FROM candles WHERE symbol IN ({placeholders}) ORDER BY symbol, timestamp"
                df = conn.execute(query, keys).df()
                
                if not df.empty:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    for symbol, group in df.groupby('symbol'):
                        # Session filter (9:15 - 15:30)
                        group = group.sort_values('timestamp').reset_index(drop=True)
                        if len(group) < 375:
                            continue
                        
                        features = compute_session_features(group)
                        if features:
                            features['date'] = current_dt.isoformat()
                            features['symbol'] = symbol
                            all_rows.append(features)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error(f"Error on {current_dt}: {e}")
            
        current_dt += timedelta(days=1)
    
    if all_rows:
        df_year = pd.DataFrame(all_rows)
        # finalize_dataframe handles rolling percentiles per symbol
        # We need to sort by symbol and date first
        df_year = df_year.sort_values(['symbol', 'date'])
        df_year = finalize_dataframe(df_year)
        
        output_path = ROOT / "data" / "features" / "day_type" / f"stocks_full_features_{year}.csv"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df_year.to_csv(output_path, index=False)
        logger.info(f"Saved {len(df_year)} rows to {output_path}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()
    
    dm = DatabaseManager(ROOT / "data")
    keys = get_nifty_50_keys()
    process_year(args.year, keys, dm)

if __name__ == "__main__":
    main()
