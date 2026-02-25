"""
Index-Breadth Confluence Audit
==============================
Statistical study of the interaction between 13:00 PM Index Predictions 
and 11:00 AM Breadth State.
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
from scipy import stats

# Path setup
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Data Paths
PM_EXPECTANCY_PATH = ROOT / "data" / "features" / "day_type" / "pm_expectancy_raw.csv"
BREADTH_LABELS_PATH = ROOT / "data" / "features" / "day_type" / "breadth_cluster_labels.csv"

# Go / No-Go Thresholds
MIN_N = 30
MIN_T_STAT = 2.0
MIN_SHARPE = 0.25
MIN_LIFT = 0.50
MAX_P_VALUE = 0.05

# Breadth mapping
BREADTH_NAMES = {0: "NeutralBreadth", 1: "BullBreadth", 2: "BearBreadth"}

def load_and_merge():
    # Load index predictions and pm_returns
    df_index = pd.read_csv(PM_EXPECTANCY_PATH)
    df_index['date'] = pd.to_datetime(df_index['date'])
    
    # Load breadth states
    df_breadth = pd.read_csv(BREADTH_LABELS_PATH)
    df_breadth['date'] = pd.to_datetime(df_breadth['date'])
    
    # Merge
    df = pd.merge(df_index, df_breadth, on='date', how='inner')
    return df

def main():
    df = load_and_merge()
    
    # Calculate global median abs return for Choppy win rate
    median_abs_return = df['pm_return'].abs().median()
    
    index_states = ["BullTrend", "Choppy", "BearTrend"]
    breadth_ids = [1, 0, 2] # Bull, Neutral, Bear
    
    results = []
    
    for idx_state in index_states:
        # Standalone metrics for this Index State
        df_standalone = df[df['pred_label'] == idx_state]
        standalone_n = len(df_standalone)
        if standalone_n == 0: continue
        
        standalone_mean = df_standalone['pm_return'].mean()
        
        for b_id in breadth_ids:
            b_name = BREADTH_NAMES[b_id]
            df_cell = df[(df['pred_label'] == idx_state) & (df['breadth_cluster'] == b_id)]
            n = len(df_cell)
            
            if n == 0:
                results.append({
                    'index_state': idx_state,
                    'breadth_state': b_name,
                    'n': 0
                })
                continue
                
            pm_ret = df_cell['pm_return']
            mean_ret = pm_ret.mean()
            std_ret = pm_ret.std()
            
            # Directional metrics
            if idx_state == "BullTrend":
                dir_ret = pm_ret
                win_rate = (pm_ret > 0).mean()
                t_val, _ = stats.ttest_1samp(dir_ret, 0)
                mean_dir_ret = mean_ret
            elif idx_state == "BearTrend":
                dir_ret = -pm_ret
                win_rate = (pm_ret < 0).mean()
                t_val, _ = stats.ttest_1samp(dir_ret, 0)
                mean_dir_ret = -mean_ret
            else: # Choppy
                dir_ret = median_abs_return - pm_ret.abs() # Pos if quieter than median
                win_rate = (pm_ret.abs() < median_abs_return).mean()
                t_val, _ = stats.ttest_1samp(dir_ret, 0)
                mean_dir_ret = dir_ret.mean()

            sharpe = mean_dir_ret / std_ret if std_ret > 1e-6 else 0
            
            # Lift & Significance
            # We compare against standalone for the same Index State
            lift = (mean_ret - standalone_mean) / abs(standalone_mean) if abs(standalone_mean) > 1e-6 else 0
            # Two-sample t-test (comparing this cell vs all other days in the same Index State)
            # Actually user said: confluent vs standalone
            _, p_val = stats.ttest_ind(df_cell['pm_return'], df_standalone['pm_return'], equal_var=False)
            
            results.append({
                'index_state': idx_state,
                'breadth_state': b_name,
                'n': n,
                'mean_ret': mean_ret,
                'mean_dir_ret': mean_dir_ret,
                'win_rate': win_rate,
                'std': std_ret,
                't_stat': t_val,
                'sharpe': sharpe,
                'standalone_mean': standalone_mean,
                'lift': lift,
                'p_val': p_val
            })

    # Print Report
    print("=" * 100)
    print(f"{'Index State':<12} | {'Breadth State':<15} | {'N':>3} | {'Mean':>7} | {'DirMean':>7} | {'Win%':>6} | {'t-stat':>6} | {'Sharpe':>6} | {'Lift%':>6} | {'p-val':>5}")
    print("-" * 100)
    
    confluence_found = False
    
    for r in results:
        if r['n'] == 0:
            print(f"{r['index_state']:<12} | {r['breadth_state']:<15} | {r['n']:>3} | {'-':>7} | {'-':>7} | {'-':>6} | {'-':>6} | {'-':>6} | {'-':>6} | {'-':>5}")
            continue
            
        print(f"{r['index_state']:<12} | {r['breadth_state']:<15} | {r['n']:>3} | {r['mean_ret']:>7.4f} | {r['mean_dir_ret']:>7.4f} | {r['win_rate']:>6.1%} | {r['t_stat']:>6.2f} | {r['sharpe']:>6.2f} | {r['lift']:>6.1%} | {r['p_val']:>5.3f}")
        
        # Go / No-Go Check
        is_confluent = (
            r['n'] >= MIN_N and 
            r['t_stat'] > MIN_T_STAT and 
            r['sharpe'] > MIN_SHARPE and 
            r['lift'] >= MIN_LIFT and 
            r['p_val'] < MAX_P_VALUE
        )
        if is_confluent:
            confluence_found = True

    print("-" * 100)
    print(f"VERDICT: CONFLUENCE FOUND: {'YES' if confluence_found else 'NO'}")
    print("=" * 100)

if __name__ == "__main__":
    main()
