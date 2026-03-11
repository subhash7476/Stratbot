"""
Stock-Level Breadth Confluence Audit (Memory Optimized)
=======================================================
Evaluates the PM session performance of INDIVIDUAL Nifty 50 stocks 
conditional on the 11:00 AM Breadth State.
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager
from core.database.utils.symbol_utils import resolve_to_instrument_key

def main():
    print("=" * 60)
    print("STOCK-LEVEL BREADTH CONFLUENCE AUDIT")
    print("=" * 60)

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
    
    regimes = {0: "Neutral", 1: "Bull", 2: "Bear"}
    
    # Using simple counters to save memory
    stats_data = {
        "Bull": {"sum": 0.0, "sum_sq": 0.0, "count": 0, "pos": 0},
        "Neutral": {"sum": 0.0, "sum_sq": 0.0, "count": 0, "pos": 0},
        "Bear": {"sum": 0.0, "sum_sq": 0.0, "count": 0, "pos": 0}
    }

    print(f"[1] Processing {len(df_labels)} days...")
    
    processed_days = 0
    for _, row in df_labels.iterrows():
        dt = row['date']
        state_name = regimes[row['breadth_cluster']]
        
        try:
            with dm.historical_reader("nse", "candles", "1m", dt) as conn:
                # Optimized Query: Only fetch entry (13:00) and exit (15:29) bars
                placeholders = ','.join(['?'] * len(keys))
                t1 = f"{dt} 13:00:00"
                t2 = f"{dt} 15:29:00"
                
                query = f"""
                    SELECT symbol, timestamp, open, close 
                    FROM candles 
                    WHERE symbol IN ({placeholders}) 
                    AND (timestamp = ? OR timestamp = ?)
                """
                params = keys + [t1, t2]
                day_df = conn.execute(query, params).df()
                
                if day_df.empty:
                    continue
                
                # Check each symbol's pair
                for symbol, group in day_df.groupby('symbol'):
                    if len(group) < 2:
                        continue
                    
                    group = group.sort_values('timestamp')
                    open_1300 = group.iloc[0]['open']
                    close_1530 = group.iloc[1]['close']
                    
                    if open_1300 > 0:
                        ret = (close_1530 / open_1300 - 1)
                        s = stats_data[state_name]
                        s['sum'] += ret
                        s['sum_sq'] += ret**2
                        s['count'] += 1
                        if ret > 0:
                            s['pos'] += 1
            
            processed_days += 1
            if processed_days % 50 == 0:
                print(f"    Progress: {processed_days}/{len(df_labels)} days...")
                
        except:
            continue

    print("\n[2] STOCK-LEVEL PERFORMANCE BY BREADTH STATE:")
    print("-" * 75)
    print(f"{'State':<10} | {'Obs':<10} | {'Mean Ret':<10} | {'Win%':>7} | {'t-stat':>7}")
    print("-" * 75)

    for state in ["Bull", "Neutral", "Bear"]:
        s = stats_data[state]
        n = s['count']
        if n < 2: continue
        
        mean = s['sum'] / n
        var = (s['sum_sq'] / n) - (mean**2)
        std = np.sqrt(max(0, var))
        t_stat = (mean / (std / np.sqrt(n))) if std > 0 else 0
        win_rate = s['pos'] / n
        
        print(f"{state:<10} | {n:<10} | {mean:>+9.4%} | {win_rate:>7.1%} | {t_stat:>7.2f}")

    print("-" * 75)
    print("=" * 60)

if __name__ == "__main__":
    main()
