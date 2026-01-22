# core/websocket/upstox_ws_client.py
"""
Upstox WebSocket Client with Enhanced Reconnection
==================================================

Async WebSocket client for Upstox market data streaming.
Adapted from OpenAlgo patterns but simplified for single-user usage.

Features:
- Protobuf message decoding (FeedResponse)
- Exponential backoff reconnection (2s -> 4s -> 8s -> 16s -> 30s)
- Multiple subscription modes (ltpc, full, option_greeks)
- Automatic resubscription on reconnect
- Health monitoring and stale data detection

Author: Trading Bot Pro
Version: 1.0
"""

import asyncio
import ssl
import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
import threading

try:
    import websockets
    from websockets.exceptions import ConnectionClosed, ConnectionClosedError
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

# Import protobuf - try both locations
try:
    from MarketDataFeedV3_pb2 import FeedResponse
    PROTOBUF_AVAILABLE = True
except ImportError:
    try:
        import sys
        sys.path.insert(0, str(__file__).rsplit('\\', 3)[0])  # Add project root
        from MarketDataFeedV3_pb2 import FeedResponse
        PROTOBUF_AVAILABLE = True
    except ImportError:
        PROTOBUF_AVAILABLE = False

import requests

logger = logging.getLogger(__name__)


class SubscriptionMode(Enum):
    """Upstox WebSocket subscription modes"""
    LTPC = "ltpc"              # LTP only (minimal bandwidth)
    FULL = "full"              # Full market data (OHLC, volume, bid/ask qty)
    OPTION_GREEKS = "option_greeks"  # Options with Greeks


@dataclass
class ConnectionHealth:
    """WebSocket connection health status"""
    connected: bool = False
    authenticated: bool = False
    last_message_time: float = 0
    last_data_age_seconds: float = 0
    reconnect_count: int = 0
    total_messages_received: int = 0
    subscription_count: int = 0
    is_stale: bool = False
    error_message: Optional[str] = None


@dataclass
class TickData:
    """Parsed tick data from WebSocket"""
    instrument_key: str
    ltp: float
    ltt: int  # Last trade time (epoch ms)
    ltq: int  # Last trade quantity
    change: float = 0.0
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[int] = None
    atp: Optional[float] = None  # Average trade price
    oi: Optional[float] = None   # Open interest
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
    timestamp: datetime = field(default_factory=datetime.now)


