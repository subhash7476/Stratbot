"""
Data models for signal quality filtering.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import pandas as pd
from core.events import SignalEvent


@dataclass
class FilterResult:
    """Result of a signal quality filter evaluation."""

    passed: bool
    """Whether the signal passed the filter (True = accept, False = reject)"""

    confidence: float
    """Confidence score from 0.0 to 1.0"""

    reason: str
    """Human-readable explanation of why the signal passed/failed"""

    filter_name: str
    """Name of the filter that produced this result"""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Filter-specific diagnostic information"""

    def __post_init__(self):
        """Validate fields."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0 and 1, got {self.confidence}")


@dataclass
class FilterContext:
    """
    Context information provided to filters for evaluation.
    Contains market data, state, and history needed for signal quality assessment.
    """

    signal: SignalEvent
    """The trading signal being evaluated"""

    symbol: str
    """Trading symbol (e.g., NSE_EQ|INE155A01022)"""

    current_price: float
    """Latest traded price (LTP)"""

    recent_bars: pd.DataFrame
    """Recent OHLCV bars for indicator calculation (typically 100-200 bars)"""

    timestamp: pd.Timestamp
    """Current timestamp (bar time)"""

    market_state: Optional[Dict[str, Any]] = None
    """Optional regime/state data (e.g., volatility regime, trend direction)"""

    additional_data: Dict[str, Any] = field(default_factory=dict)
    """Any additional context data filters may need"""

    def __post_init__(self):
        """Validate required fields."""
        if self.recent_bars is None or self.recent_bars.empty:
            raise ValueError("recent_bars cannot be empty")

        required_cols = ['open', 'high', 'low', 'close', 'volume']
        missing = [col for col in required_cols if col not in self.recent_bars.columns]
        if missing:
            raise ValueError(f"recent_bars missing required columns: {missing}")
