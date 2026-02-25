import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "features" / "day_type" / "stock_930am_test_results.csv"

def main():
    df = pd.read_csv(DATA_PATH)
    cost = 0.0004 # 0.04%
    
    print("=" * 60)
    print("STOCK CLASSIFIER BACKTEST (2026 H1: 10:30 AM - 13:30 PM)")
    print("=" * 60)
    
    # 1. Directional Returns
    # Long if BullTrend, Short if BearTrend, No Trade if Choppy
    df['trade_ret'] = 0.0
    
    # Bull Predictions -> Long
    bull_mask = (df['pred_label'] == "BullTrend")
    df.loc[bull_mask, 'trade_ret'] = df.loc[bull_mask, 'target_ret'] - cost
    
    # Bear Predictions -> Short
    bear_mask = (df['pred_label'] == "BearTrend")
    df.loc[bear_mask, 'trade_ret'] = -df.loc[bear_mask, 'target_ret'] - cost
    
    # 2. Stats
    trades = df[df['pred_label'].isin(["BullTrend", "BearTrend"])]
    
    if trades.empty:
        print("No trades taken.")
        return
        
    mean_ret = trades['trade_ret'].mean()
    win_rate = (trades['trade_ret'] > 0).mean()
    t_stat = (mean_ret / (trades['trade_ret'].std() / np.sqrt(len(trades))))
    
    print(f"Total Trades: {len(trades)}")
    print(f"Mean Return:  {mean_ret:>+7.4%}")
    print(f"Win Rate:     {win_rate:>7.1%}")
    print(f"t-stat:       {t_stat:>7.2f}")
    
    # 3. Breakdown
    print("\nPERFORMANCE BY DIRECTION:")
    for label in ["BullTrend", "BearTrend"]:
        subset = df[df['pred_label'] == label]
        if subset.empty: continue
        m = subset['trade_ret'].mean()
        w = (subset['trade_ret'] > 0).mean()
        print(f"{label:<10} | N={len(subset):<4} | Mean={m:>+7.4%} | Win%={w:>5.1%}")
        
    print("=" * 60)

if __name__ == "__main__":
    main()
