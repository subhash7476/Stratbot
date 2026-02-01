"""
Trade Truth Model
-----------------
Explains why a trade happened by linking it to analytical facts.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional
from core.analytics.models import ConfluenceInsight

@dataclass(frozen=True)
class TradeTruth:
    trade_id: str
    symbol: str
    timestamp: datetime
    bias_at_entry: str
    confidence_at_entry: float
    indicator_facts: Dict[str, Any]
    regime_at_entry: Optional[str]
