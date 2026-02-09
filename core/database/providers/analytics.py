"""
Analytics Providers
-------------------
Providers for pre-computed and real-time analytics data.

DuckDBAnalyticsProvider: Direct database queries for analytics.
LiveConfluenceProvider: Real-time calculation of indicators.
CachedAnalyticsProvider: In-memory caching wrapper for performance.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from pathlib import Path
import json
import logging

from core.database.providers.base import AnalyticsProvider
from core.database.queries import AnalyticsQuery, MarketDataQuery
from core.database.manager import DatabaseManager
from core.analytics.models import ConfluenceInsight, Bias, ConfluenceSignal, IndicatorResult
from core.analytics.confluence_engine import ConfluenceEngine

logger = logging.getLogger(__name__)

class DuckDBAnalyticsProvider(AnalyticsProvider):
    """
    Provides analytics snapshots from DuckDB.
    """

    def __init__(self, db_manager: DatabaseManager):
        """
        Initialize the analytics provider.

        Args:
            db_manager: Shared DatabaseManager instance.
        """
        self._db = db_manager
        self._query = AnalyticsQuery(self._db)

        # Optional: Pre-loaded data for backtesting performance
        self._preloaded_insights: Dict[str, List[Dict]] = {}
        self._preloaded_regimes: Dict[str, List[Dict]] = {}

    def pre_load(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        """
        Pre-load analytics for a time range (performance optimization).
        """
        # Load insights
        insights = self._query.get_insights(
            symbol=symbol,
            start_time=start_time,
            end_time=end_time,
            limit=100000,
        )
        self._preloaded_insights[symbol] = insights

        # Load regimes
        regime = self._query.get_market_regime(symbol, end_time)
        if regime:
            self._preloaded_regimes[symbol] = [regime]

    def get_latest_snapshot(
        self, symbol: str, as_of: Optional[datetime] = None
    ) -> Optional[ConfluenceInsight]:
        """
        Get the latest analytics snapshot for a symbol.
        """
        # Check pre-loaded data first
        if symbol in self._preloaded_insights and as_of is not None:
            insights = self._preloaded_insights[symbol]
            latest = None
            for insight in insights:
                if insight["timestamp"] <= as_of:
                    latest = insight
                else:
                    break
            if latest:
                return self._dict_to_insight(latest)

        # Fall back to database query
        result = self._query.get_latest_insight(symbol, as_of)
        if result:
            return self._dict_to_insight(result)
        return None

    def get_market_regime(
        self, symbol: str, as_of: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get the current market regime for a symbol.
        """
        # Check pre-loaded data first
        if symbol in self._preloaded_regimes and as_of:
            regimes = self._preloaded_regimes[symbol]
            latest = None
            for regime in regimes:
                if regime["timestamp"] <= as_of:
                    latest = regime
                else:
                    break
            if latest:
                return latest

        # Fall back to database query
        return self._query.get_market_regime(symbol, as_of)

    def _dict_to_insight(self, data: Dict[str, Any]) -> ConfluenceInsight:
        """Convert a dictionary to ConfluenceInsight object."""
        indicator_results = []
        if data.get("indicator_results"):
            try:
                if isinstance(data["indicator_results"], str):
                    results_data = json.loads(data["indicator_results"])
                else:
                    results_data = data["indicator_results"]

                for ir in results_data:
                    indicator_results.append(
                        IndicatorResult(
                            name=ir.get("name", ""),
                            bias=Bias(ir.get("bias", "NEUTRAL")),
                            value=float(ir.get("value", 0.0)),
                            metadata=ir.get("metadata", {}),
                        )
                    )
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        bias_str = data.get("bias", "NEUTRAL")
        try:
            bias = Bias(bias_str)
        except ValueError:
            bias = Bias.NEUTRAL

        signal_str = data.get("signal", "NEUTRAL")
        try:
            signal = ConfluenceSignal(signal_str)
        except ValueError:
            signal = ConfluenceSignal.NEUTRAL

        return ConfluenceInsight(
            timestamp=data["timestamp"],
            symbol=data["symbol"],
            bias=bias,
            confidence_score=float(data.get("confidence_score", 0.0)),
            indicator_results=indicator_results,
            signal=signal,
            agreement_level=float(data.get("agreement_level", 0.0)),
        )


