"""
Signal Quality Filter System
----------------------------
Modular pipeline for evaluating and filtering trading signals.
"""

from core.filters.base import BaseSignalFilter
from core.filters.models import FilterResult, FilterContext
from core.filters.pipeline import SignalQualityPipeline
from core.filters.registry import FilterRegistry

# Auto-register all concrete filters so they're available via FilterRegistry
import core.filters.kalman_filter          # noqa: F401
import core.filters.volatility_filter      # noqa: F401
import core.filters.ou_reversion_filter    # noqa: F401
import core.filters.gmm_regime_filter      # noqa: F401

__all__ = [
    "BaseSignalFilter",
    "FilterResult",
    "FilterContext",
    "SignalQualityPipeline",
    "FilterRegistry",
]
