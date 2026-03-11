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
from datetime import date
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

    def resolve_active_future(self, name: str, symbol_prefix: str = None,
                              as_of: date = None) -> Optional[dict]:
        """Resolve the nearest non-expired futures contract by commodity name.

        Args:
            name: Upstox name field, e.g. "CRUDE OIL", "GOLD"
            symbol_prefix: tradingsymbol prefix to disambiguate variants,
                           e.g. "CRUDEOIL FUT" (excludes CRUDEOILM), "GOLDM FUT"
            as_of: reference date (defaults to today)
        """
        if not self._loaded:
            return None
        ref_date = (as_of or date.today()).isoformat()
        try:
            con = self._con()
            if symbol_prefix:
                row = con.execute(
                    """SELECT instrument_key, tradingsymbol, name, expiry, lot_size
                       FROM instruments
                       WHERE name = ?
                         AND instrument_type = 'FUT'
                         AND tradingsymbol LIKE ?
                         AND expiry >= ?
                       ORDER BY expiry ASC
                       LIMIT 1""",
                    [name, symbol_prefix + "%", ref_date]
                ).fetchone()
            else:
                row = con.execute(
                    """SELECT instrument_key, tradingsymbol, name, expiry, lot_size
                       FROM instruments
                       WHERE name = ?
                         AND instrument_type = 'FUT'
                         AND expiry >= ?
                       ORDER BY expiry ASC
                       LIMIT 1""",
                    [name, ref_date]
                ).fetchone()
            con.close()
            if row:
                return {
                    "instrument_key": row[0],
                    "tradingsymbol": row[1],
                    "name": row[2],
                    "expiry": row[3],
                    "lot_size": row[4],
                }
            return None
        except Exception as exc:
            logger.warning(f"[InstrumentMaster] resolve_active_future({name}) failed: {exc}")
            return None

    def get_lot_size(self, instrument_key: str) -> int:
        """Return lot_size for an instrument_key, or 1 if not found."""
        if not self._loaded:
            return 1
        try:
            con = self._con()
            row = con.execute(
                "SELECT lot_size FROM instruments WHERE instrument_key = ? LIMIT 1",
                [instrument_key]
            ).fetchone()
            con.close()
            return row[0] if row and row[0] else 1
        except Exception:
            return 1

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

    def resolve_option(self, name: str, expiry: "date", strike: float,
                       option_type: str) -> Optional[str]:
        """Resolve instrument_key by structured fields (bypasses tradingsymbol format mismatch).
        e.g. resolve_option("NIFTY", date(2026,3,10), 22500, "CE") → "NSE_FO|54710"
        Falls back to nearest available strike if exact match not found.
        """
        expiry_str = expiry.strftime("%Y-%m-%d")
        results = self.find_options(name, expiry_str, strike, option_type)
        if results:
            return results[0]["instrument_key"]
        # Fallback: nearest strike for this expiry
        return self._nearest_strike_key(name, expiry_str, strike, option_type)

    def _nearest_strike_key(self, name: str, expiry: str, strike: float,
                            option_type: str) -> Optional[str]:
        """Find the nearest available strike when exact ATM isn't listed."""
        if not self._loaded:
            return None
        try:
            con = self._con()
            row = con.execute(
                """SELECT instrument_key, strike FROM instruments
                   WHERE name = ? AND expiry = ? AND instrument_type = ?
                   ORDER BY ABS(strike - ?) LIMIT 1""",
                [name, expiry, option_type.upper(), float(strike)]
            ).fetchone()
            con.close()
            if row:
                logger.info(
                    f"[InstrumentMaster] Nearest strike fallback: "
                    f"requested {strike:.0f}, found {row[1]:.0f} → {row[0]}"
                )
                return row[0]
            return None
        except Exception as exc:
            logger.warning(f"[InstrumentMaster] nearest_strike lookup failed: {exc}")
            return None

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
