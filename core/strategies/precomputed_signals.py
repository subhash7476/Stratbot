from typing import Optional, Dict, List
from core.strategies.base import BaseStrategy, StrategyContext
from core.events import OHLCVBar, SignalEvent

class PrecomputedSignalStrategy(BaseStrategy):
    """
    A thin strategy that returns pre-computed signals based on timestamp matching.
    Used for batch backtesting where signals are generated vectorized upfront.
    """
    def __init__(self, strategy_id: str, signals: List[SignalEvent], config: Optional[Dict] = None):
        super().__init__(strategy_id, config)
        # Index signals by timestamp for fast lookup
        # If multiple signals per timestamp (different symbols), store as list
        self.signal_map: Dict[str, List[SignalEvent]] = {}
        for sig in signals:
            ts_key = sig.timestamp.isoformat()
            if ts_key not in self.signal_map:
                self.signal_map[ts_key] = []
            self.signal_map[ts_key].append(sig)

    def process_bar(self, bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        ts_key = bar.timestamp.isoformat()
        signals = self.signal_map.get(ts_key, [])
        
        # Return the signal that matches the symbol
        for sig in signals:
            if sig.symbol == bar.symbol:
                return sig
        return None
