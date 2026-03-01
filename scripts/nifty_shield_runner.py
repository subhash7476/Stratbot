#!/usr/bin/env python3
"""
NiftyShield Paper Trading Runner
---------------------------------
Background thread that:
  1. Polls the live buffer (candles) every 30 s
  2. Feeds new 1-minute Nifty + BankNifty bars to NiftyShieldStrategy
  3. Handles session resets at EOD
"""
import sys
import time
import logging
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager
from core.database.utils.market_hours import MarketHours
from core.strategies.nifty_shield_strategy import NiftyShieldStrategy, NF_SYMBOL
from core.logging import setup_logger

logger = setup_logger("nifty_shield_runner")

POLL_SECS = 30
BN_SYMBOL = "NSE_INDEX|Nifty Bank"


class NiftyShieldRunner:
    """
    Continuously polls the live buffer and feeds 1m bars to NiftyShieldStrategy.
    Follows V9PMRunner pattern exactly.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db       = db_manager
        self.strategy = NiftyShieldStrategy(db_manager)

        self._last_nf_ts: Optional[datetime] = None
        self._last_bn_ts: Optional[datetime] = None
        self._session_date: Optional[date]   = None

    def run(self, stop_event: threading.Event):
        logger.info(f"[NSRunner] Started | symbol={NF_SYMBOL}")

        while not stop_event.is_set():
            now   = MarketHours.get_ist_now()
            today = now.date()

            if MarketHours.is_market_open(now):
                if self._session_date != today:
                    logger.info(f"[NSRunner] New session: {today}")
                    self._last_nf_ts   = None
                    self._last_bn_ts   = None
                    self._session_date = today
                    self.strategy.on_session_start(today)

                self._process_new_bars()

            stop_event.wait(timeout=POLL_SECS)

        logger.info("[NSRunner] Stopped.")

    def _process_new_bars(self):
        try:
            nf_bars = self._fetch_bars(NF_SYMBOL, self._last_nf_ts)
            bn_bars = self._fetch_bars(BN_SYMBOL, self._last_bn_ts)

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
            logger.error(f"[NSRunner] Bar processing error: {exc}", exc_info=True)

    def _fetch_bars(self, symbol: str, last_ts: Optional[datetime]) -> List[Dict]:
        try:
            with self.db.live_buffer_reader() as conns:
                if "candles" not in conns:
                    return []
                conn = conns["candles"]
                if last_ts is None:
                    rows = conn.execute(
                        "SELECT timestamp, open, high, low, close, volume "
                        "FROM candles WHERE symbol=? AND timeframe='1m' "
                        "ORDER BY timestamp ASC",
                        [symbol]
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT timestamp, open, high, low, close, volume "
                        "FROM candles WHERE symbol=? AND timeframe='1m' "
                        "AND timestamp > ? ORDER BY timestamp ASC",
                        [symbol, last_ts]
                    ).fetchall()
                return [
                    {"timestamp": r[0], "open": r[1], "high": r[2],
                     "low": r[3], "close": r[4], "volume": r[5]}
                    for r in rows
                ]
        except Exception as exc:
            logger.warning(f"[NSRunner] Fetch bars failed ({symbol}): {exc}")
            return []
