"""
Refactored Backtest Facade
------------------------
Bridge for backtesting results in the UI using isolated databases.
"""
from typing import List, Dict, Optional, Any
import pandas as pd
from core.database.manager import DatabaseManager

class BacktestFacade:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def get_all_runs(self) -> List[Dict[str, Any]]:
        """Returns all backtest run summaries from the index DB."""
        try:
            with self.db.backtest_index_reader() as conn:
                df = pd.read_sql_query("SELECT * FROM backtest_runs ORDER BY created_at DESC", conn)
                return df.to_dict(orient='records')
        except Exception as e:
            print(f"[BACKTEST FACADE] Error getting runs: {e}")
            return []

    def get_run_trades(self, run_id: str) -> List[Dict[str, Any]]:
        """Returns detailed trades for a specific run from its isolated DuckDB file."""
        try:
            with self.db.backtest_reader(run_id) as conn:
                df = conn.execute("SELECT * FROM trades ORDER BY entry_ts ASC").df()
                return df.to_dict(orient='records')
        except Exception as e:
            print(f"[BACKTEST FACADE] Error getting trades for {run_id}: {e}")
            return []
