
import os
import sys
import duckdb
import json
import pandas as pd
from pathlib import Path

def analyze_conf_correlation(run_id):
    db_path = Path("data/backtest/runs") / f"{run_id}.duckdb"
    
    if not db_path.exists():
        print(f"Error: Run DB not found at {db_path}")
        return

    conn = duckdb.connect(str(db_path), read_only=True)
    trades_df = conn.execute("SELECT * FROM trades").df()
    conn.close()

    if trades_df.empty:
        print("No trades found.")
        return

    # Extract confidence from metadata
    def extract_conf(meta_str):
        try:
            if isinstance(meta_str, dict):
                return meta_str.get('confidence', None)
            meta = json.loads(meta_str)
            return meta.get('confidence', None)
        except:
            return None

    trades_df['confidence'] = trades_df['metadata'].apply(extract_conf)
    trades_df['net_pnl'] = trades_df['pnl'] - trades_df['fees']
    trades_df['is_winner'] = trades_df['net_pnl'] > 0

    valid_trades = trades_df.dropna(subset=['confidence'])
    
    if valid_trades.empty:
        print("Warning: No confidence scores found in trade metadata.")
        # Check if maybe it's under a different key
        print("Sample metadata:", trades_df['metadata'].iloc[0] if not trades_df.empty else "N/A")
        return

    # Correlation
    correlation = valid_trades['confidence'].corr(valid_trades['net_pnl'])
    
    # Bucket by confidence
    valid_trades['conf_bucket'] = (valid_trades['confidence'] * 10).round() / 10
    bucket_stats = valid_trades.groupby('conf_bucket').agg(
        total_trades=('net_pnl', 'count'),
        win_rate=('is_winner', 'mean'),
        avg_net_pnl=('net_pnl', 'mean'),
        total_net_pnl=('net_pnl', 'sum')
    )

    print(f"\nAnalysis for Run: {run_id}")
    print(f"Total Trades with Confidence: {len(valid_trades)}")
    print(f"Correlation (Conf vs Net PnL): {correlation:.4f}")
    print("\nStats by Confidence Bucket:")
    print(bucket_stats)
    
    # Winners vs Losers Stats
    winners = valid_trades[valid_trades['is_winner']]
    losers = valid_trades[~valid_trades['is_winner']]
    
    print("\nWinner/Loser Confidence Stats:")
    print(f"Winners Avg Confidence: {winners['confidence'].mean():.4f}")
    print(f"Losers Avg Confidence:  {losers['confidence'].mean():.4f}")

if __name__ == "__main__":
    analyze_conf_correlation("scan_20260215_180817_d70dfc_test_ine205a01025")
