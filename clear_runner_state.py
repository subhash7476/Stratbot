#!/usr/bin/env python3
"""Clear runner_state table to reset scanner."""
from pathlib import Path
from core.database.manager import DatabaseManager

db = DatabaseManager(Path("data"))

with db.config_writer() as conn:
    result = conn.execute("DELETE FROM runner_state")
    count = result.rowcount
    print(f"[OK] Cleared {count} rows from runner_state table")

print("Scanner will show fresh data on next run.")
