"""
Core Database Utilities
-----------------------
Legacy or helper methods for database interaction.
"""
from core.data.duckdb_client import get_connection

def get_db():
    """Compatibility wrapper for get_connection."""
    return get_connection()
