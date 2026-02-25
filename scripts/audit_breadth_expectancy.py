"""
Breadth Expectancy Audit
========================
Calculates PM session (13:00 - 15:30) performance metrics 
conditional on the 11:00 AM Breadth State.
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
import duckdb
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager

def compute_pm_return(dm: DatabaseManager, dt_str: str) -> Optional[float]:
    """Return: (Close_15:30 / Open_13:00 - 1) for Nifty 50."""
    try:
        d = date.fromisoformat(dt_str)
        with dm.historical_reader("nse", "candles", "1m", d) as conn:
            # Bar 225 is 13:00 (9:15 + 225)
            # Bar 374 is 15:29 (9:15 + 374)
            df = conn.execute("""
                SELECT timestamp, open, close 
                FROM candles 
                WHERE symbol = 'NSE_INDEX|Nifty 50'
                ORDER BY timestamp
            """).df()
            
            if len(df) < 375:
                return None
            
            open_1300 = df.iloc[225]['open']
            close_1530 = df.iloc[374]['close']
            
            return (close_1530 / open_1300 - 1)
    except:
        return None

def main():
    print("=" * 60)
    print("BREADTH EXPECTANCY AUDIT (PM SESSION)")
    print("=" * 60)

    # 1. Load labels
    labels_path = ROOT / "data" / "features" / "day_type" / "breadth_cluster_labels.csv"
    if not labels_path.exists():
        print("Error: Labels not found. Run scripts/cluster_breadth_day_types.py first.")
        return
    
    df = pd.read_csv(labels_path)
    print(f"[1] Loaded {len(df)} days with breadth labels.")

    # 2. Compute PM returns
    dm = DatabaseManager(ROOT / "data")
    pm_returns = []
    
    print("[2] Computing PM returns (13:00 - 15:30)...")
    for i, row in df.iterrows():
        ret = compute_pm_return(dm, row['date'])
        pm_returns.append(ret)
        if (i+1) % 100 == 0:
            print(f"    Progress: {i+1}/{len(df)} days...")

    df['pm_return'] = pm_returns
    df = df.dropna()

    # 3. Analyze Expectancy
    regimes = {0: "Neutral", 1: "Bull", 2: "Bear"}
    
    print("\n[3] PM EXPECTANCY BY BREADTH STATE:")
    print("-" * 60)
    print(f"{'State':<10} | {'Days':<5} | {'Mean Ret':<10} | {'Win%':<7} | {'t-stat':<7}")
    print("-" * 60)
    
    for c_id, name in regimes.items():
        c_data = df[df['breadth_cluster'] == c_id]['pm_return']
        if c_data.empty: continue
        
        n = len(c_data)
        mean_ret = c_data.mean()
        win_rate = (c_data > 0).mean()
        std = c_data.std()
        t_stat = (mean_ret / (std / np.sqrt(n))) if std > 0 else 0
        
        print(f"{name:<10} | {n:<5} | {mean_ret:>+9.4%} | {win_rate:>7.1%} | {t_stat:>7.2f}")

    print("-" * 60)
    print("Validation: BullBreadth should have Mean Ret > 0 and BearBreadth < 0.")
    print("=" * 60)

if __name__ == "__main__":
    main()
