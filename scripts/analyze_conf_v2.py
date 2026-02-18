
import os
import sys
import duckdb
import json
import pandas as pd
from pathlib import Path
from datetime import datetime

def analyze_conf_correlation():
    # Find latest validation run
    runs_dir = Path("data/backtest/runs")
    run_files = list(runs_dir.glob("validation_global_*.duckdb"))
    if not run_files:
        print("No validation runs found.")
        return
        
    latest_run = max(run_files, key=lambda p: p.stat().st_mtime)
    print(f"Analyzing Latest Run: {latest_run.name}")

    conn = duckdb.connect(str(latest_run), read_only=True)
    trades_df = conn.execute("SELECT * FROM trades").df()
    conn.close()

    if trades_df.empty:
        print("No trades found.")
        return

    # Extract confidence from metadata
    def extract_conf(meta):
        if isinstance(meta, str):
            meta = json.loads(meta)
        return meta.get('confidence', None)

    trades_df['confidence'] = trades_df['metadata'].apply(extract_conf)
    trades_df['net_pnl'] = trades_df['pnl'] - trades_df['fees']
    trades_df['is_winner'] = trades_df['net_pnl'] > 0

    valid_trades = trades_df.dropna(subset=['confidence'])
    
    if valid_trades.empty:
        print("Warning: No confidence scores found in trade metadata.")
        print("Sample metadata:", trades_df['metadata'].iloc[0] if not trades_df.empty else "N/A")
        return

    # Correlation
    correlation = valid_trades['confidence'].corr(valid_trades['net_pnl'])
    
    # Bucket by confidence (scaled)
    # If conf is 0.45-0.70, let's bucket by 0.05
    valid_trades['conf_bucket'] = (valid_trades['confidence'] * 20).round() / 20
    bucket_stats = valid_trades.groupby('conf_bucket').agg(
        total_trades=('net_pnl', 'count'),
        win_rate=('is_winner', 'mean'),
        avg_net_pnl=('net_pnl', 'mean'),
        total_net_pnl=('net_pnl', 'sum'),
        max_loss=('net_pnl', 'min'),
        max_gain=('net_pnl', 'max')
    )

    print(f"\nTotal Trades Analysed: {len(valid_trades)}")
    print(f"Correlation (Confidence vs Net PnL): {correlation:.4f}")
    
    print("\nStats by Confidence Bucket:")
    print(bucket_stats.to_string())
    
    # Specific Loss Analysis
    losses = valid_trades[~valid_trades['is_winner']]
    print("\nLoss Distribution by Confidence:")
    loss_dist = losses.groupby('conf_bucket').size()
    print(loss_dist)
    
    print("\nWinner/Loser Confidence Stats:")
    print(f"Winners Avg Confidence: {valid_trades[valid_trades['is_winner']]['confidence'].mean():.4f}")
    print(f"Losers Avg Confidence:  {valid_trades[~valid_trades['is_winner']]['confidence'].mean():.4f}")

if __name__ == "__main__":
    analyze_conf_correlation()
