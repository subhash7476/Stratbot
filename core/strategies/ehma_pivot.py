"""Legacy-compatible EHMA pivot strategy shim."""

from typing import Optional

from core.events import OHLCVBar, SignalEvent, SignalType
from core.strategies.base import BaseStrategy, StrategyContext


class EHMAPivotStrategy(BaseStrategy):
    """Minimal EHMA pivot strategy used by legacy tests."""

    def process_bar(self, bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        if not context.analytics_snapshot:
            return None

        signal_val = getattr(context.analytics_snapshot.signal, "value", "")
        if signal_val == "BUY":
            return SignalEvent(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                timestamp=bar.timestamp,
                signal_type=SignalType.BUY,
                confidence=0.8,
            )
        if signal_val == "SELL":
            return SignalEvent(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                timestamp=bar.timestamp,
                signal_type=SignalType.SELL,
                confidence=0.8,
            )
        return None

