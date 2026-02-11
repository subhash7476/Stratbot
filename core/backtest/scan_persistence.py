"""
Scan Persistence — Save/Load Scanner Results to SQLite
------------------------------------------------------
Persists ScanResults and per-symbol results for historical comparison
and UI display.
"""
import json
import logging
from dataclasses import asdict
from typing import List, Dict, Optional, Any

import pandas as pd

from core.database.manager import DatabaseManager
from core.database import schema

logger = logging.getLogger(__name__)


class ScanPersistence:
    """Handles saving and loading scanner results to/from SQLite."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self._ensure_tables()

    def _ensure_tables(self):
        """Create scanner tables if they don't exist."""
        try:
            with self.db.scanner_writer() as conn:
                conn.execute(schema.SCANNER_RESULTS_SCHEMA)
                conn.execute(schema.SCANNER_SYMBOL_RESULTS_SCHEMA)
        except FileNotFoundError:
            # First time — scanner_writer creates the directory
            with self.db.scanner_writer() as conn:
                conn.execute(schema.SCANNER_RESULTS_SCHEMA)
                conn.execute(schema.SCANNER_SYMBOL_RESULTS_SCHEMA)

    def save_scan(self, scan) -> None:
        """Persist a ScanResults object (from symbol_scanner.py)."""
        with self.db.scanner_writer() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO scanner_results
                   (scan_id, scan_timestamp, total_symbols, profitable_symbols, scan_params, status)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    scan.scan_id,
                    scan.timestamp.isoformat(),
                    scan.total_symbols,
                    scan.profitable_symbols,
                    json.dumps(scan.scan_params, default=str),
                    scan.status,
                ],
            )

            for result in scan.symbol_results:
                conn.execute(
                    """INSERT OR REPLACE INTO scanner_symbol_results
                       (scan_id, symbol, trading_symbol,
                        train_pnl, train_trades, train_win_rate, train_max_dd,
                        train_run_id, train_status,
                        test_pnl, test_trades, test_win_rate, test_max_dd,
                        test_run_id, test_status,
                        is_profitable, rank, error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        scan.scan_id,
                        result.symbol,
                        result.trading_symbol,
                        result.train_pnl,
                        result.train_trades,
                        result.train_win_rate,
                        result.train_max_dd,
                        result.train_run_id,
                        result.train_status,
                        result.test_pnl,
                        result.test_trades,
                        result.test_win_rate,
                        result.test_max_dd,
                        result.test_run_id,
                        result.test_status,
                        1 if result.is_profitable else 0,
                        result.rank,
                        result.error or "",
                    ],
                )

        logger.info(f"Saved scan {scan.scan_id}: {scan.profitable_symbols}/{scan.total_symbols} profitable")

    def get_all_scans(self) -> List[Dict[str, Any]]:
        """Return all scan summaries ordered by most recent first."""
        try:
            with self.db.scanner_reader() as conn:
                df = pd.read_sql_query(
                    "SELECT * FROM scanner_results ORDER BY created_at DESC", conn
                )
                return df.to_dict(orient="records")
        except (FileNotFoundError, Exception) as e:
            logger.warning(f"No scanner data available: {e}")
            return []

    def get_scan_results(self, scan_id: str) -> Dict[str, Any]:
        """Return full details for a specific scan including per-symbol results."""
        try:
            with self.db.scanner_reader() as conn:
                # Scan summary
                scan_row = conn.execute(
                    "SELECT * FROM scanner_results WHERE scan_id = ?", [scan_id]
                ).fetchone()
                if not scan_row:
                    return {}

                cols = [d[0] for d in conn.execute("SELECT * FROM scanner_results LIMIT 0").description]
                scan_dict = dict(zip(cols, scan_row))

                # Symbol results
                df = pd.read_sql_query(
                    "SELECT * FROM scanner_symbol_results WHERE scan_id = ? "
                    "ORDER BY is_profitable DESC, rank ASC, test_pnl DESC",
                    conn,
                    params=[scan_id],
                )
                scan_dict["symbol_results"] = df.to_dict(orient="records")
                return scan_dict

        except (FileNotFoundError, Exception) as e:
            logger.warning(f"Failed to load scan {scan_id}: {e}")
            return {}

    def get_profitable_symbols(self, scan_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return only profitable symbols from a scan (or latest scan if none specified)."""
        try:
            with self.db.scanner_reader() as conn:
                if scan_id is None:
                    row = conn.execute(
                        "SELECT scan_id FROM scanner_results WHERE status = 'COMPLETED' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ).fetchone()
                    if not row:
                        return []
                    scan_id = row[0]

                df = pd.read_sql_query(
                    "SELECT * FROM scanner_symbol_results "
                    "WHERE scan_id = ? AND is_profitable = 1 "
                    "ORDER BY rank ASC",
                    conn,
                    params=[scan_id],
                )
                return df.to_dict(orient="records")

        except (FileNotFoundError, Exception) as e:
            logger.warning(f"Failed to load profitable symbols: {e}")
            return []
