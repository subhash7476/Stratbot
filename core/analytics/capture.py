"""
Capture Engine
--------------
Snapshots market structural state at signal generation time.
Universal capture service for Trade Learning Protocol V1.
"""
from datetime import datetime, time
import logging
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
import pandas as pd

from core.database.manager import DatabaseManager
from core.analytics.metrics_service import StructuralMetricsService
from core.events import TradeStructuralContext

logger = logging.getLogger(__name__)

class CaptureEngine:
    def __init__(self, db_manager: DatabaseManager, metrics_service: StructuralMetricsService):
        self.db = db_manager
        self.metrics = metrics_service
        self._nifty_universe = []
        self._load_universe()

    def _load_universe(self):
        """Load the fixed Nifty universe version 1."""
        csv_path = Path("data/nifty-50-stock-list.csv")
        if csv_path.exists():
            try:
                df = pd.read_csv(csv_path)
                self._nifty_universe = df['Symbol'].tolist()
            except Exception as e:
                logger.error(f"Failed to load Nifty universe CSV: {e}")

    def capture_context(self, 
                        symbol: str, 
                        timestamp: datetime, 
                        signal_rank: int, 
                        signal_percentile: float,
                        sl_distance: float, 
                        risk_r: float,
                        signal_score: float = 0.0) -> TradeStructuralContext:
        """
        Snapshots the structural truth at this specific timestamp.
        """
        # 1. HMM Regime from previous session EOD
        regime, confidence = self._get_previous_regime(timestamp)
        
        # 2. Session Type
        session_type = "AM" if timestamp.time() < time(12, 30) else "PM"
        
        # 3. Breadth (Adv/Dec)
        breadth = self._calculate_breadth(timestamp)
        
        # 4. Dispersion & Volatility (Percentiles)
        csad, atr = self._get_current_metrics(timestamp)
        pctls = self.metrics.get_percentiles(csad, atr, timestamp.date())
        
        # 5. Index Trend (Return from open to now)
        index_trend = self._get_index_trend(timestamp)

        return TradeStructuralContext(
            regime_state=regime,
            regime_confidence=confidence,
            session_type=session_type,
            index_trend=index_trend,
            dispersion_value=csad,
            dispersion_pct=pctls["dispersion_pct"],
            volatility_value=atr,
            volatility_pct=pctls["volatility_pct"],
            breadth_ratio=breadth,
            signal_rank=signal_rank,
            signal_score=signal_score,
            signal_percentile=signal_percentile,
            sl_distance=sl_distance,
            risk_r=risk_r,
            model_version="TLP_V1_CORE",
            universe_version="NIFTY_UNIVERSE_V1"
        )

    def _get_previous_regime(self, ts: datetime) -> Tuple[str, float]:
        """Fetches the finalized HMM state from the last trading day."""
        try:
            with self.db.signals_reader() as conn:
                row = conn.execute("""
                    SELECT regime, persistence_score 
                    FROM regime_insights 
                    WHERE timestamp < ? 
                    ORDER BY timestamp DESC LIMIT 1
                """, [ts.date().isoformat()]).fetchone()
                if row:
                    return str(row[0]), float(row[1])
        except Exception:
            pass
        return "UNKNOWN", 0.0

    def _calculate_breadth(self, ts: datetime) -> float:
        """Approximates breadth by comparing current prices to open."""
        # For V1, we return neutral if real-time scanning is not implemented in runner
        return 0.5

    def _get_current_metrics(self, ts: datetime) -> Tuple[float, float]:
        """Approximates CSAD and ATR for percentile snapshot."""
        # Pull latest available metrics from signals.db
        try:
            with self.db.signals_reader() as conn:
                row = conn.execute("""
                    SELECT dispersion_csad, volatility_atr 
                    FROM daily_structural_metrics 
                    WHERE timestamp <= ? 
                    ORDER BY timestamp DESC LIMIT 1
                """, [ts.date().isoformat()]).fetchone()
                if row:
                    return float(row[0]), float(row[1])
        except Exception:
            pass
        return 0.0, 0.0

    def _get_index_trend(self, ts: datetime) -> str:
        """Placeholder for index trend logic."""
        return "NEUTRAL"