class UpstoxWebSocketClient:
    """
    Async WebSocket client for Upstox market data.

    Usage:
        client = UpstoxWebSocketClient(access_token)
        client.on_tick = my_tick_handler
        client.on_connect = my_connect_handler

        # Start in background thread
        client.start(instrument_keys, mode=SubscriptionMode.FULL)

        # Later...
        client.subscribe(["NSE_EQ|INE009A01021"])
        client.unsubscribe(["NSE_EQ|INE002A01018"])

        # Stop
        client.stop()
    """

    # Upstox WebSocket authorization endpoint
    AUTH_URL = "https://api.upstox.com/v3/feed/market-data-feed/authorize"

    # Reconnection settings (exponential backoff)
    INITIAL_RECONNECT_DELAY = 2.0
    MAX_RECONNECT_DELAY = 30.0
    MAX_RECONNECT_ATTEMPTS = 10
    BACKOFF_MULTIPLIER = 2.0

    # Health check settings
    STALE_DATA_THRESHOLD = 60.0  # seconds
    HEARTBEAT_INTERVAL = 30.0

    def __init__(self, access_token: str):
        """
        Initialize WebSocket client.

        Args:
            access_token: Upstox API access token
        """
        if not WEBSOCKETS_AVAILABLE:
            raise ImportError("websockets library not installed. Run: pip install websockets")

        self.access_token = access_token
        self._ws: Optional[Any] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        # State
        self._running = False
        self._connected = False
        self._subscriptions: Dict[str, SubscriptionMode] = {}  # instrument_key -> mode
        self._reconnect_count = 0
        self._last_message_time = 0.0
        self._total_messages = 0

        # Locks
        self._subscription_lock = threading.Lock()
        self._state_lock = threading.Lock()

        # Callbacks
        self.on_tick: Optional[Callable[[TickData], None]] = None
        self.on_connect: Optional[Callable[[], None]] = None
        self.on_disconnect: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None
        self.on_reconnect: Optional[Callable[[int], None]] = None

        logger.info("UpstoxWebSocketClient initialized")

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected"""
        with self._state_lock:
            return self._connected

    @property
    def is_running(self) -> bool:
        """Check if client is running"""
        with self._state_lock:
            return self._running

    def get_health(self) -> ConnectionHealth:
        """Get current connection health status"""
        with self._state_lock:
            age = time.time() - self._last_message_time if self._last_message_time > 0 else 0
            return ConnectionHealth(
                connected=self._connected,
                authenticated=self._connected,  # Same for Upstox
                last_message_time=self._last_message_time,
                last_data_age_seconds=age,
                reconnect_count=self._reconnect_count,
                total_messages_received=self._total_messages,
                subscription_count=len(self._subscriptions),
                is_stale=age > self.STALE_DATA_THRESHOLD if self._connected else False
            )

    def start(
        self,
        instrument_keys: List[str],
        mode: SubscriptionMode = SubscriptionMode.FULL
    ):
        """
        Start WebSocket connection in background thread.

        Args:
            instrument_keys: List of instrument keys to subscribe
            mode: Subscription mode (ltpc, full, option_greeks)
        """
        if self._running:
            logger.warning("WebSocket already running")
            return

        # Store initial subscriptions
        with self._subscription_lock:
            for key in instrument_keys:
                self._subscriptions[key] = mode

        # Start event loop in background thread
        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()

        logger.info(f"WebSocket client started with {len(instrument_keys)} instruments in {mode.value} mode")

    def stop(self):
        """Stop WebSocket connection"""
        logger.info("Stopping WebSocket client...")

        with self._state_lock:
            self._running = False

        # Close WebSocket if connected
        if self._ws and self._loop:
            try:
                asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop).result(timeout=5)
            except Exception as e:
                logger.warning(f"Error closing WebSocket: {e}")

        # Stop event loop
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

        # Wait for thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

        self._ws = None
        self._loop = None
        self._thread = None
        self._connected = False

        logger.info("WebSocket client stopped")

    def subscribe(self, instrument_keys: List[str], mode: Optional[SubscriptionMode] = None):
        """
        Subscribe to additional instruments.

        Args:
            instrument_keys: List of instrument keys to subscribe
            mode: Subscription mode (defaults to FULL)
        """
        if not self._running:
            logger.warning("WebSocket not running, cannot subscribe")
            return

        mode = mode or SubscriptionMode.FULL

        with self._subscription_lock:
            new_keys = [k for k in instrument_keys if k not in self._subscriptions]
            for key in new_keys:
                self._subscriptions[key] = mode

        if new_keys and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._send_subscribe(new_keys, mode),
                self._loop
            )
            logger.info(f"Subscribed to {len(new_keys)} new instruments")

    def unsubscribe(self, instrument_keys: List[str]):
        """
        Unsubscribe from instruments.

        Args:
            instrument_keys: List of instrument keys to unsubscribe
        """
        if not self._running:
            return

        with self._subscription_lock:
            keys_to_remove = [k for k in instrument_keys if k in self._subscriptions]
            for key in keys_to_remove:
                del self._subscriptions[key]

        if keys_to_remove and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._send_unsubscribe(keys_to_remove),
                self._loop
            )
            logger.info(f"Unsubscribed from {len(keys_to_remove)} instruments")

    def change_mode(self, instrument_keys: List[str], mode: SubscriptionMode):
        """
        Change subscription mode for instruments.

        Args:
            instrument_keys: List of instrument keys
            mode: New subscription mode
        """
        if not self._running:
            return

        with self._subscription_lock:
            for key in instrument_keys:
                if key in self._subscriptions:
                    self._subscriptions[key] = mode

        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._send_change_mode(instrument_keys, mode),
                self._loop
            )
            logger.info(f"Changed mode to {mode.value} for {len(instrument_keys)} instruments")

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _run_event_loop(self):
        """Run asyncio event loop in background thread"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._connect_with_retry())
        except Exception as e:
            logger.error(f"Event loop error: {e}")
        finally:
            self._loop.close()
            self._loop = None

    async def _connect_with_retry(self):
        """Connect to WebSocket with exponential backoff retry"""
        delay = self.INITIAL_RECONNECT_DELAY

        while self._running:
            try:
                await self._connect()

                # Reset reconnect count on successful connection
                self._reconnect_count = 0
                delay = self.INITIAL_RECONNECT_DELAY

                # Listen for messages
                await self._listen()

            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
            except ConnectionClosedError as e:
                logger.warning(f"WebSocket connection closed with error: {e}")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                if self.on_error:
                    self.on_error(e)

            # Mark as disconnected
            with self._state_lock:
                self._connected = False

            if self.on_disconnect:
                self.on_disconnect("Connection lost")

            # Check if we should retry
            if not self._running:
                break

            self._reconnect_count += 1

            if self._reconnect_count > self.MAX_RECONNECT_ATTEMPTS:
                logger.error(f"Max reconnection attempts ({self.MAX_RECONNECT_ATTEMPTS}) exceeded")
                break

            # Exponential backoff
            logger.info(f"Reconnecting in {delay:.1f}s (attempt {self._reconnect_count}/{self.MAX_RECONNECT_ATTEMPTS})")

            if self.on_reconnect:
                self.on_reconnect(self._reconnect_count)

            await asyncio.sleep(delay)
            delay = min(delay * self.BACKOFF_MULTIPLIER, self.MAX_RECONNECT_DELAY)

    async def _connect(self):
        """Establish WebSocket connection"""
        # Get authorized WebSocket URL
        ws_url = await self._get_websocket_url()

        if not ws_url:
            raise ConnectionError("Failed to get WebSocket URL from Upstox")

        # Create SSL context (disable hostname verification for Upstox)
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        # Connect
        logger.info(f"Connecting to Upstox WebSocket...")
        self._ws = await websockets.connect(
            ws_url,
            ssl=ssl_context,
            ping_interval=self.HEARTBEAT_INTERVAL,
            ping_timeout=10,
            close_timeout=5
        )

        with self._state_lock:
            self._connected = True

        logger.info("WebSocket connected successfully")

        # Subscribe to instruments
        with self._subscription_lock:
            if self._subscriptions:
                # Group by mode
                mode_groups: Dict[SubscriptionMode, List[str]] = {}
                for key, mode in self._subscriptions.items():
                    if mode not in mode_groups:
                        mode_groups[mode] = []
                    mode_groups[mode].append(key)

                for mode, keys in mode_groups.items():
                    await self._send_subscribe(keys, mode)

        if self.on_connect:
            self.on_connect()

    async def _get_websocket_url(self) -> Optional[str]:
        """Get authorized WebSocket URL from Upstox API"""
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}"
        }

        try:
            response = requests.get(self.AUTH_URL, headers=headers, timeout=10)
            response.raise_for_status()

            data = response.json()
            ws_url = data.get("data", {}).get("authorizedRedirectUri")

            if ws_url:
                logger.debug(f"Got WebSocket URL: {ws_url[:50]}...")
                return ws_url
            else:
                logger.error(f"No WebSocket URL in response: {data}")
                return None

        except Exception as e:
            logger.error(f"Failed to get WebSocket URL: {e}")
            return None

    async def _listen(self):
        """Listen for incoming WebSocket messages"""
        async for message in self._ws:
            if not self._running:
                break

            try:
                self._process_message(message)
            except Exception as e:
                logger.error(f"Error processing message: {e}")

    def _process_message(self, message: bytes):
        """Process incoming WebSocket message"""
        with self._state_lock:
            self._last_message_time = time.time()
            self._total_messages += 1

        # Try to decode as protobuf first
        if isinstance(message, bytes) and PROTOBUF_AVAILABLE:
            try:
                feed_response = FeedResponse()
                feed_response.ParseFromString(message)
                self._process_protobuf_message(feed_response)
                return
            except Exception as e:
                logger.debug(f"Not a protobuf message: {e}")

        # Try JSON
        try:
            if isinstance(message, bytes):
                message = message.decode('utf-8')

            data = json.loads(message)
            self._process_json_message(data)
        except json.JSONDecodeError:
            logger.warning(f"Unknown message format: {type(message)}")

    def _process_protobuf_message(self, feed_response):
        """Process protobuf FeedResponse message"""
        feeds = feed_response.feeds

        for instrument_key, feed in feeds.items():
            try:
                tick = self._extract_tick_from_feed(instrument_key, feed)
                if tick and self.on_tick:
                    self.on_tick(tick)
            except Exception as e:
                logger.error(f"Error extracting tick for {instrument_key}: {e}")

    def _extract_tick_from_feed(self, instrument_key: str, feed) -> Optional[TickData]:
        """Extract TickData from protobuf Feed message"""
        # Check which feed type we have
        feed_type = feed.WhichOneof('FeedUnion')

        if feed_type == 'ltpc':
            # LTPC mode - minimal data
            ltpc = feed.ltpc
            return TickData(
                instrument_key=instrument_key,
                ltp=ltpc.ltp,
                ltt=ltpc.ltt,
                ltq=ltpc.ltq,
                change=ltpc.cp
            )

        elif feed_type == 'fullFeed':
            # Full feed - comprehensive data
            full_feed = feed.fullFeed
            full_type = full_feed.WhichOneof('FullFeedUnion')

            if full_type == 'marketFF':
                mff = full_feed.marketFF
                ltpc = mff.ltpc

                # Extract OHLC if available
                ohlc_data = {}
                if mff.marketOHLC and mff.marketOHLC.ohlc:
                    for ohlc in mff.marketOHLC.ohlc:
                        if ohlc.interval == '1d':
                            ohlc_data = {
                                'open': ohlc.open,
                                'high': ohlc.high,
                                'low': ohlc.low,
                                'close': ohlc.close,
                                'volume': ohlc.vol
                            }
                            break

                # Extract bid/ask from market level
                bid_price, ask_price, bid_qty, ask_qty = None, None, None, None
                if mff.marketLevel and mff.marketLevel.bidAskQuote:
                    quotes = mff.marketLevel.bidAskQuote
                    if len(quotes) >= 1:
                        bid_price = quotes[0].bidP
                        bid_qty = quotes[0].bidQ
                        ask_price = quotes[0].askP
                        ask_qty = quotes[0].askQ

                # Extract option greeks if available
                greeks = {}
                if mff.optionGreeks:
                    og = mff.optionGreeks
                    greeks = {
                        'delta': og.delta,
                        'theta': og.theta,
                        'gamma': og.gamma,
                        'vega': og.vega
                    }

                return TickData(
                    instrument_key=instrument_key,
                    ltp=ltpc.ltp,
                    ltt=ltpc.ltt,
                    ltq=ltpc.ltq,
                    change=ltpc.cp,
                    open=ohlc_data.get('open'),
                    high=ohlc_data.get('high'),
                    low=ohlc_data.get('low'),
                    close=ohlc_data.get('close'),
                    volume=ohlc_data.get('volume'),
                    atp=mff.atp if mff.atp else None,
                    oi=mff.oi if mff.oi else None,
                    tbq=mff.tbq if mff.tbq else None,
                    tsq=mff.tsq if mff.tsq else None,
                    bid_price=bid_price,
                    ask_price=ask_price,
                    bid_qty=bid_qty,
                    ask_qty=ask_qty,
                    iv=mff.iv if mff.iv else None,
                    **greeks
                )

            elif full_type == 'indexFF':
                # Index feed
                iff = full_feed.indexFF
                ltpc = iff.ltpc

                return TickData(
                    instrument_key=instrument_key,
                    ltp=ltpc.ltp,
                    ltt=ltpc.ltt,
                    ltq=ltpc.ltq,
                    change=ltpc.cp
                )

        elif feed_type == 'firstLevelWithGreeks':
            # Option greeks mode
            flwg = feed.firstLevelWithGreeks
            ltpc = flwg.ltpc

            greeks = {}
            if flwg.optionGreeks:
                og = flwg.optionGreeks
                greeks = {
                    'delta': og.delta,
                    'theta': og.theta,
                    'gamma': og.gamma,
                    'vega': og.vega
                }

            bid_price, ask_price, bid_qty, ask_qty = None, None, None, None
            if flwg.firstDepth:
                fd = flwg.firstDepth
                bid_price = fd.bidP
                bid_qty = fd.bidQ
                ask_price = fd.askP
                ask_qty = fd.askQ

            return TickData(
                instrument_key=instrument_key,
                ltp=ltpc.ltp,
                ltt=ltpc.ltt,
                ltq=ltpc.ltq,
                change=ltpc.cp,
                oi=flwg.oi if flwg.oi else None,
                iv=flwg.iv if flwg.iv else None,
                bid_price=bid_price,
                ask_price=ask_price,
                bid_qty=bid_qty,
                ask_qty=ask_qty,
                **greeks
            )

        return None

    def _process_json_message(self, data: dict):
        """Process JSON message (fallback for non-protobuf)"""
        # Handle feeds from JSON response
        feeds = data.get("feeds", {})

        for instrument_key, feed_data in feeds.items():
            try:
                # Extract LTPC from fullFeed.marketFF.ltpc
                ltpc = (
                    feed_data.get("fullFeed", {})
                    .get("marketFF", {})
                    .get("ltpc", {})
                )

                if not ltpc:
                    # Try direct ltpc
                    ltpc = feed_data.get("ltpc", {})

                if ltpc and ltpc.get("ltp") is not None:
                    tick = TickData(
                        instrument_key=instrument_key,
                        ltp=ltpc.get("ltp", 0),
                        ltt=ltpc.get("ltt", 0),
                        ltq=ltpc.get("ltq", 0),
                        change=ltpc.get("cp", 0)
                    )

                    if self.on_tick:
                        self.on_tick(tick)

            except Exception as e:
                logger.error(f"Error processing JSON feed for {instrument_key}: {e}")

    async def _send_subscribe(self, instrument_keys: List[str], mode: SubscriptionMode):
        """Send subscription message"""
        if not self._ws:
            return

        message = {
            "guid": f"sub_{int(time.time() * 1000)}",
            "method": "sub",
            "data": {
                "mode": mode.value,
                "instrumentKeys": instrument_keys
            }
        }

        await self._ws.send(json.dumps(message))
        logger.debug(f"Sent subscription for {len(instrument_keys)} instruments in {mode.value} mode")

    async def _send_unsubscribe(self, instrument_keys: List[str]):
        """Send unsubscription message"""
        if not self._ws:
            return

        message = {
            "guid": f"unsub_{int(time.time() * 1000)}",
            "method": "unsub",
            "data": {
                "instrumentKeys": instrument_keys
            }
        }

        await self._ws.send(json.dumps(message))
        logger.debug(f"Sent unsubscription for {len(instrument_keys)} instruments")

    async def _send_change_mode(self, instrument_keys: List[str], mode: SubscriptionMode):
        """Send mode change message"""
        if not self._ws:
            return

        message = {
            "guid": f"mode_{int(time.time() * 1000)}",
            "method": "change_mode",
            "data": {
                "mode": mode.value,
                "instrumentKeys": instrument_keys
            }
        }

        await self._ws.send(json.dumps(message))
        logger.debug(f"Sent mode change to {mode.value} for {len(instrument_keys)} instruments")

    async def _close_ws(self):
        """Close WebSocket connection"""
        if self._ws:
            await self._ws.close()
