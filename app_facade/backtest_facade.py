"""
Backtest Facade
---------------
Bridge for backtesting results in the UI.
"""
from core.data.duckdb_client import db_cursor

class BacktestFacade:
    def get_all_runs(self):
        with db_cursor(read_only=True) as conn:
            return conn.execute("SELECT * FROM backtest_runs").fetchdf().to_dict(orient='records')
