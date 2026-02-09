"""
Signal Quality Filter System
----------------------------
Modular pipeline for evaluating and filtering trading signals.
"""

from core.filters.base import BaseSignalFilter
from core.filters.models import FilterResult, FilterContext
from core.filters.pipeline import SignalQualityPipeline
from core.filters.registry import FilterRegistry

__all__ = [
    "BaseSignalFilter",
    "FilterResult",
    "FilterContext",
    "SignalQualityPipeline",
    "FilterRegistry",
]
