"""
Instrument Master Lookup
-------------------------
Fast symbol → instrument_key resolution from the local NSE_FO DuckDB.
Populated daily by scripts/fetch_instrument_master.py.

Usage:
    from core.instruments.instrument_db import InstrumentMaster
    im = InstrumentMaster()
    key = im.resolve("NIFTY10MAR2622500CE")   # "NSE_FO|123456"
    rows = im.find_options("NIFTY", expiry="2026-03-10", strike=22500)
"""
import logging
import duckdb
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "instruments" / "nse_fo_instruments.duckdb"


class InstrumentMaster:
    """Read-only lookup against the locally cached NSE_FO instrument master."""

    def __init__(self, db_path: Path = _DB_PATH):
        self._db_path = db_path
        self._loaded = db_path.exists()
        if not self._loaded:
            logger.warning(
                f"[InstrumentMaster] DB not found at {db_path}. "
                "Run scripts/fetch_instrument_master.py first."
            )

    def _con(self):
        return duckdb.connect(str(self._db_path), read_only=True)

    def resolve(self, tradingsymbol: str) -> Optional[str]:
        """
        Return the Upstox instrument_key for a given trading symbol.
        e.g. "NIFTY10MAR2622500CE" → "NSE_FO|123456"
        Returns None if not found or DB not loaded.
        """
        if not self._loaded:
            return None
        try:
            con = self._con()
            row = con.execute(
                "SELECT instrument_key FROM instruments WHERE tradingsymbol = ? LIMIT 1",
                [tradingsymbol]
            ).fetchone()
            con.close()
            return row[0] if row else None
        except Exception as exc:
            logger.warning(f"[InstrumentMaster] resolve({tradingsymbol}) failed: {exc}")
            return None

    def find_options(
        self,
        name: str,
        expiry: str,
        strike: float,
        option_type: Optional[str] = None,
    ) -> list[dict]:
        """
        Find option contracts by name, expiry (YYYY-MM-DD), strike.
        Optionally filter by option_type ('CE' or 'PE').
        Returns list of {instrument_key, tradingsymbol, lot_size}.
        """
        if not self._loaded:
            return []
        try:
            con = self._con()
            query = """
                SELECT instrument_key, tradingsymbol, lot_size
                FROM instruments
                WHERE name = ?
                  AND expiry = ?
                  AND ABS(strike - ?) < 0.01
            """
            params = [name, expiry, float(strike)]
            if option_type:
                query += " AND instrument_type = ?"
                params.append(option_type.upper())
            rows = con.execute(query, params).fetchall()
            con.close()
            return [
                {"instrument_key": r[0], "tradingsymbol": r[1], "lot_size": r[2]}
                for r in rows
            ]
        except Exception as exc:
            logger.warning(f"[InstrumentMaster] find_options failed: {exc}")
            return []

    def is_loaded(self) -> bool:
        return self._loaded and self._db_path.exists()

    def row_count(self) -> int:
        if not self.is_loaded():
            return 0
        try:
            con = self._con()
            n = con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
            con.close()
            return n
        except Exception:
            return 0
