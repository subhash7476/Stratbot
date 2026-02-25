"""
Breadth + Relative Strength (RS) Backtest
=========================================
Tests the PM session performance (13:00 - 15:30) of the TOP 10 stocks 
(ranked by 11:00 AM return), conditional on the 11:00 AM Breadth State.
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager
from core.database.utils.symbol_utils import resolve_to_instrument_key

def main():
    print("=" * 70)
    print("BACKTEST: TOP 10 RS STOCKS + BREADTH FILTER")
    print("=" * 70)

    # 1. Load Breadth Labels
    labels_path = ROOT / "data" / "features" / "day_type" / "breadth_cluster_labels.csv"
    if not labels_path.exists():
        print("Error: Breadth labels not found.")
        return
    df_labels = pd.read_csv(labels_path)
    df_labels['date'] = pd.to_datetime(df_labels['date']).dt.date
    
    # 2. Load Nifty 50 Symbols
    symbol_csv = ROOT / "data" / "nifty-50-stock-list.csv"
    symbols = pd.read_csv(symbol_csv)['Symbol'].tolist()
    
    dm = DatabaseManager(ROOT / "data")
    keys = [resolve_to_instrument_key(s, db_path=str(ROOT / "data")) for s in symbols]
    keys = [k for k in keys if k]
    
    cost = 0.0004 # 0.04%
    results = []

    print(f"[1] Processing {len(df_labels)} days...")
    
    for _, row in df_labels.iterrows():
        dt = row['date']
        is_bull_breadth = (row['breadth_cluster'] == 1)
        
        try:
            with dm.historical_reader("nse", "candles", "1m", dt) as conn:
                # 1. Get 09:15, 11:00, 13:00, 15:29 bars for all 50 stocks
                # Bar 0, Bar 105, Bar 225, Bar 374
                t0 = f"{dt} 09:15:00"
                t1 = f"{dt} 11:00:00"
                t2 = f"{dt} 13:00:00"
                t3 = f"{dt} 15:29:00"
                
                placeholders = ','.join(['?'] * len(keys))
                query = f"""
                    SELECT symbol, timestamp, open, close 
                    FROM candles 
                    WHERE symbol IN ({placeholders}) 
                    AND (timestamp = ? OR timestamp = ? OR timestamp = ? OR timestamp = ?)
                """
                params = keys + [t0, t1, t2, t3]
                day_df = conn.execute(query, params).df()
                
                if day_df.empty:
                    continue
                
                stock_metrics = []
                for symbol, group in day_df.groupby('symbol'):
                    if len(group) < 4: continue
                    
                    group = group.sort_values('timestamp')
                    # 11:00 AM RS Rank metric: return from 9:15 to 11:00
                    rs_ret = (group.iloc[1]['close'] / group.iloc[0]['open'] - 1)
                    # PM trade performance: return from 13:00 to 15:30
                    pm_ret = (group.iloc[3]['close'] / group.iloc[2]['open'] - 1)
                    
                    stock_metrics.append({'symbol': symbol, 'rs_ret': rs_ret, 'pm_ret': pm_ret})
                
                if not stock_metrics: continue
                
                # Rank by RS (descending)
                df_day_stocks = pd.DataFrame(stock_metrics).sort_values('rs_ret', ascending=False)
                
                # Pick Top 10
                top_10 = df_day_stocks.head(10)
                
                for _, s_row in top_10.iterrows():
                    results.append({
                        'date': dt,
                        'symbol': s_row['symbol'],
                        'is_bull_breadth': is_bull_breadth,
                        'pm_ret_net': s_row['pm_ret'] - cost
                    })
                    
        except:
            continue

    if not results:
        print("No results found.")
        return

    df_final = pd.DataFrame(results)

    # 3. Analyze
    def get_stats(data):
        mean = data.mean()
        win_rate = (data > 0).mean()
        # Daily return of the Top 10 portfolio (mean of the 10)
        return f"Mean: {mean:>+7.4%} | Win%: {win_rate:>5.1%}"

    print("\n[2] TOP 10 RS PORTFOLIO PERFORMANCE:")
    print("-" * 75)
    
    baseline = df_final['pm_ret_net']
    print(f"{'Baseline (All Breadth States)':<30} | {get_stats(baseline)}")
    
    bull_only = df_final[df_final['is_bull_breadth']]['pm_ret_net']
    print(f"{'Filtered (BullBreadth ONLY)':<30} | {get_stats(bull_only)}")
    
    non_bull = df_final[~df_final['is_bull_breadth']]['pm_ret_net']
    print(f"{'Excluded (Non-Bull States)':<30} | {get_stats(non_bull)}")
    
    print("-" * 75)
    print("=" * 70)

if __name__ == "__main__":
    main()
