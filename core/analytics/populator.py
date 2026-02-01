"""
Analytics Populator
-----------------
Batch process to compute and store insights.
"""
import pandas as pd
import logging
from typing import List
from datetime import datetime

from core.data.duckdb_client import db_cursor
from core.data.analytics_persistence import save_insight, save_regime_snapshot
from core.analytics.confluence_engine import ConfluenceEngine
from core.analytics.regime_engine import RegimeSnapshot

logger = logging.getLogger(__name__)

class AnalyticsPopulator:
    """
    Coordinates the calculation and storage of analytics for all symbols.
    """
    
    def __init__(self, db_path: str = "data/trading_bot.duckdb"):
        self.db_path = db_path
        self.confluence_engine = ConfluenceEngine()

    def update_all(self, symbols: List[str]):
        """
        Calculates latest insights for a list of symbols.
        """
        for symbol in symbols:
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
