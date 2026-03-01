#!/usr/bin/env python3
"""Check if scanner data is being populated."""
from pathlib import Path
from core.database.manager import DatabaseManager

db = DatabaseManager(Path("data"))

print("=== Checking runner_state table ===\n")

with db.config_reader() as conn:
    # Check runner_state
    rows = conn.execute("""
        SELECT symbol, strategy_id, timeframe, current_bias, confidence, status, updated_at
        FROM runner_state
        ORDER BY updated_at DESC
    """).fetchall()

    if rows:
        print(f"Found {len(rows)} rows in runner_state:\n")
        for symbol, strategy_id, timeframe, bias, conf, status, updated_at in rows:
            print(f"{symbol:30} | {strategy_id:15} | {timeframe:5} | {bias:8} | {conf:4.2f} | {status:10} | {updated_at}")
    else:
        print("❌ runner_state table is EMPTY!")
        print("\nThis is why the scanner is not showing data.")
        print("\nThe TradingRunner needs to write to runner_state table.")

print("\n=== Checking recent signals ===\n")

with db.signals_reader() as conn:
    rows = conn.execute("""
        SELECT symbol, strategy_id, signal_type, confidence, bar_ts, status
        FROM signals
        ORDER BY bar_ts DESC
        LIMIT 10
    """).fetchall()

    if rows:
        print(f"Found {len(rows)} recent signals:\n")
        for symbol, strategy_id, signal_type, conf, bar_ts, status in rows:
            print(f"{symbol:30} | {strategy_id:15} | {signal_type:5} | {conf:4.2f} | {bar_ts} | {status}")
    else:
        print("No signals found in signals table.")
