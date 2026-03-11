
import sqlite3
import pandas as pd

db_path = 'data/scanner/scanner_index.db'

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    table = 'scanner_results'
    print(f"\n--- Schema for table: {table} ---")
    cursor.execute(f"PRAGMA table_info({table})")
    columns = cursor.fetchall()
    for col in columns:
        print(col)
        
    print(f"\n--- First 5 rows for table: {table} ---")
    df = pd.read_sql_query(f"SELECT * FROM {table} LIMIT 5", conn)
    print(df)
        
    conn.close()

except Exception as e:
    print(f"Error accessing database: {e}")
