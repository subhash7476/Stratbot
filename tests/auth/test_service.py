from core.auth.auth_service import AuthService
from core.data.duckdb_client import db_cursor
from core.data.schema import BOOTSTRAP_STATEMENTS
import pytest

def test_auth_logic(temp_db):
    # Initialize schema
    with db_cursor(temp_db) as conn:
        for stmt in BOOTSTRAP_STATEMENTS:
            conn.execute(stmt)

    auth = AuthService(temp_db)
    # 1. Register
    assert auth.register_user("admin", "password123", ["admin"]) == True
    # 2. Authenticate
    user = auth.authenticate("admin", "password123")
    assert user is not None
    assert user.username == "admin"
    assert "admin" in user.roles
    # 3. Fail
    assert auth.authenticate("admin", "wrong") is None
