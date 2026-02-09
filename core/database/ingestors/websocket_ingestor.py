import asyncio
import json
import logging
import threading
import websockets
from datetime import datetime
import pytz
from typing import List, Optional, Dict

from core.data.MarketDataFeedV3_pb2 import FeedResponse
from core.database.manager import DatabaseManager

logger = logging.getLogger(__name__)

class TickBuffer:
    """
    Buffered tick writer for high-throughput ingestion.
    Uses DatabaseManager.live_buffer_writer() for persistence.
    """

    def __init__(self, db_manager: DatabaseManager, flush_interval: float = 0.5, max_buffer_size: int = 100):
        self.db_manager = db_manager
        self.flush_interval = flush_interval
        self.max_buffer_size = max_buffer_size
        self._buffer = []
        self._last_flush = datetime.now()

    def add_tick(
        self,
        symbol: str,
        exchange_ts: datetime,
        price: float,
        volume: int
    ):
        """Add tick to buffer. Auto-flushes when full or interval reached."""
        self._buffer.append((
            symbol, exchange_ts, price, volume
        ))

        now = datetime.now()
        elapsed = (now - self._last_flush).total_seconds()

        if len(self._buffer) >= self.max_buffer_size or elapsed >= self.flush_interval:
            self.flush()

    def flush(self, max_retries=3):
        """Write all buffered ticks to today's live buffer."""
        if not self._buffer:
            return

        for attempt in range(max_retries):
            try:
                with self.db_manager.live_buffer_writer() as conns:
                    ticks_conn = conns['ticks']
                    ticks_conn.executemany(
                        """
                        INSERT OR IGNORE INTO ticks (symbol, timestamp, price, volume)
                        VALUES (?, ?, ?, ?)
                        """,
                        self._buffer
                    )
                self._buffer.clear()
                self._last_flush = datetime.now()
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Tick buffer flush failed (attempt {attempt+1}/{max_retries}): {e}")
                    import time
                    time.sleep(0.1 * (attempt + 1))
                else:
                    logger.error(f"Tick buffer flush failed after {max_retries} attempts: {e}")
                    # Drop oldest ticks to prevent memory overflow
                    if len(self._buffer) > 1000:
                        self._buffer = self._buffer[-500:]
                        logger.warning("Dropped old ticks to prevent memory overflow")

    def close(self):
        """Flush remaining ticks."""
        self.flush()


class WebSocketIngestor:
    """
    Stateless WebSocket ingestor for Upstox V3.
    Persists ticks to the live buffer via DatabaseManager.
    """

    WSS_URL = "wss://api.upstox.com/v3/feed/market-data-feed"
    
    def __init__(self, symbols: List[str], access_token: str, db_manager: DatabaseManager):
        self.symbols = symbols
        self.access_token = access_token
        self.db_manager = db_manager
        self._is_running = False
        self._loop = None
        self._thread = None
        self._tick_buffer = TickBuffer(db_manager=db_manager, flush_interval=0.5, max_buffer_size=100)
        
    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self):
        """Starts the ingestion in a background thread."""
        if self._is_running:
            return
        self._is_running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()
        logger.info(f"WebSocketIngestor started for symbols: {self.symbols}")
        
    def stop(self):
        """Stops the ingestion."""
        self._is_running = False
        if self._tick_buffer:
            self._tick_buffer.close()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("WebSocketIngestor stopped.")

    def _run_event_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_ingest())
        except Exception as e:
            logger.error(f"Ingestor loop failure: {e}", exc_info=True)
        finally:
            self._loop.close()

    async def _get_authorized_url(self):
        """Fetches the dynamic authorized WebSocket URL from Upstox."""
        import requests
        url = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json"
        }
        try:
            res = requests.get(url, headers=headers, timeout=10)
            res.raise_for_status()
            data = res.json()
            return data['data']['authorized_redirect_uri']
        except Exception as e:
            logger.error(f"Failed to get authorized WS URL: {e}")
            return None

    async def _connect_and_ingest(self):
        backoff = 1.0
        while self._is_running:
            try:
                wss_url = await self._get_authorized_url()
                if not wss_url:
                    logger.error(f"Could not authorize WebSocket. Retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(60.0, backoff * 2)
                    continue

                async with websockets.connect(wss_url) as ws:
                    logger.info("Connected to Upstox Authorized WebSocket Feed.")
                    backoff = 1.0 
                    
                    subscribe_msg = {
                        "guid": "trading_bot_ingestor",
                        "method": "sub",
                        "data": {
                            "mode": "full",
                            "instrumentKeys": self.symbols
                        }
                    }
                    await ws.send(json.dumps(subscribe_msg).encode('utf-8'))
                    
                    async for message in ws:
                        if not self._is_running:
                            break
                        
                        if isinstance(message, bytes):
                            self._handle_message(message)
                        
            except Exception as e:
                if self._is_running:
                    logger.error(f"WebSocket connection error: {e}. Reconnecting in {backoff}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(60.0, backoff * 2)
                else:
                    break

    def _handle_message(self, message: bytes):
        """Decodes and persists ticks using buffered writes."""
        try:
            feed_response = FeedResponse()
            feed_response.ParseFromString(message)

            if not feed_response.feeds:
                return

            for symbol, feed in feed_response.feeds.items():
                ltp_data = self._extract_ltp_from_feed(feed)
                if not ltp_data:
                    continue
                
                ltp, ltt_ms, ltq = ltp_data
                if ltp == 0: continue

                # Convert to IST but make it naive for storage consistency
                ist_tz = pytz.timezone('Asia/Kolkata')
                exchange_ts = datetime.fromtimestamp(ltt_ms / 1000.0, tz=ist_tz).replace(tzinfo=None)

                # Buffer tick for batch write
                self._tick_buffer.add_tick(
                    symbol=symbol,
                    exchange_ts=exchange_ts,
                    price=ltp,
                    volume=int(ltq)
                )
        except Exception as e:
            logger.error(f"Failed to handle WS message: {e}")

    def _extract_ltp_from_feed(self, feed):
        """Navigates the Feed union to extract LTP, LTT, and LTQ."""
        try:
            union_type = feed.WhichOneof('FeedUnion')
            if union_type == 'ltpc':
                return feed.ltpc.ltp, feed.ltpc.ltt, feed.ltpc.ltq
            
            elif union_type == 'fullFeed':
                ff_union_type = feed.fullFeed.WhichOneof('FullFeedUnion')
                if ff_union_type == 'marketFF':
                    ltpc = feed.fullFeed.marketFF.ltpc
                    return ltpc.ltp, ltpc.ltt, ltpc.ltq
                elif ff_union_type == 'indexFF':
                    ltpc = feed.fullFeed.indexFF.ltpc
                    return ltpc.ltp, ltpc.ltt, 0
            
            elif union_type == 'firstLevelWithGreeks':
                ltpc = feed.firstLevelWithGreeks.ltpc
                return ltpc.ltp, ltpc.ltt, ltpc.ltq
                
        except Exception:
            pass
        return None
