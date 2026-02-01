"""
Data Facade
-----------
Bridge for raw data access in the UI.
"""
from core.data.duckdb_client import db_cursor

class DataFacade:
    def get_tables(self):
        with db_cursor(read_only=True) as conn:
            return [row[0] for row in conn.execute("SHOW TABLES").fetchall()]
