# core/websocket/manager.py
"""
WebSocket Manager - Orchestrates WebSocket and Data Store
==========================================================

High-level manager that coordinates:
- UpstoxWebSocketClient (connection management)
- MarketDataStore (in-memory data)
- Candle building (1-min OHLCV aggregation)
- DuckDB persistence

Provides a simple interface for the rest of the application.

Usage:
    from core.websocket import WebSocketManager

    manager = WebSocketManager.get_instance()

    # Start streaming
    manager.start(access_token, instrument_keys)

    # Get instant LTP
    ltp = manager.get_ltp("NSE_EQ|INE002A01018")

    # Get all LTPs
    all_ltps = manager.get_all_ltps()

    # Get connection status
    status = manager.get_status()

    # Stop
    manager.stop()

Author: Trading Bot Pro
Version: 1.0
"""

import threading
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

from .upstox_ws_client import UpstoxWebSocketClient, SubscriptionMode, TickData, ConnectionHealth
from .market_data_store import MarketDataStore, MarketQuote, StoreHealth

logger = logging.getLogger(__name__)


# Market hours
MARKET_OPEN = datetime.strptime("09:15", "%H:%M").time()
MARKET_CLOSE = datetime.strptime("15:30", "%H:%M").time()


def is_market_hours(now: Optional[datetime] = None) -> bool:
    """Check if current time is within market hours"""
    if now is None:
        now = datetime.now()
    current_time = now.time()
    return MARKET_OPEN <= current_time <= MARKET_CLOSE


@dataclass
class WebSocketStatus:
    """Comprehensive status of WebSocket system"""
    # Connection
    connected: bool = False
    running: bool = False
    ws_started_at: Optional[datetime] = None

    # Health
    connection_health: Optional[ConnectionHealth] = None
    store_health: Optional[StoreHealth] = None

    # Subscriptions
    subscribed_instruments: int = 0
    instruments_with_data: int = 0

    # Data flow
    total_ticks_received: int = 0
    candles_written: int = 0
    last_tick_time: Optional[datetime] = None

    # Errors
    last_error: Optional[str] = None
    reconnect_count: int = 0


