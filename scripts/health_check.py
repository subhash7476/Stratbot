#!/usr/bin/env python3
"""
Database health check script for refactored isolated architecture.
"""
from pathlib import Path
import sys
import os
import sqlite3
import duckdb
import shutil
from datetime import datetime

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.logging import setup_logger

logger = setup_logger("health_check")

class HealthCheck:
    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.errors = []
        self.warnings = []

    def run_all(self) -> bool:
        """Run all health checks. Returns True if healthy."""
        logger.info(f"Running Health Check on {self.data_root}...")

        self.check_directory_structure()
        self.check_lock_files()
        self.check_database_integrity()
        self.check_disk_space()

        if self.errors:
            logger.error("\n[ERROR] HEALTH CHECK FAILED")
            for e in self.errors:
                logger.error(f"  ERROR: {e}")
            return False

        if self.warnings:
            logger.warning("\n[WARNING] HEALTH CHECK PASSED WITH WARNINGS")
            for w in self.warnings:
                logger.warning(f"  WARNING: {w}")
        else:
            logger.info("\n[SUCCESS] HEALTH CHECK PASSED")

        return True

    def check_directory_structure(self):
        """Verify expected directories exist."""
        required_dirs = [
            'market_data',
            'live_buffer',
            'trading',
            'signals',
            'backtest/runs',
            'config',
        ]
        for d in required_dirs:
            path = self.data_root / d
            if not path.exists():
                self.warnings.append(f"Directory missing: {path}")

    def check_lock_files(self):
        """Check for orphaned lock files."""
        lock_files = list(self.data_root.rglob('.writer.lock'))
        for lock_file in lock_files:
            try:
                with open(lock_file, 'r') as f:
                    content = f.read().strip()
                    if not content:
                        continue
                    pid = int(content)
                    
                    # Check if process is still running (Windows compatible)
                    import ctypes
                    PROCESS_QUERY_INFORMATION = 0x0400
                    process_handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
                    if not process_handle:
                        self.warnings.append(f"Orphaned lock file (PID {pid} not running): {lock_file}")
                    else:
                        ctypes.windll.kernel32.CloseHandle(process_handle)
            except (ValueError, FileNotFoundError):
                pass

    def check_database_integrity(self):
        """Run integrity checks on all databases."""
        # Check DuckDB files
        for db_path in self.data_root.rglob('*.duckdb'):
            try:
                conn = duckdb.connect(str(db_path), read_only=True)
                # DuckDB basic check
                conn.execute("SELECT 1").fetchall()
                conn.close()
            except Exception as e:
                self.errors.append(f"Cannot open DuckDB: {db_path} - {e}")

        # Check SQLite files
        for db_path in self.data_root.rglob('*.db'):
            try:
                # Use URI for read-only to avoid lock conflicts
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                result = conn.execute("PRAGMA integrity_check").fetchone()
                conn.close()
                if result[0] != 'ok':
                    self.errors.append(f"Integrity check failed: {db_path} ({result[0]})")
            except Exception as e:
                self.errors.append(f"Cannot open SQLite: {db_path} - {e}")

    def check_disk_space(self):
        """Check available disk space."""
        total, used, free = shutil.disk_usage(self.data_root)
        free_gb = free / (1024**3)
        if free_gb < 1: # Low threshold for dev env, change for prod
            self.errors.append(f"Low disk space: {free_gb:.1f} GB free")
        elif free_gb < 5:
            self.warnings.append(f"Disk space warning: {free_gb:.1f} GB free")

if __name__ == '__main__':
    data_root = ROOT / "data"
    checker = HealthCheck(data_root)
    sys.exit(0 if checker.run_all() else 1)
