"""
Global Settings
"""
import os
import json
from pathlib import Path

DB_PATH = os.environ.get("TRADING_DB_PATH", "data/trading_bot.duckdb")
LOG_LEVEL = "INFO"

def load_zmq_config():
    """Load ZMQ configuration from config file."""
    zmq_config_path = Path(__file__).parent / "zmq.json"
    with open(zmq_config_path, "r") as f:
        return json.load(f)
