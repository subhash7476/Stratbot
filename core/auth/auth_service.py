"""
Authentication Service
----------------------
Core business logic for user management and session verification.
"""
import logging
from typing import Optional, List
from pathlib import Path

from core.database.manager import DatabaseManager
from core.auth.password import verify_password, hash_password
from core.auth.models import User

logger = logging.getLogger(__name__)

class AuthService:
    """
    Handles user authentication and registration using isolated config database.
    """
    
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager(Path("data"))

    def authenticate(self, username: str, password: str) -> Optional[User]:
        """Verifies credentials and returns User object if successful."""
        query = "SELECT username, password_hash, roles FROM users WHERE username = ?"
        logger.info(f"Attempting authentication for user: {username}")
        try:
            with self.db.config_reader() as conn:
                row = conn.execute(query, [username]).fetchone()
                if row:
                    logger.info(f"User {username} found in database. Verifying password...")
                    if verify_password(password, row[1]):
                        logger.info(f"Authentication successful for user: {username}")
                        return User(
                            username=row[0],
                            roles=row[2].split(",") if row[2] else []
                        )
                    else:
                        logger.warning(f"Password verification failed for user: {username}")
                else:
                    logger.warning(f"User {username} not found in database.")
        except Exception as e:
            logger.error(f"Authentication error for {username}: {e}", exc_info=True)
        return None

    def register_user(self, username: str, password: str, roles: Optional[List[str]] = None) -> bool:
        """Creates a new user record in config database."""
        roles_str = ",".join(roles) if roles else "viewer"
        pw_hash = hash_password(password)
        
        query = "INSERT INTO users (username, password_hash, roles) VALUES (?, ?, ?)"
        try:
            with self.db.config_writer() as conn:
                conn.execute(query, [username, pw_hash, roles_str])
            return True
        except Exception as e:
            logger.error(f"Registration failed: {e}")
            return False
