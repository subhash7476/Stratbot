#!/usr/bin/env python3
"""
Upstox Instrument Master — Daily Refresh
-----------------------------------------
Downloads the complete Upstox instruments JSON.GZ, filters for NSE_FO
(options and futures), and stores in a local DuckDB for fast symbol lookup.

Run once after OAuth login each morning:
    python scripts/fetch_instrument_master.py

Source:
    https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz

Output:
    data/instruments/nse_fo_instruments.duckdb
    Table: instruments(instrument_key, tradingsymbol, name, expiry, strike,
                       instrument_type, lot_size, exchange)
    Index: tradingsymbol → fast lookup
"""
import sys
import gzip
import json
import logging
import requests
import duckdb
import pyarrow as pa
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
DB_PATH = ROOT / "data" / "instruments" / "nse_fo_instruments.duckdb"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS instruments (
    instrument_key  TEXT PRIMARY KEY,
    tradingsymbol   TEXT NOT NULL,
    name            TEXT,
    expiry          TEXT,
    strike          REAL,
    instrument_type TEXT,
    lot_size        INTEGER,
    exchange        TEXT
);
"""

_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_tradingsymbol ON instruments (tradingsymbol);"


def _parse_expiry(raw) -> str | None:
    """Convert Upstox expiry field (ms timestamp or date string) to YYYY-MM-DD."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and raw > 0:
        try:
            return datetime.utcfromtimestamp(raw / 1000).strftime("%Y-%m-%d")
        except Exception:
            return None
    if isinstance(raw, str) and raw:
        # Accept ISO date or datetime strings
        return raw[:10]
    return None


def download_and_parse() -> list[dict]:
    """Download and decompress Upstox instrument master. Returns NSE_FO rows."""
    logger.info(f"Downloading instrument master from {INSTRUMENTS_URL} ...")
    resp = requests.get(INSTRUMENTS_URL, timeout=60)
    resp.raise_for_status()

    raw_json = gzip.decompress(resp.content)
    instruments = json.loads(raw_json)
    logger.info(f"Total instruments in master: {len(instruments):,}")

    rows = []
    for item in instruments:
        # Accept both 'segment' and 'exchange' as the segment identifier
        segment = item.get("segment") or item.get("exchange", "")
        if segment not in ("NSE_FO", "MCX_FO"):
            continue

        ikey = item.get("instrument_key") or item.get("key")
        if not ikey:
            continue

        # Normalise instrument_type
        itype = (
            item.get("instrument_type")
            or item.get("option_type")
            or ""
        ).upper()

        expiry_raw = item.get("expiry") or item.get("expiry_date")
        strike = item.get("strike_price") or item.get("strike") or 0.0

        rows.append({
            "instrument_key":  ikey,
            "tradingsymbol":   item.get("tradingsymbol") or item.get("trading_symbol") or "",
            "name":            item.get("name") or "",
            "expiry":          _parse_expiry(expiry_raw),
            "strike":          float(strike) if strike else 0.0,
            "instrument_type": itype,
            "lot_size":        int(item.get("lot_size") or 0),
            "exchange":        segment,
        })

    logger.info(f"F&O instruments parsed (NSE + MCX): {len(rows):,}")
    return rows


def refresh(db_path: Path = DB_PATH) -> int:
    """Download master and upsert into local DuckDB. Returns rows written."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    rows = download_and_parse()
    if not rows:
        logger.error("No F&O rows parsed — aborting DB write")
        return 0

    arrow_data = pa.table({
        "instrument_key":  [r["instrument_key"] for r in rows],
        "tradingsymbol":   [r["tradingsymbol"] for r in rows],
        "name":            [r["name"] for r in rows],
        "expiry":          [r["expiry"] for r in rows],
        "strike":          [r["strike"] for r in rows],
        "instrument_type": [r["instrument_type"] for r in rows],
        "lot_size":        [r["lot_size"] for r in rows],
        "exchange":        [r["exchange"] for r in rows],
    })

    con = duckdb.connect(str(db_path))
    try:
        con.execute("DROP TABLE IF EXISTS instruments")
        con.execute(_CREATE_TABLE)
        con.execute("INSERT INTO instruments SELECT * FROM arrow_data")
        con.execute(_CREATE_INDEX)
        count = con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
        logger.info(f"Instrument master refreshed: {count:,} F&O rows (NSE + MCX) in {db_path}")
        return count
    finally:
        con.close()


def get_db_path() -> Path:
    return DB_PATH


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )
    n = refresh()
    print(f"\nDone — {n:,} F&O instruments (NSE + MCX) stored at {DB_PATH}")
