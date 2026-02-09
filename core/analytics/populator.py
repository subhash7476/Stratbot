"""
Refactored Analytics Populator
----------------------------
Batch process to compute and store insights using DatabaseManager.
"""
import pandas as pd
import logging
from typing import List, Optional, Any
from datetime import datetime, timedelta
from pathlib import Path

from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.database.legacy_adapter import save_insights, save_regime_snapshot
from core.analytics.confluence_engine import ConfluenceEngine
from core.analytics.models import ConfluenceInsight
from core.analytics.regime_engine import RegimeSnapshot, RegimeDetector

logger = logging.getLogger(__name__)

class AnalyticsPopulator:
    """
    Coordinates the calculation and storage of analytics for all symbols.
    Uses MarketDataQuery to bridge historical and live data.
    """
    
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager(Path("data"))
        self.query = MarketDataQuery(self.db)
        self.confluence_engine = ConfluenceEngine()
        self.regime_detector = RegimeDetector()

    def update_all(self, symbols: List[str], backfill: bool = True, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None, timeframe: str = '1m'):
        """
        Calculates latest insights for a list of symbols.
        """
        for symbol in symbols:
            if backfill:
                self._backfill_symbol(symbol, start_date=start_date, end_date=end_date, timeframe=timeframe)
            else:
                self._update_symbol(symbol)

    def _update_symbol(self, symbol: str):
        try:
            # Load recent data
            now = datetime.now()
            start = now - timedelta(days=2)
            df = self.query.get_candles(symbol, 'nse', '1m', start, now)
            
            if df.empty:
                logger.warning(f"No data found for {symbol}")
                return

            # Generate Confluence Insight
            insight = self.confluence_engine.generate_insight(symbol, df)
            if insight:
                from core.database.legacy_adapter import save_insight
                save_insight(insight)
                logger.info(f"Saved confluence insight for {symbol}")

            # Generate Regime Snapshot
            snapshot = self.regime_detector.detect(symbol, df)
            if snapshot:
                save_regime_snapshot(snapshot)
                logger.info(f"Saved regime snapshot for {symbol}: {snapshot.regime}")
            
        except Exception as e:
            logger.error(f"Failed to update analytics for {symbol}: {e}")

    def _backfill_symbol(self, symbol: str, window_size: int = 100, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None, timeframe: str = '1m'):
        """
        Generates historical insights by sliding a window over OHLCV data.
        """
        try:
            logger.info(f"Starting analytics backfill for {symbol}...")
            
            # Load data using MarketDataQuery
            # We need window_size bars BEFORE start_date
            lookback_days = {'1m': 2, '5m': 5, '15m': 10, '1h': 30, '1d': 200}
            fetch_start = (start_date or (datetime.now() - timedelta(days=lookback_days.get(timeframe, 2)))) - timedelta(days=lookback_days.get(timeframe, 2))
            fetch_end = end_date or datetime.now()

            logger.info(f"Backfill query: symbol={symbol}, exchange=nse, tf={timeframe}, start={fetch_start}, end={fetch_end}, data_root={self.db.data_root}")
            df = self.query.get_candles(symbol, 'nse', timeframe, fetch_start, fetch_end)

            if len(df) < window_size:
                logger.warning(f"Insufficient data for backfill: {symbol} ({len(df)} bars, need {window_size})")
                return

            # Determine start index
            start_idx = window_size
            if start_date:
                # Localize start_date if needed for comparison
                ts_start = pd.Timestamp(start_date).tz_localize(None)
                # Ensure df['timestamp'] is also naive for comparison
                df_ts = df['timestamp'].dt.tz_localize(None) if df['timestamp'].dt.tz else df['timestamp']
                
                future_bars = df[df_ts >= ts_start]
                if not future_bars.empty:
                    actual_start_idx = df.index.get_loc(future_bars.index[0])
                    start_idx = max(window_size, actual_start_idx)

            # Use vectorized bulk generation
            # Note: iloc is 0-based, so we take from start_idx - window_size to the end
            insights = self.confluence_engine.generate_insights_bulk(symbol, df.iloc[start_idx-window_size:])
            
            if insights:
                save_insights(insights)
                
            logger.info(f"Backfill complete for {symbol}. Processed {len(insights)} bars.")
            
        except Exception as e:
            logger.error(f"Backfill failed for {symbol}: {e}")
            import traceback
            traceback.print_exc()
