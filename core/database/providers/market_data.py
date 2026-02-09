"""
DuckDBMarketDataProvider - Historical Data Provider
---------------------------------------------------
Provides OHLCV bars from DuckDB for backtesting.

This provider pre-loads all data for the requested time range and
iterates through it sequentially, simulating the passage of time.
"""

from datetime import datetime
from typing import Optional, List, Dict, Tuple
from pathlib import Path
import pandas as pd

from core.database.providers.base import MarketDataProvider
from core.database.queries import MarketDataQuery
from core.database.manager import DatabaseManager
from core.events import OHLCVBar


class DuckDBMarketDataProvider(MarketDataProvider):
    """
    Provides OHLCV bars from DuckDB for backtesting.
    """

    def __init__(
        self,
        symbols: List[str],
        db_manager: Optional[DatabaseManager] = None,
        table_name: str = "candles",
        timeframe: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        data_root: Optional[str] = None,
    ):
        """
        Initialize the market data provider.
        """
        super().__init__(symbols)

        self.table_name = table_name
        self.timeframe = timeframe
        self.start_time = start_time
        self.end_time = end_time

        # Initialize database connection
        if db_manager:
            self._db = db_manager
        else:
            self._db = DatabaseManager(data_root or Path("data"))

        self._query = MarketDataQuery(self._db)

        # Data storage: symbol -> list of OHLCVBar
        self._data: Dict[str, List[OHLCVBar]] = {}

        # Current position: symbol -> index
        self._indices: Dict[str, int] = {}

        # Load all data upfront
        self._load_data()

    def _load_data(self) -> None:
        """Pre-load all data for performance."""
        # Always request 1m data and resample in-memory if needed
        # This avoids needing multiple DuckDB files per timeframe.
        target_tf = self.timeframe or "1m"
        
        # Internal mapping for common variations
        tf_map = {
            "5minute": "5m",
            "15minute": "15m",
            "60minute": "1h",
            "1day": "1d",
        }
        target_tf = tf_map.get(target_tf, target_tf)

        for symbol in self.symbols:
            # Always fetch 1m data from the database
            df = self._query.get_ohlcv(
                instrument_key=symbol,
                start_time=self.start_time,
                end_time=self.end_time,
                timeframe="1m",
            )

            if not df.empty and target_tf != "1m":
                from core.analytics.resampler import resample_ohlcv
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                df = resample_ohlcv(df, target_tf)

            bars = []
            if not df.empty:
                for _, row in df.iterrows():
                    bars.append(
                        OHLCVBar(
                            symbol=symbol,
                            timestamp=row["timestamp"],
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=int(row["volume"]) if "volume" in row and row["volume"] else 0,
                        )
                    )

            self._data[symbol] = bars
            self._indices[symbol] = 0

    def get_next_bar(self, symbol: str) -> Optional[OHLCVBar]:
        if symbol not in self._data:
            return None

        idx = self._indices.get(symbol, 0)
        bars = self._data[symbol]

        if idx >= len(bars):
            return None

        bar = bars[idx]
        self._indices[symbol] = idx + 1
        return bar

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        if symbol not in self._data:
            return None

        idx = self._indices.get(symbol, 0)
        bars = self._data[symbol]

        if idx > 0:
            return bars[idx - 1]
        elif bars:
            return bars[0]
        return None

    def is_data_available(self, symbol: str) -> bool:
        if symbol not in self._data:
            return False
        return self._indices.get(symbol, 0) < len(self._data[symbol])

    def reset(self, symbol: str) -> None:
        if symbol in self._indices:
            self._indices[symbol] = 0

    def get_progress(self, symbol: str) -> Tuple[int, int]:
        if symbol not in self._data:
            return (0, 0)
        return (self._indices.get(symbol, 0), len(self._data[symbol]))

    def get_bar_count(self, symbol: str) -> int:
        return len(self._data.get(symbol, []))

    def get_time_range(self, symbol: str) -> Tuple[Optional[datetime], Optional[datetime]]:
        bars = self._data.get(symbol, [])
        if not bars:
            return (None, None)
        return (bars[0].timestamp, bars[-1].timestamp)
