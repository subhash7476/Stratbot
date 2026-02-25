#!/usr/bin/env python3
"""
Migration: CSV -> SQLite (V9 PM Scalper)
---------------------------------------
Imports existing trades from logs/v9_paper_trades.csv into trading.db.
"""
import csv
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager

def migrate():
    data_root = ROOT / "data"
    db_manager = DatabaseManager(data_root)
    csv_path = ROOT / "logs" / "v9_paper_trades.csv"
    
    if not csv_path.exists():
        print(f"No CSV found at {csv_path}. Skipping migration.")
        return

    print(f"Reading trades from {csv_path}...")
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Found {len(rows)} trades in CSV. Importing to SQLite...")
    
    # Initialize schema if needed
    from core.database.schema import V9_PAPER_TRADES_SCHEMA
    with db_manager.trading_writer() as conn:
        conn.execute(V9_PAPER_TRADES_SCHEMA)

    imported = 0
    skipped = 0
    
    with db_manager.trading_writer() as conn:
        for r in rows:
            # Check for existing
            exists = conn.execute(
                "SELECT COUNT(*) FROM v9_paper_trades WHERE session_date = ? AND entry_time = ?",
                [r["session_date"], r["entry_time"]]
            ).fetchone()[0]
            
            if exists > 0:
                skipped += 1
                continue
                
            conn.execute(
                """
                INSERT INTO v9_paper_trades
                (session_date, entry_time, entry_price, stop_level,
                 exit_time, exit_price, exit_reason, confidence,
                 predicted_state, pnl_gross_pct, pnl_net_pct, model_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    r["session_date"], r["entry_time"], 
                    float(r["entry_price"]) if r["entry_price"] else None,
                    float(r["stop_level"]) if r["stop_level"] else None,
                    r["exit_time"],
                    float(r["exit_price"]) if r["exit_price"] else None,
                    r["exit_reason"],
                    float(r["confidence"]) if r["confidence"] else None,
                    r["predicted_state"],
                    float(r["pnl_gross_pct"]) if r["pnl_gross_pct"] else None,
                    float(r["pnl_net_pct"]) if r["pnl_net_pct"] else None,
                    r["model_version"]
                ]
            )
            imported += 1

    print(f"Migration complete: {imported} imported, {skipped} skipped.")

if __name__ == "__main__":
    migrate()
