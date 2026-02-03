"""
Strategy Registry
-----------------
Centralized factory for instantiating trading strategies.
"""
from typing import Dict, Type, List, Optional
from core.strategies.base import BaseStrategy
from core.strategies.ehma_pivot import EHMAPivotStrategy
from core.strategies.confluence_consumer import ConfluenceConsumerStrategy
from core.strategies.daily_regime_strategy_v2 import DailyRegimeStrategyV2
from core.strategies.regime_adaptive import RegimeAdaptiveStrategy
from core.strategies.premium_tp_sl import PremiumTpSlStrategy

# symbol -> Strategy Class
STRATEGY_MAP: Dict[str, Type[BaseStrategy]] = {
    "ehma_pivot": EHMAPivotStrategy,
    "confluence_consumer": ConfluenceConsumerStrategy,
    "regime_v2": DailyRegimeStrategyV2,
    "regime_adaptive": RegimeAdaptiveStrategy,
    "premium_tp_sl": PremiumTpSlStrategy
}

def create_strategy(strategy_id: str, instance_id: str, config: Optional[Dict] = None) -> Optional[BaseStrategy]:
    """Factory method to create a strategy instance."""
    strat_class = STRATEGY_MAP.get(strategy_id)
    if not strat_class:
        return None
    return strat_class(instance_id, config)

def get_available_strategies() -> List[str]:
    """Returns list of registered strategy IDs."""
    return list(STRATEGY_MAP.keys())
