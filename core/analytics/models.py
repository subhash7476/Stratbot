"""
Analytical Snapshots & Models
-----------------------------
Immutable representations of indicator states and market insights.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Any

class Bias(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

class ConfluenceSignal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"

@dataclass(frozen=True)
class IndicatorResult:
    name: str
    bias: Bias
    value: float
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class ConfluenceInsight:
    timestamp: datetime
    symbol: str
    bias: Bias
    confidence_score: float # 0.0 to 1.0
    indicator_results: List[IndicatorResult]
    signal: ConfluenceSignal
    agreement_level: float = 0.0 # 0.0 to 1.0
