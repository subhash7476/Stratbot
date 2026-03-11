#!/usr/bin/env python3
"""
NiftyShield Paper Trading Runner
---------------------------------
Background thread that:
  1. At session start, backfills today's complete intraday bars from Upstox API
  2. Polls the live buffer (candles) every 30 s for new bars
  3. Feeds new 1-minute Nifty + BankNifty bars to NiftyShieldStrategy
  4. Periodically re-backfills from API to fill WebSocket gaps
  5. Handles session resets at EOD
"""
import sys
import time
import threading
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager
from core.database.utils.market_hours import MarketHours
from core.strategies.nifty_shield_strategy import NiftyShieldStrategy, NF_SYMBOL
from core.logging import setup_logger

logger = setup_logger("nifty_shield_runner")

POLL_SECS    = 30
BN_SYMBOL    = "NSE_INDEX|Nifty Bank"
# Re-backfill from API every N minutes to patch WebSocket gaps
BACKFILL_INTERVAL_MINS = 5


class NiftyShieldRunner:
    """
    Continuously polls the live buffer and feeds 1m bars to NiftyShieldStrategy.
    On session start (and periodically) fetches today's complete intraday history
    from the Upstox API so DayTypeEngine always has dense bar data for features.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db       = db_manager
        self.strategy = NiftyShieldStrategy(db_manager)

        self._last_nf_ts: Optional[datetime]  = None
        self._last_bn_ts: Optional[datetime]  = None
        self._session_date: Optional[date]    = None
        self._last_backfill: Optional[datetime] = None

    def run(self, stop_event: threading.Event):
        logger.info(f"[NSRunner] Started | symbol={NF_SYMBOL}")

        while not stop_event.is_set():
            now   = MarketHours.get_ist_now()
            today = now.date()

            if MarketHours.is_market_open(now):
                if self._session_date != today:
                    logger.info(f"[NSRunner] New session: {today}")
                    self._last_nf_ts    = None
                    self._last_bn_ts    = None
                    self._session_date  = today
                    self._last_backfill = None
                    self.strategy.on_session_start(today)
                    # Backfill complete intraday history at session start
                    self._backfill_from_api()

                # Periodic mid-session backfill to patch WebSocket gaps
                elif self._needs_backfill(now):
                    self._backfill_from_api()

                self._process_new_bars()

            stop_event.wait(timeout=POLL_SECS)

        logger.info("[NSRunner] Stopped.")

    # ── API backfill ────────────────────────────────────────────────

    def _needs_backfill(self, now: datetime) -> bool:
        """True if it's time for a periodic gap-fill run."""
        if self._last_backfill is None:
            return True
        return (now - self._last_backfill) >= timedelta(minutes=BACKFILL_INTERVAL_MINS)

    def _backfill_from_api(self):
        """
        Fetch today's complete 1-minute intraday bars from Upstox API for Nifty
        and BankNifty and feed them directly to the strategy.

        This ensures DayTypeEngine has dense bar data even when the WebSocket is
        sparse (typically produces only ~50% of expected 1-minute bars).

        After feeding, _last_nf_ts / _last_bn_ts are advanced so subsequent
        live-buffer reads only return bars AFTER the last API bar (no duplicates).
        """
        try:
            from core.auth.credentials import credentials
            token = credentials.get("access_token")
            if not token:
                logger.warning("[NSRunner] No access token — API backfill skipped")
                return

            from core.api.upstox_client import UpstoxClient
            client = UpstoxClient(access_token=token)

            nf_candles = client.fetch_intraday_candles_v3(NF_SYMBOL, "minutes", 1)
            bn_candles = client.fetch_intraday_candles_v3(BN_SYMBOL, "minutes", 1)

            if not nf_candles:
                logger.warning("[NSRunner] API backfill: no NF candles returned")
                return

            # Sort ascending by timestamp
            nf_candles.sort(key=lambda c: c["timestamp"])
            bn_candles.sort(key=lambda c: c["timestamp"])

            # Strip tz-info so comparisons with live-buffer naive datetimes don't raise
            for c in nf_candles:
                if hasattr(c["timestamp"], "tzinfo") and c["timestamp"].tzinfo is not None:
                    c["timestamp"] = c["timestamp"].replace(tzinfo=None)
            for c in bn_candles:
                if hasattr(c["timestamp"], "tzinfo") and c["timestamp"].tzinfo is not None:
                    c["timestamp"] = c["timestamp"].replace(tzinfo=None)

            # Exclude bars we've already processed (avoid duplicate-feeding)
            if self._last_nf_ts is not None:
                nf_candles = [c for c in nf_candles if c["timestamp"] > self._last_nf_ts]
            if self._last_bn_ts is not None:
                bn_candles = [c for c in bn_candles if c["timestamp"] > self._last_bn_ts]

            if not nf_candles:
                logger.info("[NSRunner] API backfill: no new bars since last feed")
                self._last_backfill = MarketHours.get_ist_now()
                return

            bn_by_ts = {c["timestamp"]: c for c in bn_candles}
            fed = 0
            for candle in nf_candles:
                ts = candle["timestamp"]
                bn = bn_by_ts.get(ts)
                if bn:
                    self.strategy.on_bn_bar(bn)
                self.strategy.on_bar(candle)
                fed += 1

            # Advance last-seen timestamps so live buffer doesn't re-feed these bars
            self._last_nf_ts = nf_candles[-1]["timestamp"]
            if bn_candles:
                self._last_bn_ts = bn_candles[-1]["timestamp"]

            self._last_backfill = MarketHours.get_ist_now()
            logger.info(
                f"[NSRunner] API backfill: {fed} NF bars, {len(bn_candles)} BN bars fed. "
                f"NF last: {self._last_nf_ts.strftime('%H:%M')}"
            )

        except Exception as exc:
            logger.error(f"[NSRunner] API backfill failed: {exc}", exc_info=True)

    # ── Live buffer poll ────────────────────────────────────────────

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
                # Always filter to today's session to exclude stale bars
                # from previous days (live buffer may not be rolled over yet)
                today_open = datetime.combine(self._session_date or date.today(),
                                              datetime.min.time().replace(hour=9, minute=0))
                if last_ts is None:
                    rows = conn.execute(
                        "SELECT timestamp, open, high, low, close, volume "
                        "FROM candles WHERE symbol=? AND timeframe='1m' "
                        "AND timestamp >= ? ORDER BY timestamp ASC",
                        [symbol, today_open]
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
