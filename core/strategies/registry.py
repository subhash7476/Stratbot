"""
Strategy Registry
-----------------
Centralized factory for instantiating trading strategies.

Post-archive (Feb 2026): Only active/live strategies remain.
Archived strategies → archive/strategies_v1/
"""
from typing import Dict, Type, List, Optional
from core.strategies.base import BaseStrategy
from core.strategies.v9_pm_scalper import V9PmScalperStrategy

STRATEGY_MAP: Dict[str, Type[BaseStrategy]] = {
    "v9_pm_scalper": V9PmScalperStrategy,
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
