import logging
import json
from datetime import datetime
from typing import Optional, List, Dict, Tuple, Set
from pathlib import Path

from core.database.providers.base import MarketDataProvider
from core.database.providers.live_market import LiveDuckDBMarketDataProvider
from core.database.manager import DatabaseManager
from core.messaging.zmq_handler import ZmqSubscriber
from core.events import OHLCVBar

logger = logging.getLogger(__name__)

class ZmqMarketDataProvider(MarketDataProvider):
    """
    Real-time market data provider using ZMQ as a fast-path,
    with DuckDB as a deterministic fallback (Dual-Rail).
    """

    def __init__(
        self,
        symbols: List[str],
        zmq_host: str,
        zmq_port: int,
        db_manager: Optional[DatabaseManager] = None,
        data_root: Optional[str] = None,
        lookback_bars: int = 5,
    ):
        super().__init__(symbols)
        
        # Initialize the fallback provider (DuckDB)
        self.fallback_provider = LiveDuckDBMarketDataProvider(
            symbols=symbols,
            db_manager=db_manager,
            data_root=data_root,
            lookback_bars=lookback_bars
        )
        
        # Initialize ZMQ Subscriber
        topics = [f"market.candle.1m.{s}" for s in symbols]
        self.subscriber = ZmqSubscriber(host=zmq_host, port=zmq_port, topics=topics)
        
        # Buffers for ZMQ data
        self._zmq_buffers: Dict[str, List[OHLCVBar]] = {s: [] for s in symbols}
        self._is_active = True

    def get_next_bar(self, symbol: str) -> Optional[OHLCVBar]:
        if not self._is_active:
            return None

        # 1. Drain ZMQ socket into buffers
        # Wait a few ms for a message if buffer is currently empty
        timeout = 10 if not self._zmq_buffers[symbol] else 0
        self._consume_zmq_messages(timeout_ms=timeout)

        # 2. Check ZMQ buffer first (Fast-path)
        if self._zmq_buffers[symbol]:
            bar = self._zmq_buffers[symbol].pop(0)
            # Sync fallback provider's state so it doesn't return the same bar later
            self.fallback_provider._last_timestamps[symbol] = bar.timestamp
            return bar

        # 3. Fallback to DuckDB (Slow-path / Recovery)
        return self.fallback_provider.get_next_bar(symbol)

    def _consume_zmq_messages(self, timeout_ms: int = 0):
        """Drains pending messages from the ZMQ socket."""
        while True:
            envelope = self.subscriber.recv(timeout_ms=timeout_ms)
            # After first successful recv, don't block anymore
            timeout_ms = 0
            
            if not envelope:
                break
                
            try:
                # Validate message
                if envelope.get("type") != "market_candle":
                    continue
                    
                data = envelope["data"]
                symbol = data["symbol"]
                
                if symbol not in self._zmq_buffers:
                    continue
                
                # Convert ISO string back to datetime
                ts = datetime.fromisoformat(data["timestamp"])
                
                # De-duplicate against fallback provider's last timestamp
                last_ts = self.fallback_provider.get_last_timestamp(symbol)
                if last_ts and ts <= last_ts:
                    continue

                bar = OHLCVBar(
                    symbol=symbol,
                    timestamp=ts,
                    open=data["open"],
                    high=data["high"],
                    low=data["low"],
                    close=data["close"],
                    volume=data["volume"]
                )
                
                # Ensure no duplicates in ZMQ buffer itself
                if not self._zmq_buffers[symbol] or ts > self._zmq_buffers[symbol][-1].timestamp:
                    self._zmq_buffers[symbol].append(bar)
                    
            except Exception as e:
                logger.error(f"Error processing ZMQ message: {e}")

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        # Always use DuckDB for 'latest' to ensure consistency with source of truth
        return self.fallback_provider.get_latest_bar(symbol)

    def is_data_available(self, symbol: str) -> bool:
        return self._is_active and symbol in self.symbols

    def reset(self, symbol: str) -> None:
        self._zmq_buffers[symbol] = []
        self.fallback_provider.reset(symbol)

    def get_progress(self, symbol: str) -> Tuple[int, int]:
        return self.fallback_provider.get_progress(symbol)

    def stop(self) -> None:
        self._is_active = False
        self.subscriber.close()
        self.fallback_provider.stop()

    def start(self) -> None:
        self._is_active = True
        self.fallback_provider.start()
