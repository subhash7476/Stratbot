from core.data.duckdb_client import db_cursor
import os

def cleanup_invalid_data():
    print("Starting data cleanup...")
    try:
        with db_cursor(read_only=False) as conn:
            # 1. Delete data for 2026-02-02 to 2026-02-03 to allow fresh fetch
            # We use string comparison for the date part
            res = conn.execute("DELETE FROM candles WHERE timestamp >= '2026-02-02'").fetchall()
            deleted = conn.execute("SELECT changes()").fetchone()[0]
            print(f"Deleted {deleted} records from 2026-02-02 onwards.")
            
            # 2. Check for any other data that might be shifted (after 10:00 UTC)
            # Market is 03:45 to 10:00 UTC
            res = conn.execute("""
                DELETE FROM candles 
                WHERE CAST(timestamp AS TIME) > '10:00:00' 
                OR CAST(timestamp AS TIME) < '03:45:00'
            """).fetchall()
            deleted_extra = conn.execute("SELECT changes()").fetchone()[0]
            print(f"Deleted {deleted_extra} extra records outside market hours (UTC).")
            
            conn.execute("CHECKPOINT")
            print("Cleanup successful.")
    except Exception as e:
        print(f"Cleanup failed: {e}")

if __name__ == "__main__":
    cleanup_invalid_data()
