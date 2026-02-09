import duckdb
import os

DB_PATH = "data/trading_bot.duckdb"

def fix_data():
    print(f"Opening DB at {DB_PATH}...")
    try:
        # Open in read-write mode
        # If it fails, try read-only just to see
        conn = duckdb.connect(DB_PATH, read_only=False)
        print("Connected.")
        
        # Shift all data back by 5 hours 30 minutes for candles
        # Only for records that were likely stored incorrectly
        # Records with time > 10:00 UTC are definitely shifted
        res = conn.execute("""
            UPDATE candles 
            SET timestamp = timestamp - INTERVAL '5 hours 30 minutes'
            WHERE CAST(timestamp AS TIME) > '10:00:00'
        """).fetchall()
        
        # Also handle potential records shifted but still within market (harder to detect)
        # Safest is to delete 2026-02-02 data if it was all fetched today
        res = conn.execute("DELETE FROM candles WHERE timestamp >= '2026-02-02'").fetchall()
        
        conn.execute("CHECKPOINT")
        conn.close()
        print("Fix applied.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fix_data()
