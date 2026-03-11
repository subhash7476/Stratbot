#!/usr/bin/env python3
"""
Daily Historical Fill
---------------------
After OAuth login, fetch yesterday's complete 1m candle data (9:15–15:29)
for all Nifty 200 stocks + Nifty 50 + BankNifty from Upstox historical API.

Replaces the old EOD rollover mechanism which was unreliable (depended on
live buffer having complete data from uninterrupted WebSocket sessions).

Can be run standalone:
    python scripts/daily_historical_fill.py
"""
import sys
import json
import time
import threading
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.api.upstox_client import UpstoxClient
from core.database.manager import DatabaseManager
from core.database import schema
from core.database.utils.symbol_utils import get_exchange_from_key
from core.logging import setup_logger

logger = setup_logger("daily_fill")

UNIVERSE_PATH = ROOT / "config" / "market_universe.json"
INDEX_SYMBOLS = ["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"]
RATE_LIMIT = 8  # Upstox V3: 10 req/s, we use 8 for safety


class _RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.calls: List[float] = []
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            self.calls = [c for c in self.calls if c > now - self.period]
            if len(self.calls) >= self.max_calls:
                sleep_time = self.calls[0] + self.period - now
                if sleep_time > 0:
                    time.sleep(sleep_time)
            self.calls.append(time.time())


def _get_previous_trading_day(ref: Optional[date] = None) -> date:
    """Return the most recent trading day before `ref` (skips weekends)."""
    d = (ref or date.today()) - timedelta(days=1)
    while d.weekday() >= 5:  # Sat=5, Sun=6
        d -= timedelta(days=1)
    return d


def _load_symbols() -> List[str]:
    """Load equity symbols from market_universe.json + index symbols."""
    symbols = []
    if UNIVERSE_PATH.exists():
        with open(UNIVERSE_PATH) as f:
            data = json.load(f)
        symbols = data.get("symbols", [])
    symbols.extend(INDEX_SYMBOLS)
    return symbols


def _check_existing(db_manager: DatabaseManager, target_date: date, exchange: str = "nse") -> bool:
    """Return True if the historical file for target_date already has substantial data."""
    path = db_manager.data_root / "market_data" / exchange / "candles" / "1m" / f"{target_date}.duckdb"
    if not path.exists():
        return False
    try:
        import duckdb
        conn = duckdb.connect(str(path), read_only=True)
        count = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        conn.close()
        return count > 1000  # >1000 rows means substantial data already present
    except Exception:
        return False


def _batch_insert_rows(conn, rows):
    """Vectorized DuckDB insert via Pandas DataFrame."""
    if not rows:
        return
    df = pd.DataFrame(rows, columns=[
        "symbol", "timeframe", "timestamp", "open", "high", "low", "close", "volume"
    ])
    conn.execute("""
        INSERT INTO candles
        (symbol, timeframe, timestamp, open, high, low, close, volume, is_synthetic)
        SELECT symbol, timeframe, timestamp, open, high, low, close, volume, FALSE
        FROM df
        ON CONFLICT (symbol, timeframe, timestamp) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            is_synthetic = FALSE
    """)


def fill_previous_day(
    token: str,
    db_manager: DatabaseManager,
    target_date: Optional[date] = None,
) -> Dict:
    """
    Fetch yesterday's (or target_date's) 1m candles for the full universe.

    Returns:
        {"date": "2026-03-03", "symbols_fetched": 195, "total_bars": 72000, "skipped": False}
    """
    target = target_date or _get_previous_trading_day()
    date_str = target.isoformat()

    # Idempotency: skip if already filled
    if _check_existing(db_manager, target):
        logger.info(f"[DailyFill] {date_str} already has data — skipping.")
        return {"date": date_str, "symbols_fetched": 0, "total_bars": 0, "skipped": True}

    symbols = _load_symbols()
    logger.info(f"[DailyFill] Fetching {date_str} for {len(symbols)} symbols...")

    client = UpstoxClient(access_token=token)
    limiter = _RateLimiter(RATE_LIMIT, 1.0)

    # Collect all rows grouped by (exchange, date) for batch write
    rows_by_exchange: Dict[str, list] = defaultdict(list)
    fetched_count = 0
    total_bars = 0
    errors = 0

    for i, symbol in enumerate(symbols):
        limiter.wait()
        try:
            candles = client.fetch_historical_candles_v3(
                instrument_key=symbol,
                unit="minutes",
                interval=1,
                to_date=date_str,
                from_date=date_str,
            )
            if candles:
                exchange = get_exchange_from_key(symbol)
                for c in candles:
                    ts = c["timestamp"]
                    ts_naive = ts.replace(tzinfo=None) if ts.tzinfo else ts
                    rows_by_exchange[exchange].append((
                        symbol, "1m", ts_naive,
                        c["open"], c["high"], c["low"], c["close"], int(c["volume"])
                    ))
                    total_bars += 1
                fetched_count += 1
        except Exception as e:
            errors += 1
            logger.debug(f"[DailyFill] {symbol}: {e}")

        # Progress every 50 symbols
        if (i + 1) % 50 == 0:
            logger.info(f"[DailyFill] Progress: {i + 1}/{len(symbols)} symbols fetched...")

    # Write to historical archive
    for exchange, rows in rows_by_exchange.items():
        try:
            with db_manager.historical_writer(exchange, "candles", "1m", target) as conn:
                conn.execute(schema.MARKET_CANDLES_SCHEMA)
                _batch_insert_rows(conn, rows)
                logger.info(f"[DailyFill] Written {len(rows)} bars to {date_str} ({exchange})")
        except Exception as e:
            logger.error(f"[DailyFill] DB write failed for {exchange}/{date_str}: {e}")

    logger.info(
        f"[DailyFill] Done: {date_str} | {fetched_count}/{len(symbols)} symbols | "
        f"{total_bars} bars | {errors} errors"
    )
    return {
        "date": date_str,
        "symbols_fetched": fetched_count,
        "total_bars": total_bars,
        "skipped": False,
    }


# ── Standalone entry point ─────────────────────────────────────────────────
if __name__ == "__main__":
    from core.auth.credentials import credentials
    credentials._load()
    token = credentials.get("access_token")
    if not token:
        print("ERROR: No Upstox token found. Login via dashboard first.")
        sys.exit(1)

    data_root = ROOT / "data"
    db_manager = DatabaseManager(data_root)
    result = fill_previous_day(token, db_manager)
    print(f"Result: {result}")