class WebSocketManager:
    """
    High-level manager for WebSocket market data streaming.

    Coordinates UpstoxWebSocketClient, MarketDataStore, and candle building.
    """

    # Singleton instance
    _instance: Optional['WebSocketManager'] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'WebSocketManager':
        """Get singleton instance of WebSocketManager"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
                    logger.info("WebSocketManager singleton created")
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset singleton instance"""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.stop()
                cls._instance = None
                logger.info("WebSocketManager singleton reset")

    def __init__(self):
        """Initialize the manager"""
        self._client: Optional[UpstoxWebSocketClient] = None
        self._store = MarketDataStore.get_instance()
        self._db = None  # Lazy loaded

        # State
        self._running = False
        self._access_token: Optional[str] = None
        self._subscribed_keys: List[str] = []
        self._ws_started_at: Optional[datetime] = None

        # Candle building state
        self._current_candles: Dict[str, Dict] = {}  # instrument_key -> candle
        self._last_flush_minute: Optional[datetime] = None
        self._candles_written = 0

        # Statistics
        self._total_ticks = 0
        self._last_tick_time: Optional[datetime] = None
        self._last_error: Optional[str] = None

        # Thread safety
        self._lock = threading.RLock()

        # Flush thread
        self._flush_thread: Optional[threading.Thread] = None
        self._flush_stop_event = threading.Event()

        logger.debug("WebSocketManager initialized")

    @property
    def db(self):
        """Lazy load database connection"""
        if self._db is None:
            try:
                from core.database import get_db
                self._db = get_db()
            except ImportError:
                logger.warning("Database module not available")
        return self._db

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected"""
        return self._client is not None and self._client.is_connected

    @property
    def is_running(self) -> bool:
        """Check if manager is running"""
        return self._running

    def start(
        self,
        access_token: str,
        instrument_keys: List[str],
        mode: SubscriptionMode = SubscriptionMode.FULL,
        enable_candle_building: bool = True
    ) -> bool:
        """
        Start WebSocket streaming.

        Args:
            access_token: Upstox API access token
            instrument_keys: List of instrument keys to subscribe
            mode: Subscription mode (ltpc, full, option_greeks)
            enable_candle_building: Whether to build 1-min candles

        Returns:
            True if started successfully
        """
        with self._lock:
            if self._running:
                logger.warning("WebSocket already running")
                return True

            # Check market hours
            if not is_market_hours():
                logger.info("Market is closed, WebSocket not started")
                return False

            try:
                self._access_token = access_token
                self._subscribed_keys = instrument_keys.copy()

                # Create client
                self._client = UpstoxWebSocketClient(access_token)

                # Set up callbacks
                self._client.on_tick = self._on_tick
                self._client.on_connect = self._on_connect
                self._client.on_disconnect = self._on_disconnect
                self._client.on_error = self._on_error
                self._client.on_reconnect = self._on_reconnect

                # Start client
                self._client.start(instrument_keys, mode)
                self._running = True
                self._ws_started_at = datetime.now()

                # Start candle flush thread if enabled
                if enable_candle_building:
                    self._start_flush_thread()

                logger.info(f"WebSocket started with {len(instrument_keys)} instruments")
                return True

            except Exception as e:
                self._last_error = str(e)
                logger.error(f"Failed to start WebSocket: {e}")
                return False

    def stop(self):
        """Stop WebSocket streaming"""
        with self._lock:
            if not self._running:
                return

            logger.info("Stopping WebSocket manager...")

            # Stop flush thread
            self._stop_flush_thread()

            # Flush any remaining candles
            self._flush_all_candles()

            # Stop client
            if self._client:
                self._client.stop()
                self._client = None

            self._running = False
            self._ws_started_at = None

            logger.info("WebSocket manager stopped")

    def restart(self, access_token: Optional[str] = None):
        """
        Restart WebSocket connection.

        Args:
            access_token: New access token (optional, uses previous if not provided)
        """
        logger.info("Restarting WebSocket...")

        # Use provided token or previous one
        token = access_token or self._access_token
        keys = self._subscribed_keys.copy()

        if not token or not keys:
            logger.error("Cannot restart: missing access token or instrument keys")
            return

        self.stop()
        time.sleep(1)  # Brief pause
        self.start(token, keys)

    def subscribe(self, instrument_keys: List[str], mode: Optional[SubscriptionMode] = None):
        """
        Subscribe to additional instruments.

        Args:
            instrument_keys: List of instrument keys
            mode: Subscription mode (optional)
        """
        with self._lock:
            if self._client:
                self._client.subscribe(instrument_keys, mode)
                # Add to our list
                for key in instrument_keys:
                    if key not in self._subscribed_keys:
                        self._subscribed_keys.append(key)

    def unsubscribe(self, instrument_keys: List[str]):
        """
        Unsubscribe from instruments.

        Args:
            instrument_keys: List of instrument keys
        """
        with self._lock:
            if self._client:
                self._client.unsubscribe(instrument_keys)
                # Remove from our list
                for key in instrument_keys:
                    if key in self._subscribed_keys:
                        self._subscribed_keys.remove(key)
                    # Also remove from store
                    self._store.remove_instrument(key)

    # =========================================================================
    # Data Access Methods (Delegate to MarketDataStore)
    # =========================================================================

    def get_ltp(self, instrument_key: str) -> Optional[float]:
        """Get instant LTP for an instrument"""
        return self._store.get_ltp(instrument_key)

    def get_ltps(self, instrument_keys: List[str]) -> Dict[str, Optional[float]]:
        """Get LTPs for multiple instruments"""
        return self._store.get_ltps(instrument_keys)

    def get_all_ltps(self) -> Dict[str, float]:
        """Get all available LTPs"""
        return self._store.get_all_ltps()

    def get_quote(self, instrument_key: str) -> Optional[MarketQuote]:
        """Get full quote for an instrument"""
        return self._store.get_quote(instrument_key)

    def get_quotes(self, instrument_keys: List[str]) -> Dict[str, Optional[MarketQuote]]:
        """Get quotes for multiple instruments"""
        return self._store.get_quotes(instrument_keys)

    def get_all_quotes(self) -> Dict[str, MarketQuote]:
        """Get all available quotes"""
        return self._store.get_all_quotes()

    def is_data_stale(self, instrument_key: str) -> bool:
        """Check if data for an instrument is stale"""
        return self._store.is_stale(instrument_key)

    # =========================================================================
    # Status Methods
    # =========================================================================

    def get_status(self) -> WebSocketStatus:
        """Get comprehensive status of WebSocket system"""
        with self._lock:
            conn_health = self._client.get_health() if self._client else None
            store_health = self._store.get_health()

            return WebSocketStatus(
                connected=self.is_connected,
                running=self._running,
                ws_started_at=self._ws_started_at,
                connection_health=conn_health,
                store_health=store_health,
                subscribed_instruments=len(self._subscribed_keys),
                instruments_with_data=len(self._store),
                total_ticks_received=self._total_ticks,
                candles_written=self._candles_written,
                last_tick_time=self._last_tick_time,
                last_error=self._last_error,
                reconnect_count=conn_health.reconnect_count if conn_health else 0
            )

    def get_subscribed_instruments(self) -> List[str]:
        """Get list of subscribed instrument keys"""
        with self._lock:
            return self._subscribed_keys.copy()

    # =========================================================================
    # Callback Handlers
    # =========================================================================

    def _on_tick(self, tick: TickData):
        """Handle incoming tick from WebSocket"""
        try:
            # Update store
            self._store.update_tick(tick)

            # Update statistics
            self._total_ticks += 1
            self._last_tick_time = datetime.now()

            # Build candle
            self._update_candle(tick)

        except Exception as e:
            logger.error(f"Error handling tick: {e}")

    def _on_connect(self):
        """Handle WebSocket connect event"""
        logger.info("WebSocket connected")
        self._last_error = None

    def _on_disconnect(self, reason: str):
        """Handle WebSocket disconnect event"""
        logger.warning(f"WebSocket disconnected: {reason}")

    def _on_error(self, error: Exception):
        """Handle WebSocket error event"""
        self._last_error = str(error)
        logger.error(f"WebSocket error: {error}")

    def _on_reconnect(self, attempt: int):
        """Handle WebSocket reconnect event"""
        logger.info(f"WebSocket reconnecting (attempt {attempt})")

    # =========================================================================
    # Candle Building
    # =========================================================================

    def _update_candle(self, tick: TickData):
        """Update 1-minute candle with new tick"""
        now = datetime.now()
        current_minute = now.replace(second=0, microsecond=0)

        # Check market close
        market_close = datetime.combine(now.date(), MARKET_CLOSE)
        if now > market_close:
            return  # Market closed

        instrument_key = tick.instrument_key
        ltp = tick.ltp
        ltq = tick.ltq

        with self._lock:
            candle = self._current_candles.get(instrument_key)

            if candle is None:
                # New candle
                self._current_candles[instrument_key] = {
                    "minute": current_minute,
                    "open": ltp,
                    "high": ltp,
                    "low": ltp,
                    "close": ltp,
                    "volume": ltq
                }
            elif current_minute > candle["minute"]:
                # New minute - flush old candle
                self._flush_candle(instrument_key, candle)
                self._current_candles[instrument_key] = {
                    "minute": current_minute,
                    "open": ltp,
                    "high": ltp,
                    "low": ltp,
                    "close": ltp,
                    "volume": ltq
                }
            else:
                # Update existing candle
                candle["high"] = max(candle["high"], ltp)
                candle["low"] = min(candle["low"], ltp)
                candle["close"] = ltp
                candle["volume"] += ltq

    def _flush_candle(self, instrument_key: str, candle: Dict):
        """Write completed candle to database"""
        if self.db is None:
            return

        try:
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

            # Update status
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

            self._candles_written += 1

        except Exception as e:
            logger.error(f"Error writing candle for {instrument_key}: {e}")

    def _flush_all_candles(self):
        """Flush all pending candles"""
        with self._lock:
            for instrument_key, candle in list(self._current_candles.items()):
                self._flush_candle(instrument_key, candle)
            self._current_candles.clear()

    def _start_flush_thread(self):
        """Start background thread for periodic candle flushing"""
        self._flush_stop_event.clear()
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()
        logger.debug("Candle flush thread started")

    def _stop_flush_thread(self):
        """Stop the flush thread"""
        if self._flush_thread:
            self._flush_stop_event.set()
            self._flush_thread.join(timeout=5)
            self._flush_thread = None
            logger.debug("Candle flush thread stopped")

    def _flush_loop(self):
        """Background loop for periodic candle flushing"""
        while not self._flush_stop_event.is_set():
            try:
                now = datetime.now()
                current_minute = now.replace(second=0, microsecond=0)

                # Check market close
                market_close = datetime.combine(now.date(), MARKET_CLOSE)
                if now > market_close:
                    logger.info("Market closed, stopping flush loop")
                    break

                # Flush once per minute
                if self._last_flush_minute != current_minute:
                    self._last_flush_minute = current_minute

                    with self._lock:
                        for instrument_key, candle in list(self._current_candles.items()):
                            if candle["minute"] < current_minute:
                                self._flush_candle(instrument_key, candle)
                                # Reset candle for new minute
                                last_price = candle["close"]
                                self._current_candles[instrument_key] = {
                                    "minute": current_minute,
                                    "open": last_price,
                                    "high": last_price,
                                    "low": last_price,
                                    "close": last_price,
                                    "volume": 0
                                }

                # Sleep until next check (every 5 seconds)
                self._flush_stop_event.wait(timeout=5)

            except Exception as e:
                logger.error(f"Error in flush loop: {e}")
                self._flush_stop_event.wait(timeout=5)


# =========================================================================
# Convenience Functions
# =========================================================================

def get_websocket_manager() -> WebSocketManager:
    """Get the singleton WebSocketManager instance"""
    return WebSocketManager.get_instance()


def get_market_data_store() -> MarketDataStore:
    """Get the singleton MarketDataStore instance"""
    return MarketDataStore.get_instance()


def start_websocket(access_token: str, instrument_keys: List[str]) -> bool:
    """
    Convenience function to start WebSocket.

    Args:
        access_token: Upstox API access token
        instrument_keys: List of instrument keys

    Returns:
        True if started successfully
    """
    manager = get_websocket_manager()
    return manager.start(access_token, instrument_keys)


def stop_websocket():
    """Convenience function to stop WebSocket"""
    manager = get_websocket_manager()
    manager.stop()


def get_instant_ltp(instrument_key: str) -> Optional[float]:
    """
    Get instant LTP without database query.

    Args:
        instrument_key: Instrument key

    Returns:
        LTP or None
    """
    store = get_market_data_store()
    return store.get_ltp(instrument_key)


def get_instant_ltps(instrument_keys: List[str]) -> Dict[str, Optional[float]]:
    """
    Get instant LTPs for multiple instruments.

    Args:
        instrument_keys: List of instrument keys

    Returns:
        Dict of instrument_key -> LTP
    """
    store = get_market_data_store()
    return store.get_ltps(instrument_keys)
