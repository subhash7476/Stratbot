"""
Analytics Populator
-----------------
Batch process to compute and store insights.
"""
import pandas as pd
import logging
from typing import List, Optional
from datetime import datetime

from core.data.duckdb_client import db_cursor
from core.data.analytics_persistence import save_insight, save_regime_snapshot
from core.analytics.confluence_engine import ConfluenceEngine
from core.analytics.models import ConfluenceInsight
from core.analytics.regime_engine import RegimeSnapshot

logger = logging.getLogger(__name__)

class AnalyticsPopulator:
    """
    Coordinates the calculation and storage of analytics for all symbols.
    """
    
    def __init__(self, db_path: str = "data/trading_bot.duckdb"):
        self.db_path = db_path
        self.confluence_engine = ConfluenceEngine()

    def update_all(self, symbols: List[str], backfill: bool = True, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None, timeframe: str = '1m'):
        """
        Calculates latest insights for a list of symbols.
        If backfill is True, it attempts to generate insights for historical bars.
        """
        for symbol in symbols:
            if backfill:
                self._backfill_symbol(symbol, start_date=start_date, end_date=end_date, timeframe=timeframe)
            else:
                self._update_symbol(symbol)

    def _update_symbol(self, symbol: str):
        try:
            # 1. Load data from DuckDB
            with db_cursor(self.db_path, read_only=True) as conn:
                df = conn.execute(
                    "SELECT timestamp, open, high, low, close, volume FROM ohlcv_1m WHERE instrument_key = ? ORDER BY timestamp ASC",
                    [symbol]
                ).fetchdf()
            
            if df.empty:
                logger.warning(f"No data found for {symbol}")
                return

            # 2. Generate Confluence Insight
            insight = self.confluence_engine.generate_insight(symbol, df)
            if insight:
                save_insight(insight, self.db_path)
                logger.info(f"Saved confluence insight for {symbol}")

            # 3. Generate Regime Snapshot
            # (Simplified regime detection)
            snapshot = RegimeSnapshot(
                insight_id=f"regime_{symbol}_{datetime.now().strftime('%Y%m%d%H%M')}",
                symbol=symbol,
                timestamp=datetime.now(),
                regime="BULL_TREND", # placeholder
                momentum_bias="BULLISH",
                trend_strength=0.8,
                volatility_level="LOW",
                persistence_score=0.9,
                ma_fast=0.0, ma_medium=0.0, ma_slow=0.0
            )
            save_regime_snapshot(snapshot, self.db_path)
            
        except Exception as e:
            logger.error(f"Failed to update analytics for {symbol}: {e}")

    def _backfill_symbol(self, symbol: str, window_size: int = 100, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None, timeframe: str = '1m'):
        """
        Generates historical insights by sliding a window over OHLCV data.
        """
        try:
            logger.info(f"Starting analytics backfill for {symbol}...")
            # 1. Load data from DuckDB with range filtering
            # We need window_size bars BEFORE start_date to calculate the first indicator
            # Select table and filter based on timeframe
            tf_map = {'1m': (None, 'ohlcv_1m'), '5m': ('5minute', 'ohlcv_resampled'),
                      '15m': ('15minute', 'ohlcv_resampled'), '1h': ('60minute', 'ohlcv_resampled'),
                      '1d': ('1day', 'ohlcv_resampled')}
            tf_key, tf_table = tf_map.get(timeframe, (None, 'ohlcv_1m'))

            query = f"SELECT timestamp, open, high, low, close, volume FROM {tf_table} WHERE instrument_key = ?"
            from typing import Any
            params: List[Any] = [symbol]

            if tf_key:
                query += " AND timeframe = ?"
                params.append(tf_key)
            
            if start_date:
                # Scale lookback based on timeframe to ensure window_size bars are available
                from datetime import timedelta
                lookback_days = {'1m': 2, '5m': 5, '15m': 10, '1h': 30, '1d': 200}
                lookback_start = start_date - timedelta(days=lookback_days.get(timeframe, 2))
                query += " AND timestamp >= ?"
                params.append(lookback_start)
            
            if end_date:
                query += " AND timestamp <= ?"
                params.append(end_date)
                
            query += " ORDER BY timestamp ASC"
            
            with db_cursor(self.db_path, read_only=True) as conn:
                df = conn.execute(query, params).fetchdf()
            
            if len(df) < window_size:
                logger.warning(f"Insufficient data for backfill: {symbol} ({len(df)} bars)")
                return

            # Determine where to start processing
            # If start_date is provided, find the first index >= start_date
            start_idx = int(window_size)
            if start_date:
                # Find index of first bar >= start_date
                # Convert start_date to pandas timestamp for comparison
                ts_start = pd.Timestamp(start_date).tz_localize(None) if start_date.tzinfo is None else pd.Timestamp(start_date)
                future_bars = df[df['timestamp'] >= ts_start]
                if not future_bars.empty:
                    # We start at the index of the first bar in range, 
                    # ensuring we have window_size bars before it.
                    actual_start_idx = int(future_bars.index.values[0])
                    start_idx = max(int(window_size), actual_start_idx)

            insights_to_save = []
            total_to_process = int(len(df)) - start_idx
            processed_count = 0
            
            if total_to_process <= 0:
                logger.info(f"No new bars to process for {symbol} in the requested range.")
                return

            logger.info(f"Processing {total_to_process} bars for {symbol} starting from index {start_idx}")
            
            # Use vectorized bulk generation
            insights = self.confluence_engine.generate_insights_bulk(symbol, df.iloc[start_idx-window_size:])
            
            if insights:
                from core.data.analytics_persistence import save_insights
                save_insights(insights, self.db_path)
                processed_count = len(insights)
                
            logger.info(f"Backfill complete for {symbol}. Processed {processed_count} bars.")
            
        except Exception as e:
            logger.error(f"Backfill failed for {symbol}: {e}")
            import traceback
            traceback.print_exc()
