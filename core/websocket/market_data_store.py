# core/websocket/market_data_store.py
"""
Thread-Safe In-Memory Market Data Store
=======================================

Provides instant access to latest market data without database queries.
Adapted from OpenAlgo patterns but simplified for single-user usage.

Features:
- Thread-safe read/write with RLock
- Singleton pattern for global access
- Message throttling (50ms minimum interval)
- Stale data detection
- Health monitoring

Usage:
    store = MarketDataStore.get_instance()

    # Update from WebSocket tick
    store.update_tick(tick_data)

    # Get instant LTP (no DB query!)
    ltp = store.get_ltp("NSE_EQ|INE002A01018")

    # Get all data for multiple instruments
    data = store.get_quotes(["NSE_EQ|INE002A01018", "NSE_EQ|INE009A01021"])

Author: Trading Bot Pro
Version: 1.0
"""

import time
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import deque
import logging

logger = logging.getLogger(__name__)


@dataclass
class MarketQuote:
    """Complete market quote data for an instrument"""
    instrument_key: str
    ltp: float
    change: float = 0.0
    change_percent: float = 0.0
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None  # Previous close
    volume: Optional[int] = None
    atp: Optional[float] = None  # Average trade price
    oi: Optional[float] = None
    tbq: Optional[float] = None  # Total buy quantity
    tsq: Optional[float] = None  # Total sell quantity
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None
    bid_qty: Optional[int] = None
    ask_qty: Optional[int] = None
    # Greeks (for options)
    delta: Optional[float] = None
    theta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    iv: Optional[float] = None
    # Metadata
    last_update: float = 0.0  # Unix timestamp
    tick_count: int = 0


@dataclass
class StoreHealth:
    """Health status of the market data store"""
    total_instruments: int = 0
    stale_instruments: int = 0
    total_updates: int = 0
    updates_per_second: float = 0.0
    oldest_data_age: float = 0.0
    newest_data_age: float = 0.0
    memory_usage_kb: float = 0.0


