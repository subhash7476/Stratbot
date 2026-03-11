"""
Stock Backtest with Breadth Filter
==================================
Tests a simple PM session strategy (13:00 - 15:30) on a specific stock,
comparing performance with and without the 11:00 AM Breadth Filter.
"""

import sys
import argparse
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager
from core.database.utils.symbol_utils import resolve_to_instrument_key

def main():
    parser = argparse.ArgumentParser(description="Backtest a stock with Breadth Filter")
    parser.add_argument("--symbol", default="RELIANCE", help="Trading symbol (e.g. RELIANCE)")
    parser.add_argument("--cost", type=float, default=0.0004, help="Round-trip cost (0.0004 = 0.04 percent)")
    args = parser.parse_args()

    print("=" * 60)
    print(f"BACKTEST: {args.symbol} + BREADTH FILTER")
    print("=" * 60)

    # 1. Resolve Key
    dm = DatabaseManager(ROOT / "data")
    instrument_key = resolve_to_instrument_key(args.symbol, db_path=str(ROOT / "data"))
    if not instrument_key:
        print(f"Error: Could not resolve symbol {args.symbol}")
        return
    print(f"Instrument Key: {instrument_key}")

    # 2. Load Breadth Labels
    labels_path = ROOT / "data" / "features" / "day_type" / "breadth_cluster_labels.csv"
    if not labels_path.exists():
        print("Error: Breadth labels not found. Run clustering first.")
        return
    df_labels = pd.read_csv(labels_path)
    df_labels['date'] = pd.to_datetime(df_labels['date']).dt.date
    
    # 3. Process Trades
    results = []
    
    print(f"[1] Running backtest...")
    
    for _, row in df_labels.iterrows():
        dt = row['date']
        breadth_state = row['breadth_cluster'] # 1 is Bull
        
        try:
            with dm.historical_reader("nse", "candles", "1m", dt) as conn:
                # Get 13:00 and 15:29 bars
                t1 = f"{dt} 13:00:00"
                t2 = f"{dt} 15:29:00"
                
                df = conn.execute("""
                    SELECT timestamp, open, close 
                    FROM candles 
                    WHERE symbol = ? AND (timestamp = ? OR timestamp = ?)
                    ORDER BY timestamp
                """, [instrument_key, t1, t2]).df()
                
                if len(df) < 2:
                    continue
                
                open_1300 = df.iloc[0]['open']
                close_1530 = df.iloc[1]['close']
                
                if open_1300 > 0:
                    raw_ret = (close_1530 / open_1300 - 1)
                    results.append({
                        'date': dt,
                        'breadth_bull': (breadth_state == 1),
                        'raw_ret': raw_ret,
                        'net_ret': raw_ret - args.cost
                    })
        except:
            continue

    if not results:
        print("No trade data found.")
        return

    df_trades = pd.DataFrame(results)
    
    # 4. Comparative Analysis
    def get_stats(data):
        if len(data) == 0: return "N/A"
        mean = data.mean()
        win_rate = (data > 0).mean()
        cum_ret = (1 + data).prod() - 1
        sharpe = (mean / data.std() * np.sqrt(252)) if data.std() > 0 else 0
        return f"Mean: {mean:>+7.4%} | Win%: {win_rate:>5.1%} | Cum: {cum_ret:>+7.2%} | Sharpe: {sharpe:>5.2f}"

    print("\n[2] PERFORMANCE COMPARISON:")
    print("-" * 80)
    
    # Baseline: All Days
    print(f"{'Baseline (All Days)':<25} | N={len(df_trades):<3} | {get_stats(df_trades['net_ret'])}")
    
    # Filtered: Only BullBreadth Days
    df_bull = df_trades[df_trades['breadth_bull']]
    print(f"{'Filtered (BullBreadth)':<25} | N={len(df_bull):<3} | {get_stats(df_bull['net_ret'])}")
    
    # Excluded: Neutral + Bear Days
    df_other = df_trades[~df_trades['breadth_bull']]
    print(f"{'Excluded (Non-Bull)':<25} | N={len(df_other):<3} | {get_stats(df_other['net_ret'])}")
    
    print("-" * 80)
    
    # 5. Conclusion
    bull_mean = df_bull['net_ret'].mean()
    other_mean = df_other['net_ret'].mean()
    diff = bull_mean - other_mean
    
    print("\n[3] VERDICT:")
    if diff > 0:
        improvement = (bull_mean / other_mean - 1) if other_mean != 0 else np.nan
        print(f"Breadth Filter provides a NET LIFT of {diff:>+7.4%} per trade.")
        if bull_mean > 0:
            print("The strategy is PROFITABLE after costs when filtered.")
        else:
            print("Even with the filter, the strategy is not profitable after costs.")
    else:
        print("Breadth Filter did not improve performance for this stock.")
    
    print("=" * 60)

if __name__ == "__main__":
    main()
