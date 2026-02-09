"""
LiveDuckDBMarketDataProvider - Real-Time Data Provider
------------------------------------------------------
Provides OHLCV bars from DuckDB for live trading.
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple, Set
from pathlib import Path

from core.database.providers.base import MarketDataProvider
from core.database.queries import MarketDataQuery
from core.database.manager import DatabaseManager
from core.events import OHLCVBar

logger = logging.getLogger(__name__)

class LiveDuckDBMarketDataProvider(MarketDataProvider):
    """
    Provides real-time OHLCV bars from DuckDB for live trading.
    """

    def __init__(
        self,
        symbols: List[str],
        db_manager: Optional[DatabaseManager] = None,
        poll_interval: float = 0.5,
        lookback_bars: int = 5,
        data_root: Optional[str] = None,
    ):
        """
        Initialize the live market data provider.
        """
        super().__init__(symbols)

        self.poll_interval = poll_interval
        self.lookback_bars = lookback_bars

        # Initialize database connection
        if db_manager:
            self._db = db_manager
        else:
            self._db = DatabaseManager(data_root or Path("data"))

        self._query = MarketDataQuery(self._db)

        # Track last processed timestamp per symbol
        self._last_timestamps: Dict[str, datetime] = {}

        # Buffer for unprocessed bars
        self._bar_buffers: Dict[str, List[OHLCVBar]] = {s: [] for s in symbols}

        # Track if we're still "live" (not stopped)
        self._is_active = True

        # Symbols that have been initialized
        self._initialized: Set[str] = set()

        # Initialize with recent bars
        self._initialize_symbols()

    def _initialize_symbols(self) -> None:
        """Load recent bars to establish starting point."""
        for symbol in self.symbols:
            try:
                # Get most recent bars
                df = self._query.get_ohlcv(
                    instrument_key=symbol,
                    timeframe="1m",
                    limit=self.lookback_bars,
                )

                if not df.empty:
                    # Set last timestamp to the most recent bar
                    last_ts = df.iloc[-1]["timestamp"]
                    self._last_timestamps[symbol] = last_ts
                    self._initialized.add(symbol)
            except Exception as e:
                logger.error(f"Failed to initialize symbol {symbol}: {e}")

    def get_next_bar(self, symbol: str) -> Optional[OHLCVBar]:
        if not self._is_active:
            return None

        # Check buffer first
        if self._bar_buffers.get(symbol):
            return self._bar_buffers[symbol].pop(0)

        # Poll database for new bars
        self._poll_for_new_bars(symbol)

        # Return from buffer if anything was added
        if self._bar_buffers.get(symbol):
            return self._bar_buffers[symbol].pop(0)

        return None

    def _poll_for_new_bars(self, symbol: str) -> None:
        """Poll database for bars newer than last processed."""
        last_ts = self._last_timestamps.get(symbol)

        try:
            if last_ts:
                # Query for bars after last timestamp
                df = self._query.get_ohlcv(
                    instrument_key=symbol,
                    start_time=last_ts + timedelta(seconds=1),
                    timeframe="1m",
                    limit=100,
                )
            else:
                # First poll - get most recent bar
                df = self._query.get_ohlcv(
                    instrument_key=symbol,
                    timeframe="1m",
                    limit=1,
                )

            if df.empty:
                return

            # Add new bars to buffer
            for _, row in df.iterrows():
                bar = OHLCVBar(
                    symbol=symbol,
                    timestamp=row["timestamp"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]) if row["volume"] else 0,
                )
                self._bar_buffers[symbol].append(bar)

            # Update last timestamp
            if self._bar_buffers[symbol]:
                self._last_timestamps[symbol] = self._bar_buffers[symbol][-1].timestamp
        except Exception as e:
            logger.error(f"Failed to poll new bars for {symbol}: {e}")

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        try:
            result = self._query.get_latest_bar(symbol)
            if result:
                return OHLCVBar(
                    symbol=symbol,
                    timestamp=result["timestamp"],
                    open=float(result["open"]),
                    high=float(result["high"]),
                    low=float(result["low"]),
                    close=float(result["close"]),
                    volume=int(result["volume"]) if result["volume"] else 0,
                )
        except Exception as e:
            logger.error(f"Failed to get latest bar for {symbol}: {e}")
        return None

    def is_data_available(self, symbol: str) -> bool:
        return self._is_active and symbol in self.symbols

    def reset(self, symbol: str) -> None:
        self._bar_buffers[symbol] = []
        self._last_timestamps.pop(symbol, None)
        self._initialized.discard(symbol)

    def get_progress(self, symbol: str) -> Tuple[int, int]:
        buffer_size = len(self._bar_buffers.get(symbol, []))
        return (buffer_size, -1)

    def stop(self) -> None:
        self._is_active = False

    def start(self) -> None:
        self._is_active = True

    def get_last_timestamp(self, symbol: str) -> Optional[datetime]:
        return self._last_timestamps.get(symbol)

    def get_buffer_size(self, symbol: str) -> int:
        return len(self._bar_buffers.get(symbol, []))

    def wait_for_data(self, symbol: str, timeout: float = 30.0) -> Optional[OHLCVBar]:
        start_time = time.time()
        while time.time() - start_time < timeout:
            bar = self.get_next_bar(symbol)
            if bar:
                return bar
            time.sleep(self.poll_interval)
        return None
