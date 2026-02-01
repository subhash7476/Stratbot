"""
Authentication Service
----------------------
Core business logic for user management and session verification.
"""
import logging
from typing import Optional, List
from core.data.duckdb_client import db_cursor
from core.auth.password import verify_password, hash_password
from core.auth.models import User

logger = logging.getLogger(__name__)

class AuthService:
    """
    Handles user authentication and registration.
    """
    
    def __init__(self, db_path: str = "data/trading_bot.duckdb"):
        self.db_path = db_path

    def authenticate(self, username: str, password: str) -> Optional[User]:
        """Verifies credentials and returns User object if successful."""
        query = "SELECT username, password_hash, roles FROM users WHERE username = ?"
        try:
            with db_cursor(self.db_path, read_only=True) as conn:
                row = conn.execute(query, [username]).fetchone()
                if row and verify_password(password, row[1]):
                    return User(
                        username=row[0],
                        roles=row[2].split(",") if row[2] else []
                    )
        except Exception as e:
            logger.error(f"Authentication error: {e}")
        return None

    def register_user(self, username: str, password: str, roles: Optional[List[str]] = None) -> bool:
        """Creates a new user record."""
        roles_str = ",".join(roles) if roles else "viewer"
        pw_hash = hash_password(password)
        
        query = "INSERT INTO users (username, password_hash, roles) VALUES (?, ?, ?)"
        try:
            with db_cursor(self.db_path) as conn:
                conn.execute(query, [username, pw_hash, roles_str])
            return True
        except Exception as e:
            logger.error(f"Registration failed: {e}")
            return False
