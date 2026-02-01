from core.strategies.ehma_pivot import EHMAPivotStrategy
from core.strategies.base import StrategyContext
from core.events import OHLCVBar, SignalType
from datetime import datetime

def test_ehma_pivot_logic():
    strat = EHMAPivotStrategy("ehma_pivot")
    bar = OHLCVBar("SYM", datetime.now(), 100, 105, 95, 102, 1000)
    # Context
    context = StrategyContext("SYM", 0, None, None, {})
    pass
