#!/usr/bin/env python3
"""Test manual runner_state update."""
from pathlib import Path
from core.database.manager import DatabaseManager
from datetime import datetime

db = DatabaseManager(Path("data"))

print("=== Before update ===")
with db.config_reader() as conn:
    row = conn.execute("SELECT symbol, current_bias, confidence, updated_at FROM runner_state WHERE symbol='MCX_FO|451669'").fetchone()
    if row:
        print(f"{row[0]} | Bias: {row[1]} | Conf: {row[2]} | Updated: {row[3]}")

print("\n=== Updating ===")
with db.config_writer() as conn:
    conn.execute("""
        UPDATE runner_state
        SET current_bias='BUY',
            confidence=0.99,
            updated_at=CURRENT_TIMESTAMP
        WHERE symbol='MCX_FO|451669'
    """)
    print("Update executed")

print("\n=== After update ===")
with db.config_reader() as conn:
    row = conn.execute("SELECT symbol, current_bias, confidence, updated_at FROM runner_state WHERE symbol='MCX_FO|451669'").fetchone()
    if row:
        print(f"{row[0]} | Bias: {row[1]} | Conf: {row[2]} | Updated: {row[3]}")
