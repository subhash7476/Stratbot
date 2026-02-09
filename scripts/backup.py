#!/usr/bin/env python3
import sys
import shutil
import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BackupManager")

class BackupManager:
    """
    Backup manager for critical isolated databases.
    """

    def __init__(self, data_root: Path, backup_root: Path):
        self.data_root = data_root
        self.backup_root = backup_root

    def backup_trading_db(self):
        """Hourly/On-demand backup of trading database."""
        src = self.data_root / 'trading' / 'trading.db'
        if not src.exists():
            logger.warning(f"Skip trading backup: {src} not found")
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst_dir = self.backup_root / 'trading' / datetime.now().strftime("%Y%m%d")
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"trading_{ts}.db"

        # Use SQLite backup API for online consistency
        src_conn = sqlite3.connect(str(src))
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
            logger.info(f"Trading backup created: {dst}")
        finally:
            src_conn.close()
            dst_conn.close()

        # Keep last 24 hours
        self._cleanup_old_backups(dst_dir.parent, keep_hours=24)

    def backup_config_db(self):
        """Daily backup of config database."""
        src = self.data_root / 'config' / 'config.db'
        if not src.exists():
            return

        ts = datetime.now().strftime("%Y%m%d")
        dst_dir = self.backup_root / 'config'
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"config_{ts}.db"

        src_conn = sqlite3.connect(str(src))
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
            logger.info(f"Config backup created: {dst}")
        finally:
            src_conn.close()
            dst_conn.close()

        # Keep last 30 days
        self._cleanup_old_backups(dst_dir, keep_days=30)

    def _cleanup_old_backups(self, directory: Path, keep_hours: int = None, keep_days: int = None):
        """Remove backups older than retention period."""
        if keep_hours:
            cutoff = datetime.now() - timedelta(hours=keep_hours)
        elif keep_days:
            cutoff = datetime.now() - timedelta(days=keep_days)
        else:
            return

        # Walk through subdirectories as well
        for f in directory.rglob('*.db'):
            if f.is_file() and f.stat().st_mtime < cutoff.timestamp():
                try:
                    f.unlink()
                    logger.info(f"  Cleaned up old backup: {f}")
                except Exception as e:
                    logger.error(f"  Failed to delete {f}: {e}")

if __name__ == "__main__":
    data_root = ROOT / "data"
    backup_root = data_root / "backups"
    
    manager = BackupManager(data_root, backup_root)
    
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    
    if cmd in ["trading", "all"]:
        manager.backup_trading_db()
    if cmd in ["config", "all"]:
        manager.backup_config_db()
