"""
Global Settings
"""
import os

DB_PATH = os.environ.get("TRADING_DB_PATH", "data/trading_bot.duckdb")
LOG_LEVEL = "INFO"
