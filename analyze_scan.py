
import sqlite3
import pandas as pd
import duckdb
import os
import io

SCANNER_DB_PATH = 'data/scanner/scanner_index.db'
BACKTEST_RUNS_DIR = 'data/backtest/runs'

def get_latest_scan_id():
    try:
        conn = sqlite3.connect(SCANNER_DB_PATH)
        query = "SELECT scan_id, scan_timestamp, profitable_symbols, total_symbols FROM scanner_results ORDER BY created_at DESC LIMIT 1"
        df = pd.read_sql_query(query, conn)
        conn.close()
        if not df.empty:
            return df.iloc[0]
        return None
    except Exception as e:
        print(f"Error reading scanner DB: {e}")
        return None

def get_symbol_results(scan_id, profitable_only=False):
    try:
        conn = sqlite3.connect(SCANNER_DB_PATH)
        if profitable_only:
            query = f"SELECT * FROM scanner_symbol_results WHERE scan_id = '{scan_id}' AND is_profitable = 1 ORDER BY rank ASC"
        else:
            query = f"SELECT * FROM scanner_symbol_results WHERE scan_id = '{scan_id}'"
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        print(f"Error reading symbol results: {e}")
        return pd.DataFrame()

def inspect_trades(run_id):
    db_path = os.path.join(BACKTEST_RUNS_DIR, f"{run_id}.duckdb")
    if not os.path.exists(db_path):
        print(f"Run DB not found: {db_path}")
        return

    try:
        conn = duckdb.connect(db_path, read_only=True)
        print(f"\n--- Schema for trades table in {run_id} ---")
        # DuckDB DESCRIBE returns columns: column_name, column_type, null, key, default, extra
        try:
            schema = conn.execute("DESCRIBE trades").fetchall()
            for col in schema:
                print(col)
        except Exception as e:
            print(f"Error describing trades: {e}")
            
        print(f"\n--- First 5 trades for {run_id} ---")
        try:
            trades = conn.execute("SELECT * FROM trades LIMIT 5").df()
            print(trades)
        except Exception as e:
            print(f"Error selecting trades: {e}")
            
        conn.close()
    except Exception as e:
        print(f"Error inspecting trades: {e}")

def main():
    scan_info = get_latest_scan_id()
    if scan_info is None:
        print("No scans found.")
        return

    print(f"Latest Scan: {scan_info['scan_id']} at {scan_info['scan_timestamp']}")
    print(f"Profitable: {scan_info['profitable_symbols']}/{scan_info['total_symbols']}")

    symbols_df = get_symbol_results(scan_info['scan_id'], profitable_only=True)
    
    if symbols_df.empty:
        print("No profitable symbols found. Fetching all symbols to find a valid run_id.")
        symbols_df = get_symbol_results(scan_info['scan_id'], profitable_only=False)

    if not symbols_df.empty:
        # Find a row with a valid run_id
        valid_row = None
        for index, row in symbols_df.iterrows():
            if row['test_run_id']:
                valid_row = row
                break
        
        if valid_row is not None:
            print(f"\nInspecting trades for symbol: {valid_row['symbol']} (Run ID: {valid_row['test_run_id']})")
            inspect_trades(valid_row['test_run_id'])
        else:
            print("No symbols with valid test_run_id found.")
    else:
        print("No symbols found in this scan.")

if __name__ == "__main__":
    main()
