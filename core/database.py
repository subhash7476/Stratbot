"""
Core Database Utilities
-----------------------
DEPRECATED: Use core.database package instead.

This file is kept for backward compatibility only.
"""
from core.database.legacy_adapter import get_connection

def get_db():
    """Compatibility wrapper for get_connection."""
    return get_connection()
