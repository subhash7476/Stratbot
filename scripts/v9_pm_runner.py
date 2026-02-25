#!/usr/bin/env python3
"""
V9 PM Scalper Paper Trading Runner
----------------------------------
Background thread that:
  1. Polls the live buffer (candles_today.duckdb) every ~30 s
  2. Feeds new 1-minute Nifty bars to V9PMScalperStrategy
  3. Handles session resets at EOD
"""
import sys
import time
import logging
import threading
from datetime import datetime, date, time as dt_time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager
from core.database.utils.market_hours import MarketHours
from core.strategies.v9_pm_scalper_strategy import V9PMScalperStrategy, SYMBOL
from core.logging import setup_logger

logger = setup_logger("v9_pm_runner")

POLL_SECS = 30
BN_SYMBOL = 'NSE_INDEX|Nifty Bank'

class V9PMRunner:
    """
    Continuously polls the live buffer and feeds 1m bars to the V9 PM Scalper strategy.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.strategy = V9PMScalperStrategy(db_manager)

        self._last_nf_ts: Optional[datetime] = None
        self._last_bn_ts: Optional[datetime] = None
        self._session_date: Optional[date] = None

    def run(self, stop_event: threading.Event):
        """Main loop."""
        logger.info(f"[V9Runner] Started | symbol={SYMBOL}")

        while not stop_event.is_set():
            now = MarketHours.get_ist_now()
            today = now.date()

            if MarketHours.is_market_open(now):
                if self._session_date != today:
                    logger.info(f"[V9Runner] New session: {today}")
                    self._last_nf_ts = None
                    self._last_bn_ts = None
                    self._session_date = today
                
                self._process_new_bars()
            
            stop_event.wait(timeout=POLL_SECS)

        logger.info("[V9Runner] Stopped.")

    def _process_new_bars(self):
        try:
            nf_bars = self._fetch_bars(SYMBOL,    self._last_nf_ts)
            bn_bars = self._fetch_bars(BN_SYMBOL, self._last_bn_ts)

            # Index BN bars by timestamp for O(1) lookup per NF bar
            bn_by_ts = {b["timestamp"]: b for b in bn_bars}

            for bar in nf_bars:
                bn_bar = bn_by_ts.get(bar["timestamp"])
                if bn_bar:
                    self.strategy.on_bn_bar(bn_bar)
                self.strategy.on_bar(bar)

            if nf_bars:
                self._last_nf_ts = nf_bars[-1]["timestamp"]
            if bn_bars:
                self._last_bn_ts = bn_bars[-1]["timestamp"]
        except Exception as exc:
            logger.debug(f"[V9Runner] Process failed: {exc}")

    def _fetch_bars(self, symbol: str, last_ts: Optional[datetime]) -> List[dict]:
        """Read new 1m candles for a symbol from the live buffer since last_ts."""
        try:
            with self.db.live_buffer_reader() as conns:
                if "candles" not in conns:
                    return []
                conn = conns["candles"]

                if last_ts is None:
                    today_open = datetime.combine(date.today(), dt_time(9, 0, 0))
                    rows = conn.execute(
                        """
                        SELECT timestamp, open, high, low, close, volume
                        FROM candles
                        WHERE symbol = ? AND timeframe = '1m' AND timestamp >= ?
                        ORDER BY timestamp ASC
                        """,
                        [symbol, today_open],
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT timestamp, open, high, low, close, volume
                        FROM candles
                        WHERE symbol = ? AND timeframe = '1m' AND timestamp > ?
                        ORDER BY timestamp ASC
                        """,
                        [symbol, last_ts],
                    ).fetchall()

                return [
                    {
                        "timestamp": r[0],
                        "open":      float(r[1]),
                        "high":      float(r[2]),
                        "low":       float(r[3]),
                        "close":     float(r[4]),
                        "volume":    int(r[5]) if r[5] else 0,
                    }
                    for r in rows
                ]
        except Exception as exc:
            logger.debug(f"[V9Runner] Fetch failed ({symbol}): {exc}")
            return []

if __name__ == "__main__":
    from core.database.manager import DatabaseManager
    data_root = ROOT / "data"
    db_manager = DatabaseManager(data_root)
    stop_ev = threading.Event()
    runner = V9PMRunner(db_manager)
    try:
        runner.run(stop_ev)
    except KeyboardInterrupt:
        stop_ev.set()
