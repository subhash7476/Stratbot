import logging
import time
from datetime import datetime, timedelta, time as dtime
from typing import List, Optional
import pytz

from core.api.upstox_client import UpstoxClient
from core.database.manager import DatabaseManager
from core.database.utils.market_hours import MarketHours

logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')

# NSE session opens at 09:15 IST
SESSION_OPEN = dtime(9, 15)


class RecoveryManager:
    """
    Handles data gap detection and automated backfilling into the live buffer.

    Gap scenarios handled:
      1. Empty buffer (fresh start / post-clear):
           last_ts = None  →  backfill from today's 09:15 IST
      2. Mid-session outage (power cut, crash):
           last_ts = some time today  →  fill the gap from last_ts to now
      3. Stale buffer from a previous day (edge case, EOD rollover missed):
           last_ts.date() < today  →  treat same as empty; backfill from 09:15
      4. No gap (< 2 min):  skip — nothing to do
    """

    def __init__(self, upstox_client: UpstoxClient, db_manager: DatabaseManager):
        self.client = upstox_client
        self.db = db_manager

    def run_recovery(self, symbols: List[str]):
        """Executes recovery for all symbols."""
        logger.info(f"Starting recovery for {len(symbols)} symbols...")
        for symbol in symbols:
            self._recover_symbol(symbol)

    def _recover_symbol(self, symbol: str):
        now   = MarketHours.get_ist_now()
        today = now.date()

        # Guard: don't attempt recovery outside market hours.
        # After market close the live buffer may have been rolled over (empty).
        if not MarketHours.is_market_open(now):
            logger.debug(f"[Recovery] {symbol}: market closed — skipping.")
            return

        last_ts = self._get_last_bar_timestamp(symbol)

        # Session open datetime for today in IST
        session_open_dt = IST.localize(datetime.combine(today, SESSION_OPEN))

        # ── Case 1 & 3: empty buffer or stale data from a previous day ────────
        if last_ts is None or last_ts.date() < today:
            if now < session_open_dt:
                # Market hasn't opened yet — nothing to recover
                logger.info(f"[Recovery] {symbol}: market not open yet, buffer empty — nothing to do.")
                return

            # Market has opened. Set last_ts to one minute before session open
            # so the write filter (ts > last_ts) includes the 09:15 bar.
            stale_reason = "empty buffer" if last_ts is None else f"stale data from {last_ts.date()}"
            last_ts = session_open_dt - timedelta(minutes=1)   # 09:14 IST
            logger.info(
                f"[Recovery] {symbol}: {stale_reason} — "
                f"backfilling from session open {session_open_dt.strftime('%H:%M')} IST"
            )

        # ── Cases 2 & 4: buffer has today's data — check gap size ─────────────
        gap = now - last_ts
        if gap < timedelta(minutes=2):
            logger.info(f"[Recovery] {symbol}: no significant gap (last bar {last_ts.strftime('%H:%M')} IST).")
            return

        logger.info(
            f"[Recovery] {symbol}: gap of {int(gap.total_seconds() // 60)} min "
            f"(last bar {last_ts.strftime('%H:%M')} IST) — fetching intraday data..."
        )

        try:
            # Always use the intraday endpoint — we are always filling today's gaps.
            # The write filter (ts > last_ts) below ensures only the missing bars
            # are inserted regardless of how many candles the API returns.
            candles = self.client.fetch_intraday_candles_v3(
                instrument_key=symbol,
                unit="minutes",
                interval=1
            )

            if not candles:
                logger.warning(f"[Recovery] {symbol}: API returned 0 candles.")
                return

            recovered_count = 0
            cutoff = now.replace(second=0, microsecond=0)  # exclude the current (incomplete) minute

            # Retry loop for DuckDB lock conflicts
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    with self.db.live_buffer_writer() as conns:
                        candles_conn = conns['candles']
                        for candle in candles:
                            ts = candle['timestamp']
                            # Only insert bars strictly after last_ts and before current minute
                            if ts > last_ts and ts < cutoff:
                                candles_conn.execute(
                                    """
                                    INSERT OR IGNORE INTO candles
                                    (symbol, timeframe, timestamp, open, high, low, close, volume, is_synthetic)
                                    VALUES (?, '1m', ?, ?, ?, ?, ?, ?, TRUE)
                                    """,
                                    [symbol, ts,
                                     candle['open'], candle['high'], candle['low'],
                                     candle['close'], int(candle['volume'])]
                                )
                                recovered_count += 1
                    logger.info(f"[Recovery] {symbol}: recovered {recovered_count} bars.")
                    break  # success
                except Exception as write_error:
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"[Recovery] {symbol}: write failed "
                            f"(attempt {attempt + 1}/{max_retries}): {write_error}"
                        )
                        time.sleep(0.2 * (attempt + 1))
                    else:
                        logger.error(
                            f"[Recovery] {symbol}: write failed after "
                            f"{max_retries} attempts: {write_error}"
                        )

        except Exception as e:
            logger.error(f"[Recovery] {symbol}: fetch failed — {e}")

    def _get_last_bar_timestamp(self, symbol: str) -> Optional[datetime]:
        """Get last bar timestamp from the live buffer with retry logic."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with self.db.live_buffer_reader() as conns:
                    if 'candles' not in conns:
                        return None
                    res = conns['candles'].execute(
                        "SELECT MAX(timestamp) FROM candles WHERE symbol = ?",
                        [symbol]
                    ).fetchone()
                    ts = res[0] if res and res[0] else None
                    if ts and ts.tzinfo is None:
                        ts = IST.localize(ts)
                    return ts
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.debug(
                        f"[Recovery] {symbol}: timestamp read failed "
                        f"(attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    time.sleep(0.1 * (attempt + 1))
                else:
                    logger.warning(
                        f"[Recovery] {symbol}: could not fetch last timestamp "
                        f"after {max_retries} attempts."
                    )
        return None
