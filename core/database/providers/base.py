"""
Abstract Provider Interfaces
----------------------------
Base classes for market data and analytics providers.

These interfaces define the contract that the TradingRunner expects.
All providers must implement these methods.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List, Tuple, Any, Dict

from core.events import OHLCVBar


class MarketDataProvider(ABC):
    """
    Abstract interface for market data providers.

    The TradingRunner calls these methods to get OHLCV bars.
    Implementations can be:
    - DuckDBMarketDataProvider: Historical data from database (backtest)
    - LiveDuckDBMarketDataProvider: Real-time data from database (live)
    - MockDataProvider: Test data (smoke tests)

    Usage:
        provider = DuckDBMarketDataProvider(["SYMBOL1", "SYMBOL2"])
        while provider.is_data_available("SYMBOL1"):
            bar = provider.get_next_bar("SYMBOL1")
            # Process bar...
    """

    def __init__(self, symbols: List[str]):
        """
        Initialize the provider with symbols to track.

        Args:
            symbols: List of instrument keys to provide data for.
        """
        self.symbols = symbols

    @abstractmethod
    def get_next_bar(self, symbol: str) -> Optional[OHLCVBar]:
        """
        Get the next OHLCV bar for a symbol.

        For backtest providers, this advances through historical data.
        For live providers, this returns the latest available bar.

        Args:
            symbol: The instrument key.

        Returns:
            OHLCVBar if data is available, None otherwise.
        """
        pass

    @abstractmethod
    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        """
        Get the most recent bar without advancing the iterator.

        Args:
            symbol: The instrument key.

        Returns:
            The most recent OHLCVBar or None.
        """
        pass

    @abstractmethod
    def is_data_available(self, symbol: str) -> bool:
        """
        Check if more data is available for a symbol.

        For backtest: True if more historical bars exist.
        For live: True if the data feed is active.

        Args:
            symbol: The instrument key.

        Returns:
            True if more data can be retrieved.
        """
        pass

    @abstractmethod
    def reset(self, symbol: str) -> None:
        """
        Reset the provider state for a symbol.

        For backtest: Resets to the beginning of the data.
        For live: May reconnect or clear buffers.

        Args:
            symbol: The instrument key.
        """
        pass

    @abstractmethod
    def get_progress(self, symbol: str) -> Tuple[int, int]:
        """
        Get progress information for a symbol.

        Args:
            symbol: The instrument key.

        Returns:
            Tuple of (current_position, total_bars).
        """
        pass


class AnalyticsProvider(ABC):
    """
    Abstract interface for analytics providers.

    The TradingRunner calls these methods to get pre-computed analytics.
    Analytics are computed offline by the AnalyticsPopulator and stored
    in the database.

    Implementations can be:
    - DuckDBAnalyticsProvider: Direct database queries
    - CachedAnalyticsProvider: In-memory caching wrapper
    - MockAnalyticsProvider: Test data (smoke tests)

    Usage:
        provider = CachedAnalyticsProvider(DuckDBAnalyticsProvider())
        snapshot = provider.get_latest_snapshot("SYMBOL", as_of=bar.timestamp)
        regime = provider.get_market_regime("SYMBOL", as_of=bar.timestamp)
    """

    @abstractmethod
    def get_latest_snapshot(
        self, symbol: str, as_of: Optional[datetime] = None
    ) -> Optional[Any]:
        """
        Get the latest analytics snapshot for a symbol.

        Args:
            symbol: The instrument key.
            as_of: Optional timestamp for point-in-time queries (backtest).
                   If None, returns the most recent snapshot.

        Returns:
            ConfluenceInsight object or None if not available.
        """
        pass

    @abstractmethod
    def get_market_regime(
        self, symbol: str, as_of: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get the current market regime for a symbol.

        Args:
            symbol: The instrument key.
            as_of: Optional timestamp for point-in-time queries.

        Returns:
            Dictionary with regime information or None.
        """
        pass
