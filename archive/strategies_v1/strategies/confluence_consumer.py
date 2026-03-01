"""
Confluence Consumer Strategy
---------------------------
Aggregates multiple indicator facts into a high-confidence signal.
"""
from typing import Optional
from core.strategies.base import BaseStrategy, StrategyContext
from core.events import OHLCVBar, SignalEvent, SignalType

class ConfluenceConsumerStrategy(BaseStrategy):
    """
    Strategy that consumes pre-computed confluence facts.
    """
    
    def process_bar(self, bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        if not context.analytics_snapshot:
            return None
            
        # Example logic based on confidence score
        if context.analytics_snapshot.confidence_score > 0.7:
            if context.analytics_snapshot.signal.value == "BUY":
                return SignalEvent(
                    strategy_id=self.strategy_id,
                    symbol=bar.symbol,
                    timestamp=bar.timestamp,
                    signal_type=SignalType.BUY,
                    confidence=context.analytics_snapshot.confidence_score
                )
            elif context.analytics_snapshot.signal.value == "SELL":
                return SignalEvent(
                    strategy_id=self.strategy_id,
                    symbol=bar.symbol,
                    timestamp=bar.timestamp,
                    signal_type=SignalType.SELL,
                    confidence=context.analytics_snapshot.confidence_score
                )
        return None
