"""
Daily Regime Strategy V2
------------------------
Adapts trading behavior based on the current market regime.
"""
from typing import Optional
from core.strategies.base import BaseStrategy, StrategyContext
from core.events import OHLCVBar, SignalEvent, SignalType

class DailyRegimeStrategyV2(BaseStrategy):
    """
    Strategy that uses regime detection to filter entries.
    """
    
    def process_bar(self, bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        if not context.market_regime:
            return None
            
        regime = context.market_regime.get("regime")
        
        # Only trade in Trending regimes
        if regime in ["BULL_TREND", "BEAR_TREND"]:
            if context.analytics_snapshot and context.analytics_snapshot.signal.value == "BUY":
                return SignalEvent(
                    strategy_id=self.strategy_id,
                    symbol=bar.symbol,
                    timestamp=bar.timestamp,
                    signal_type=SignalType.BUY,
                    confidence=0.7
                )
        return None
