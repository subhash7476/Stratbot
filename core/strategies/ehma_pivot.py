"""
EHMA Pivot Strategy
-------------------
Pure price-action strategy using EHMA crossovers.
"""
from typing import Optional
from core.strategies.base import BaseStrategy, StrategyContext
from core.events import OHLCVBar, SignalEvent, SignalType

class EHMAPivotStrategy(BaseStrategy):
    """
    Strategy that uses Hull Moving Average (EHMA) crossovers.
    """
    
    def process_bar(self, bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        # Simple logic for demo
        if not context.analytics_snapshot:
            return None
            
        # Example condition (real logic would use EHMA values from snapshot)
        if context.analytics_snapshot.signal.value == "BUY":
            return SignalEvent(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                timestamp=bar.timestamp,
                signal_type=SignalType.BUY,
                confidence=0.8
            )
        elif context.analytics_snapshot.signal.value == "SELL":
            return SignalEvent(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                timestamp=bar.timestamp,
                signal_type=SignalType.SELL,
                confidence=0.8
            )
        return None
