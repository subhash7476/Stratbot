
import sqlite3
import pandas as pd
import duckdb
import os
import numpy as np

SCANNER_DB_PATH = 'data/scanner/scanner_index.db'
BACKTEST_RUNS_DIR = 'data/backtest/runs'

def get_latest_scan_id():
    try:
        conn = sqlite3.connect(SCANNER_DB_PATH)
        query = "SELECT scan_id, scan_timestamp, profitable_symbols, total_symbols, created_at FROM scanner_results ORDER BY created_at DESC LIMIT 1"
        df = pd.read_sql_query(query, conn)
        conn.close()
        if not df.empty:
            return df.iloc[0]
        return None
    except Exception as e:
        print(f"Error reading scanner DB: {e}")
        return None

def get_symbol_results(scan_id, profitable=True):
    try:
        conn = sqlite3.connect(SCANNER_DB_PATH)
        if profitable:
            query = f"SELECT * FROM scanner_symbol_results WHERE scan_id = '{scan_id}' AND is_profitable = 1 ORDER BY rank ASC"
        else:
            query = f"SELECT * FROM scanner_symbol_results WHERE scan_id = '{scan_id}' ORDER BY test_pnl DESC"
            
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        print(f"Error reading symbol results: {e}")
        return pd.DataFrame()

def get_trades(run_id):
    db_path = os.path.join(BACKTEST_RUNS_DIR, f"{run_id}.duckdb")
    if not os.path.exists(db_path):
        return pd.DataFrame()

    try:
        conn = duckdb.connect(db_path, read_only=True)
        trades = conn.execute("SELECT * FROM trades ORDER BY entry_ts").df()
        conn.close()
        return trades
    except Exception as e:
        print(f"Error reading trades for {run_id}: {e}")
        return pd.DataFrame()

def analyze_trades(trades_df):
    if trades_df.empty:
        return {}

    # Calculate Net PnL (Gross PnL - Fees)
    trades_df['net_pnl'] = trades_df['pnl'] - trades_df['fees']

    total_gross_pnl = trades_df['pnl'].sum()
    total_fees = trades_df['fees'].sum()
    total_net_pnl = trades_df['net_pnl'].sum()
    
    total_trades = len(trades_df)
    win_trades = trades_df[trades_df['net_pnl'] > 0]
    loss_trades = trades_df[trades_df['net_pnl'] <= 0]
    
    win_count = len(win_trades)
    loss_count = len(loss_trades)
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
    
    avg_win = win_trades['net_pnl'].mean() if win_count > 0 else 0
    avg_loss = loss_trades['net_pnl'].mean() if loss_count > 0 else 0
    risk_reward = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    
    # Duration analysis
    trades_df['entry_ts'] = pd.to_datetime(trades_df['entry_ts'])
    trades_df['exit_ts'] = pd.to_datetime(trades_df['exit_ts'])
    trades_df['duration'] = trades_df['exit_ts'] - trades_df['entry_ts']
    avg_duration = trades_df['duration'].mean()
    max_duration = trades_df['duration'].max()
    
    # Drawdown analysis (based on Net PnL curve)
    trades_df['cum_pnl'] = trades_df['net_pnl'].cumsum()
    trades_df['peak'] = trades_df['cum_pnl'].cummax()
    trades_df['drawdown'] = trades_df['cum_pnl'] - trades_df['peak']
    max_drawdown = trades_df['drawdown'].min()
    
    return {
        "Total Gross PnL": total_gross_pnl,
        "Total Fees": total_fees,
        "Total Net PnL": total_net_pnl,
        "Total Trades": total_trades,
        "Win Rate": win_rate,
        "Avg Profit (Net)": avg_win,
        "Avg Loss (Net)": avg_loss,
        "Risk/Reward (Net)": risk_reward,
        "Avg Duration": avg_duration,
        "Max Duration": max_duration,
        "Max Drawdown (Net)": max_drawdown
    }

def main():
    scan_info = get_latest_scan_id()
    if scan_info is None:
        print("No scans found.")
        return

    print(f"Latest Scan ID: {scan_info['scan_id']}")
    print(f"Timestamp: {scan_info['created_at']}")
    print(f"Profitable Symbols: {scan_info['profitable_symbols']}/{scan_info['total_symbols']}")
    print("-" * 60)

    symbols_df = get_symbol_results(scan_info['scan_id'], profitable=True)
    if symbols_df.empty:
        print("No profitable symbols found. Showing top 5 best performing (even if not profitable by all criteria):")
        symbols_df = get_symbol_results(scan_info['scan_id'], profitable=False).head(5)

    for index, row in symbols_df.iterrows():
        symbol = row['symbol']
        test_run_id = row['test_run_id']
        
        print(f"\nAnalyzing Symbol: {symbol}")
        print(f"  Rank: {row['rank']}")
        print(f"  Test PnL (Scanner DB): {row['test_pnl']:.2f}")
        
        if not test_run_id:
            print("  No Test Run ID found.")
            continue
            
        trades = get_trades(test_run_id)
        if trades.empty:
            print("  No trades found in test run.")
            continue
            
        stats = analyze_trades(trades)
        
        print("  Detailed Trade Analysis (from trades DB):")
        print(f"    Total Trades: {stats['Total Trades']}")
        print(f"    Total Net PnL: {stats['Total Net PnL']:.2f} (Gross: {stats['Total Gross PnL']:.2f}, Fees: {stats['Total Fees']:.2f})")
        print(f"    Win Rate: {stats['Win Rate']:.2f}%")
        print(f"    Avg Win (Net): {stats['Avg Profit (Net)']:.2f}")
        print(f"    Avg Loss (Net): {stats['Avg Loss (Net)']:.2f}")
        print(f"    Risk/Reward Ratio: {stats['Risk/Reward (Net)']:.2f}")
        print(f"    Avg Duration: {stats['Avg Duration']}")
        print(f"    Max Duration: {stats['Max Duration']}")
        print(f"    Max Drawdown: {stats['Max Drawdown (Net)']:.2f}")
        
        if abs(stats['Total Net PnL'] - row['test_pnl']) > 1.0:
            print(f"    [Warning] Discrepancy in PnL: DB={row['test_pnl']:.2f} vs Calc={stats['Total Net PnL']:.2f}")

    print("\n" + "=" * 60)
    print("SL and TP Logic Analysis (Based on Codebase):")
    print("The strategy 'PixityAIMetaStrategy' uses 'PixityAIRiskEngine'.")
    print("- Stop Loss (SL): Entry Price +/- 1.0 * ATR")
    print("- Take Profit (TP): Entry Price -/+ 2.0 * ATR")
    print("- Position Sizing: Risk Per Trade (default 500) / ATR")
    print("Note: Exact SL/TP values were not persisted in the database for individual trades.")

if __name__ == "__main__":
    main()
