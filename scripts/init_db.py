import sys
import os
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database import db_cursor, BOOTSTRAP_STATEMENTS

# Hardening tweak: Configurable path with environment variable
DB_PATH = os.environ.get("TRADING_DB_PATH", "data/trading_bot.duckdb")

def bootstrap():
    print(f"Initializing database at {DB_PATH}...")
    with db_cursor(DB_PATH) as conn:
        # Check for legacy roles table
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info('roles')").fetchall()]
            if 'name' in cols and 'role_name' not in cols:
                print("Migrating legacy 'roles' table...")
                conn.execute("ALTER TABLE roles RENAME COLUMN name TO role_name")
        except Exception:
            pass

        # Check for legacy users table
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info('users')").fetchall()]
            if 'roles' not in cols:
                print("Adding 'roles' column to 'users' table...")
                conn.execute("ALTER TABLE users ADD COLUMN roles TEXT")
        except Exception:
            pass

        # Hardening tweak: Explicit transaction for all-or-nothing bootstrap
        conn.execute("BEGIN TRANSACTION;")
        try:
            for statement in BOOTSTRAP_STATEMENTS:
                conn.execute(statement)
            conn.execute("COMMIT;")
            print("Database initialization complete. Roles seeded: admin, viewer.")
        except Exception as e:
            conn.execute("ROLLBACK;")
            print(f"Bootstrap failed: {e}")
            sys.exit(1)

if __name__ == "__main__":
    bootstrap()