class MarketDataStore:
    """
    Thread-safe in-memory store for real-time market data.

    Provides O(1) access to latest prices without database queries.
    """

    # Singleton instance
    _instance: Optional['MarketDataStore'] = None
    _instance_lock = threading.Lock()

    # Settings
    STALE_THRESHOLD_SECONDS = 60.0
    THROTTLE_INTERVAL_MS = 50  # Minimum interval between updates for same instrument
    MAX_HISTORY_SIZE = 100  # Keep last N ticks per instrument for debugging

    @classmethod
    def get_instance(cls) -> 'MarketDataStore':
        """Get singleton instance of MarketDataStore"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
                    logger.info("MarketDataStore singleton created")
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset singleton instance (for testing)"""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.clear()
                cls._instance = None
                logger.info("MarketDataStore singleton reset")

    def __init__(self):
        """Initialize the store"""
        # Main data store: instrument_key -> MarketQuote
        self._quotes: Dict[str, MarketQuote] = {}

        # Throttling: instrument_key -> last_update_time
        self._last_update_times: Dict[str, float] = {}

        # Recent ticks history for debugging
        self._tick_history: Dict[str, deque] = {}

        # Statistics
        self._total_updates = 0
        self._updates_in_last_second = 0
        self._last_stats_reset = time.time()

        # Thread safety
        self._lock = threading.RLock()

        # Callbacks for data updates
        self._on_update_callbacks: List[callable] = []

        logger.debug("MarketDataStore initialized")

    def update_tick(self, tick) -> bool:
        """
        Update store with new tick data.

        Args:
            tick: TickData object from WebSocket

        Returns:
            True if update was applied, False if throttled
        """
        now = time.time()
        instrument_key = tick.instrument_key

        # Check throttling
        with self._lock:
            last_update = self._last_update_times.get(instrument_key, 0)
            if (now - last_update) * 1000 < self.THROTTLE_INTERVAL_MS:
                return False  # Throttled

            self._last_update_times[instrument_key] = now

            # Get or create quote
            quote = self._quotes.get(instrument_key)

            if quote is None:
                quote = MarketQuote(
                    instrument_key=instrument_key,
                    ltp=tick.ltp,
                    change=tick.change,
                    last_update=now,
                    tick_count=1
                )
                self._quotes[instrument_key] = quote
            else:
                # Calculate change percent if we have previous close
                if quote.close and quote.close > 0:
                    quote.change_percent = ((tick.ltp - quote.close) / quote.close) * 100

                quote.ltp = tick.ltp
                quote.change = tick.change
                quote.last_update = now
                quote.tick_count += 1

            # Update additional fields if available
            if tick.open is not None:
                quote.open = tick.open
            if tick.high is not None:
                quote.high = tick.high
            if tick.low is not None:
                quote.low = tick.low
            if tick.close is not None:
                quote.close = tick.close
            if tick.volume is not None:
                quote.volume = tick.volume
            if tick.atp is not None:
                quote.atp = tick.atp
            if tick.oi is not None:
                quote.oi = tick.oi
            if tick.tbq is not None:
                quote.tbq = tick.tbq
            if tick.tsq is not None:
                quote.tsq = tick.tsq
            if tick.bid_price is not None:
                quote.bid_price = tick.bid_price
            if tick.ask_price is not None:
                quote.ask_price = tick.ask_price
            if tick.bid_qty is not None:
                quote.bid_qty = tick.bid_qty
            if tick.ask_qty is not None:
                quote.ask_qty = tick.ask_qty

            # Option Greeks
            if tick.delta is not None:
                quote.delta = tick.delta
            if tick.theta is not None:
                quote.theta = tick.theta
            if tick.gamma is not None:
                quote.gamma = tick.gamma
            if tick.vega is not None:
                quote.vega = tick.vega
            if tick.iv is not None:
                quote.iv = tick.iv

            # Update statistics
            self._total_updates += 1
            self._updates_in_last_second += 1

            # Reset per-second counter
            if now - self._last_stats_reset >= 1.0:
                self._updates_in_last_second = 0
                self._last_stats_reset = now

            # Store tick in history
            if instrument_key not in self._tick_history:
                self._tick_history[instrument_key] = deque(maxlen=self.MAX_HISTORY_SIZE)
            self._tick_history[instrument_key].append({
                'ltp': tick.ltp,
                'time': now,
                'ltq': tick.ltq
            })

        # Notify callbacks
        for callback in self._on_update_callbacks:
            try:
                callback(instrument_key, quote)
            except Exception as e:
                logger.error(f"Error in update callback: {e}")

        return True

    def get_ltp(self, instrument_key: str) -> Optional[float]:
        """
        Get last traded price for an instrument.

        Args:
            instrument_key: Instrument key (e.g., "NSE_EQ|INE002A01018")

        Returns:
            LTP or None if not available
        """
        with self._lock:
            quote = self._quotes.get(instrument_key)
            return quote.ltp if quote else None

    def get_ltps(self, instrument_keys: List[str]) -> Dict[str, Optional[float]]:
        """
        Get LTPs for multiple instruments.

        Args:
            instrument_keys: List of instrument keys

        Returns:
            Dict mapping instrument_key -> LTP (or None)
        """
        with self._lock:
            return {
                key: self._quotes[key].ltp if key in self._quotes else None
                for key in instrument_keys
            }

    def get_all_ltps(self) -> Dict[str, float]:
        """
        Get all available LTPs.

        Returns:
            Dict mapping instrument_key -> LTP
        """
        with self._lock:
            return {
                key: quote.ltp
                for key, quote in self._quotes.items()
            }

    def get_quote(self, instrument_key: str) -> Optional[MarketQuote]:
        """
        Get full quote data for an instrument.

        Args:
            instrument_key: Instrument key

        Returns:
            MarketQuote or None
        """
        with self._lock:
            quote = self._quotes.get(instrument_key)
            if quote:
                # Return a copy to prevent external modification
                return MarketQuote(
                    instrument_key=quote.instrument_key,
                    ltp=quote.ltp,
                    change=quote.change,
                    change_percent=quote.change_percent,
                    open=quote.open,
                    high=quote.high,
                    low=quote.low,
                    close=quote.close,
                    volume=quote.volume,
                    atp=quote.atp,
                    oi=quote.oi,
                    tbq=quote.tbq,
                    tsq=quote.tsq,
                    bid_price=quote.bid_price,
                    ask_price=quote.ask_price,
                    bid_qty=quote.bid_qty,
                    ask_qty=quote.ask_qty,
                    delta=quote.delta,
                    theta=quote.theta,
                    gamma=quote.gamma,
                    vega=quote.vega,
                    iv=quote.iv,
                    last_update=quote.last_update,
                    tick_count=quote.tick_count
                )
            return None

    def get_quotes(self, instrument_keys: List[str]) -> Dict[str, Optional[MarketQuote]]:
        """
        Get quotes for multiple instruments.

        Args:
            instrument_keys: List of instrument keys

        Returns:
            Dict mapping instrument_key -> MarketQuote (or None)
        """
        return {key: self.get_quote(key) for key in instrument_keys}

    def get_all_quotes(self) -> Dict[str, MarketQuote]:
        """
        Get all available quotes.

        Returns:
            Dict mapping instrument_key -> MarketQuote
        """
        with self._lock:
            return {key: self.get_quote(key) for key in self._quotes.keys()}

    def is_stale(self, instrument_key: str) -> bool:
        """
        Check if data for an instrument is stale.

        Args:
            instrument_key: Instrument key

        Returns:
            True if data is older than STALE_THRESHOLD_SECONDS
        """
        with self._lock:
            quote = self._quotes.get(instrument_key)
            if not quote:
                return True
            return (time.time() - quote.last_update) > self.STALE_THRESHOLD_SECONDS

    def get_stale_instruments(self) -> List[str]:
        """
        Get list of instruments with stale data.

        Returns:
            List of instrument keys with stale data
        """
        now = time.time()
        with self._lock:
            return [
                key for key, quote in self._quotes.items()
                if (now - quote.last_update) > self.STALE_THRESHOLD_SECONDS
            ]

    def get_health(self) -> StoreHealth:
        """
        Get health status of the store.

        Returns:
            StoreHealth object
        """
        now = time.time()
        with self._lock:
            if not self._quotes:
                return StoreHealth()

            ages = [now - q.last_update for q in self._quotes.values()]
            stale_count = sum(1 for age in ages if age > self.STALE_THRESHOLD_SECONDS)

            # Estimate memory usage (rough)
            memory_kb = len(self._quotes) * 0.5  # ~500 bytes per quote

            return StoreHealth(
                total_instruments=len(self._quotes),
                stale_instruments=stale_count,
                total_updates=self._total_updates,
                updates_per_second=self._updates_in_last_second,
                oldest_data_age=max(ages) if ages else 0,
                newest_data_age=min(ages) if ages else 0,
                memory_usage_kb=memory_kb
            )

    def get_tick_history(self, instrument_key: str) -> List[Dict]:
        """
        Get recent tick history for an instrument.

        Args:
            instrument_key: Instrument key

        Returns:
            List of recent ticks (oldest first)
        """
        with self._lock:
            history = self._tick_history.get(instrument_key)
            if history:
                return list(history)
            return []

    def add_update_callback(self, callback: callable):
        """
        Add callback to be notified on data updates.

        Args:
            callback: Function(instrument_key, quote) to call on updates
        """
        self._on_update_callbacks.append(callback)

    def remove_update_callback(self, callback: callable):
        """
        Remove update callback.

        Args:
            callback: Previously registered callback
        """
        if callback in self._on_update_callbacks:
            self._on_update_callbacks.remove(callback)

    def clear(self):
        """Clear all stored data"""
        with self._lock:
            self._quotes.clear()
            self._last_update_times.clear()
            self._tick_history.clear()
            self._total_updates = 0
            self._updates_in_last_second = 0
            logger.info("MarketDataStore cleared")

    def remove_instrument(self, instrument_key: str):
        """
        Remove data for a specific instrument.

        Args:
            instrument_key: Instrument key to remove
        """
        with self._lock:
            self._quotes.pop(instrument_key, None)
            self._last_update_times.pop(instrument_key, None)
            self._tick_history.pop(instrument_key, None)

    def __len__(self) -> int:
        """Get number of instruments in store"""
        with self._lock:
            return len(self._quotes)

    def __contains__(self, instrument_key: str) -> bool:
        """Check if instrument is in store"""
        with self._lock:
            return instrument_key in self._quotes
