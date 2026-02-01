"""
Trade Recorder
--------------
Stateless utility to persist completed trades.
"""
from core.events import TradeEvent
from core.data.analytics_persistence import save_trade

class TradeRecorder:
    def __init__(self, db_path: str = "data/trading_bot.duckdb"):
        self.db_path = db_path

    def record(self, trade: TradeEvent):
        """Persists trade to DuckDB."""
        return save_trade(trade, self.db_path)
