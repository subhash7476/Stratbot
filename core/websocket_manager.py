# core/websocket_manager.py
import upstox_client
from collections import defaultdict
from datetime import datetime, timedelta
import threading


# st.session_state.resampling_in_progress = False

class WebSocketCandleBuilder:
    def __init__(self, db_manager):
        self.db = db_manager
        self.current_candles = {}   # instrument_key -> candle
        self.last_ltp = {}          # instrument_key -> last traded price
        self.last_minute = None     # last flushed minute
        self.streamer = None
        self.ws_started_at = None

    def start(self, access_token: str, instrument_keys: list):
        """Start WebSocket connection and subscribe to instruments"""
        config = upstox_client.Configuration()
        config.access_token = access_token

        self.streamer = upstox_client.MarketDataStreamerV3(
            upstox_client.ApiClient(config)
        )
        # reconnect after 10s, 5 retries
        self.streamer.auto_reconnect(True, 10, 5)

        def on_open():
            # ✅ Capture WS start time ONCE
            if self.ws_started_at is None:
                self.ws_started_at = datetime.now()

            self.streamer.subscribe(instrument_keys, "full")

        def on_message(message):
            self._process_tick(message)
            self._flush_minute_candles()

        self.streamer.on("open", on_open)
        self.streamer.on("message", on_message)
        self.streamer.connect()

    def _process_tick(self, message):
        feeds = message.get("feeds", {})
        current_ts = datetime.now()
        current_minute = current_ts.replace(second=0, microsecond=0)

        # STOP processing ticks after market close (3:30 PM)
        market_close_today = datetime.combine(current_ts.date(), datetime.strptime("15:30", "%H:%M").time())
        if current_minute > market_close_today:
            return  # Market closed, ignore ticks

        for instrument_key, data in feeds.items():
            ltpc = (
                data.get("fullFeed", {})
                    .get("marketFF", {})
                    .get("ltpc", {})
            )
            if not ltpc:
                continue

            ltp = ltpc.get("ltp")
            if ltp is None:
                continue

            ltq = int(ltpc.get("ltq", 0))

            # Track last price ALWAYS
            self.last_ltp[instrument_key] = ltp

            candle = self.current_candles.get(instrument_key)

            if candle is None:
                self.current_candles[instrument_key] = {
                    "minute": current_minute,
                    "open": ltp,
                    "high": ltp,
                    "low": ltp,
                    "close": ltp,
                    "volume": ltq
                }
            elif current_minute > candle["minute"]:
                self._flush_candle(instrument_key, candle)
                self.current_candles[instrument_key] = {
                    "minute": current_minute,
                    "open": ltp,
                    "high": ltp,
                    "low": ltp,
                    "close": ltp,
                    "volume": ltq
                }
            else:
                candle["high"] = max(candle["high"], ltp)
                candle["low"] = min(candle["low"], ltp)
                candle["close"] = ltp
                candle["volume"] += ltq

    def _flush_minute_candles(self):
        """
        Ensure one candle per instrument per minute,
        even if no tick arrived.

        CRITICAL: Stop creating candles after market close (3:30 PM)
        """
        now = datetime.now()
        minute = now.replace(second=0, microsecond=0)

        # STOP at market close - don't create candles after 3:30 PM
        market_close_today = datetime.combine(now.date(), datetime.strptime("15:30", "%H:%M").time())
        if minute > market_close_today:
            return  # Market closed, stop creating candles

        # Only flush once per minute
        if self.last_minute == minute:
            return

        self.last_minute = minute

        for instrument_key, last_price in self.last_ltp.items():

            candle = self.current_candles.get(instrument_key)

            # If we have an existing candle for previous minute
            if candle and candle["minute"] < minute:
                self._flush_candle(instrument_key, candle)

            # If no candle existed (no ticks this minute), create flat candle
            elif not candle:
                self._flush_candle(
                    instrument_key,
                    {
                        "minute": minute - timedelta(minutes=1),
                        "open": last_price,
                        "high": last_price,
                        "low": last_price,
                        "close": last_price,
                        "volume": 0
                    }
                )

            # Start new candle for current minute
            self.current_candles[instrument_key] = {
                "minute": minute,
                "open": last_price,
                "high": last_price,
                "low": last_price,
                "close": last_price,
                "volume": 0
            }

    def _flush_candle(self, instrument_key: str, candle: dict):
        """Write completed candle to live_ohlcv_cache and update status"""
        # Insert/update candle
        self.db.execute_safe("""
            INSERT INTO live_ohlcv_cache
            (instrument_key, timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (instrument_key, timestamp)
            DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume
        """, [
            instrument_key, candle["minute"],
            candle["open"], candle["high"], candle["low"],
            candle["close"], candle["volume"]
        ])

        # Update live_data_status with latest fetch time (real-time update)
        now = datetime.now()
        self.db.execute_safe("""
            INSERT INTO live_data_status
            (instrument_key, last_fetch, last_candle_time, candle_count, status)
            VALUES (?, ?, ?, 1, 'WS_LIVE')
            ON CONFLICT (instrument_key)
            DO UPDATE SET
                last_fetch = EXCLUDED.last_fetch,
                last_candle_time = EXCLUDED.last_candle_time,
                candle_count = candle_count + 1
        """, [instrument_key, now, candle["minute"]])

    def reset(self):
        self.current_candles.clear()
        self.last_ltp.clear()
        self.last_minute = None
        self.ws_started_at = None
