"""
Base Strategy Interface
-----------------------
Defines the contract for all trading strategies.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any
from core.events import OHLCVBar, SignalEvent
from core.analytics.models import ConfluenceInsight

@dataclass
class StrategyContext:
    symbol: str
    current_position: float
    analytics_snapshot: Optional[ConfluenceInsight]
    market_regime: Optional[Dict[str, Any]]
    strategy_params: Dict[str, Any]

class BaseStrategy(ABC):
    """
    Abstract base class for all intent-only strategies.
    """
    
    def __init__(self, strategy_id: str, config: Optional[Dict] = None):
        self.strategy_id = strategy_id
        self.config = config or {}
        self.is_enabled = True

    @abstractmethod
    def process_bar(self, bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        """
        Processes a single bar and optionally emits a signal.
        """
        pass