class LiveConfluenceProvider(AnalyticsProvider):
    """
    Computes confluence insights in real-time.
    Used for live trading when pre-computed analytics aren't available.
    """

    def __init__(self, db_manager: DatabaseManager):
        self._db = db_manager
        self._query = MarketDataQuery(self._db)
        self._engine = ConfluenceEngine()
        self._last_calc: Dict[str, datetime] = {}
        self._cached_insight: Dict[str, ConfluenceInsight] = {}

    def get_latest_snapshot(self, symbol: str, as_of: Optional[datetime] = None) -> Optional[ConfluenceInsight]:
        """
        Calculates indicators on the fly using recent history.
        """
        # Throttling: only recalculate once per 30s per symbol if as_of is close
        now = as_of or datetime.now()
        if symbol in self._last_calc:
            if (now - self._last_calc[symbol]).total_seconds() < 30:
                return self._cached_insight.get(symbol)

        try:
            # Fetch last 100 bars for accurate indicator calculation
            df = self._query.get_ohlcv(
                instrument_key=symbol,
                timeframe="1m",
                limit=100
            )
            
            if df.empty or len(df) < 50:
                return None
                
            insight = self._engine.generate_insight(symbol, df)
            if insight:
                self._last_calc[symbol] = now
                self._cached_insight[symbol] = insight
            return insight
            
        except Exception as e:
            logger.error(f"Live analytics calculation failed for {symbol}: {e}")
            return None

    def get_market_regime(self, symbol: str, as_of: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """
        Returns a simplified regime for live trading.
        """
        return {
            "regime": "TRENDING",
            "momentum_bias": "NEUTRAL",
            "trend_strength": 0.5,
            "volatility_level": "NORMAL",
            "persistence_score": 0.5
        }


class CachedAnalyticsProvider(AnalyticsProvider):
    """
    In-memory caching wrapper for analytics providers.
    """

    def __init__(
        self,
        base_provider: AnalyticsProvider,
        cache_size: int = 1000,
    ):
        self._base = base_provider
        self._cache_size = cache_size
        self._snapshot_cache: Dict[str, Any] = {}
        self._regime_cache: Dict[str, Any] = {}
        self._access_order: int = 0

    def _make_key(self, symbol: str, as_of: Optional[datetime]) -> str:
        ts_str = as_of.isoformat() if as_of else "latest"
        return f"{symbol}:{ts_str}"

    def _evict_if_needed(self, cache: Dict) -> None:
        if len(cache) >= self._cache_size:
            to_remove = self._cache_size // 10
            sorted_keys = sorted(
                cache.keys(),
                key=lambda k: cache[k][1] if isinstance(cache[k], tuple) else 0,
            )
            for key in sorted_keys[:to_remove]:
                del cache[key]

    def get_latest_snapshot(
        self, symbol: str, as_of: Optional[datetime] = None
    ) -> Optional[Any]:
        key = self._make_key(symbol, as_of)
        if key in self._snapshot_cache:
            value, _ = self._snapshot_cache[key]
            self._access_order += 1
            self._snapshot_cache[key] = (value, self._access_order)
            return value

        result = self._base.get_latest_snapshot(symbol, as_of)
        self._evict_if_needed(self._snapshot_cache)
        self._access_order += 1
        self._snapshot_cache[key] = (result, self._access_order)
        return result

    def get_market_regime(
        self, symbol: str, as_of: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        key = self._make_key(symbol, as_of)
        if key in self._regime_cache:
            value, _ = self._regime_cache[key]
            self._access_order += 1
            self._regime_cache[key] = (value, self._access_order)
            return value

        result = self._base.get_market_regime(symbol, as_of)
        self._evict_if_needed(self._regime_cache)
        self._access_order += 1
        self._regime_cache[key] = (result, self._access_order)
        return result
