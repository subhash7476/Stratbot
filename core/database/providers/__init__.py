"""
Database Providers Package
--------------------------
Data provider implementations for market data and analytics.

Usage:
    from core.database.providers import (
        MarketDataProvider,
        AnalyticsProvider,
        DuckDBMarketDataProvider,
        DuckDBAnalyticsProvider,
        CachedAnalyticsProvider,
        LiveDuckDBMarketDataProvider,
    )
"""

from core.database.providers.base import MarketDataProvider, AnalyticsProvider
from core.database.providers.market_data import DuckDBMarketDataProvider
from core.database.providers.analytics import (
    DuckDBAnalyticsProvider, 
    CachedAnalyticsProvider,
    LiveConfluenceProvider
)
from core.database.providers.live_market import LiveDuckDBMarketDataProvider
from core.database.providers.zmq_market import ZmqMarketDataProvider

__all__ = [
    # Base interfaces
    "MarketDataProvider",
    "AnalyticsProvider",
    # Implementations
    "DuckDBMarketDataProvider",
    "DuckDBAnalyticsProvider",
    "CachedAnalyticsProvider",
    "LiveConfluenceProvider",
    "LiveDuckDBMarketDataProvider",
    "ZmqMarketDataProvider",
]
