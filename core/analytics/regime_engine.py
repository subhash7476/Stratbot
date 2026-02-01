"""
Regime Detection Engine
----------------------
Categorizes market state based on volatility, trend, and momentum.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass(frozen=True)
class RegimeSnapshot:
    insight_id: str
    symbol: str
    timestamp: datetime
    regime: str # e.g., 'BULL_TREND', 'BEAR_VOLATILE', 'RANGING'
    momentum_bias: str
    trend_strength: float
    volatility_level: str
    persistence_score: float
    ma_fast: float
    ma_medium: float
    ma_slow: float
