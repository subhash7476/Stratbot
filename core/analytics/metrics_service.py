"""
Structural Metrics Service
--------------------------
Maintains rolling 60-day buffers for Dispersion (CSAD) and Volatility (ATR).
Provides frozen percentile snapshots for Trade Learning Protocol V1.
"""
import pandas as pd
import numpy as np
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List
from pathlib import Path

from core.database.manager import DatabaseManager

logger = logging.getLogger(__name__)

class StructuralMetricsService:
    def __init__(self, db_manager: DatabaseManager, window_size: int = 60):
        self.db = db_manager
        self.window_size = window_size
        self._dispersion_buffer: List[float] = []
        self._volatility_buffer: List[float] = []
        self._last_loaded_date: Optional[date] = None

    def _ensure_buffer_loaded(self, as_of: date):
        """Lazy load historical metrics from signals.db."""
        if self._last_loaded_date == as_of:
            return

        try:
            with self.db.signals_reader() as conn:
                query = """
                    SELECT dispersion_csad, volatility_atr 
                    FROM daily_structural_metrics 
                    WHERE timestamp < ? 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """
                rows = conn.execute(query, [as_of.isoformat(), self.window_size]).fetchall()
                
                if not rows:
                    self._dispersion_buffer = []
                    self._volatility_buffer = []
                else:
                    self._dispersion_buffer = [r[0] for r in rows if r[0] is not None]
                    self._volatility_buffer = [r[1] for r in rows if r[1] is not None]
                    
            self._last_loaded_date = as_of
            logger.debug(f"Loaded {len(self._dispersion_buffer)} days of structural context.")
        except Exception as e:
            logger.error(f"Failed to load structural metrics buffer: {e}")
            self._dispersion_buffer = []
            self._volatility_buffer = []

    def get_percentiles(self, current_csad: float, current_atr: float, as_of: date) -> Dict[str, float]:
        """Compute percentiles for given values against the historical buffer."""
        self._ensure_buffer_loaded(as_of)
        
        def calc_pctl(val, buffer):
            if not buffer:
                return 50.0  # Neutral default
            return round((sum(1 for x in buffer if x < val) / len(buffer)) * 100, 1)

        return {
            "dispersion_pct": calc_pctl(current_csad, self._dispersion_buffer),
            "volatility_pct": calc_pctl(current_atr, self._volatility_buffer)
        }

    def update_daily_metrics(self, ts: datetime, csad: float, atr: float):
        """Persist today's metrics to signals.db for future sessions."""
        from core.database.schema import SIGNALS_DAILY_METRICS_SCHEMA
        try:
            with self.db.signals_writer() as conn:
                conn.execute(SIGNALS_DAILY_METRICS_SCHEMA)
                conn.execute("""
                    INSERT INTO daily_structural_metrics (timestamp, dispersion_csad, volatility_atr)
                    VALUES (?, ?, ?)
                    ON CONFLICT(timestamp) DO UPDATE SET
                        dispersion_csad = excluded.dispersion_csad,
                        volatility_atr = excluded.volatility_atr
                """, [ts.date().isoformat(), csad, atr])
            logger.info(f"Updated daily structural metrics for {ts.date()}")
        except Exception as e:
            logger.error(f"Failed to update daily structural metrics: {e}")
