"""
Quick script to check scanner progress without interrupting the run
"""
import sqlite3
from pathlib import Path

db_path = Path("data/scanner/scanner_index.db")
if not db_path.exists():
    print("No scanner database found yet")
    exit(0)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get latest scan
cursor.execute("""
    SELECT scan_id, scan_timestamp, total_symbols, profitable_symbols, status
    FROM scanner_results
    ORDER BY scan_timestamp DESC
    LIMIT 1
""")
scan = cursor.fetchone()

if not scan:
    print("No scans found")
    exit(0)

scan_id, timestamp, total, profitable, status = scan

# Count completed symbols
cursor.execute("""
    SELECT COUNT(*)
    FROM scanner_symbol_results
    WHERE scan_id = ? AND test_status = 'COMPLETED'
""", (scan_id,))
completed = cursor.fetchone()[0]

# Get latest symbol being processed
cursor.execute("""
    SELECT trading_symbol, test_status
    FROM scanner_symbol_results
    WHERE scan_id = ?
    ORDER BY rowid DESC
    LIMIT 1
""", (scan_id,))
latest = cursor.fetchone()

conn.close()

print(f"\n{'='*60}")
print(f"Latest Scan: {scan_id}")
print(f"Started: {timestamp}")
print(f"Status: {status}")
print(f"{'='*60}")
print(f"Progress: {completed}/{total} symbols ({completed/total*100:.1f}%)")
print(f"Profitable so far: {profitable}")
if latest:
    print(f"Latest: {latest[0]} ({latest[1]})")
print(f"{'='*60}\n")
