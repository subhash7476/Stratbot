"""
Base class for all signal quality filters.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
import pandas as pd
import logging

from core.filters.models import FilterResult, FilterContext


logger = logging.getLogger(__name__)


class BaseSignalFilter(ABC):
    """
    Base class for all signal quality filters.

    All filters must implement:
    - initialize(): Set up filter state (fit models, estimate parameters)
    - evaluate(): Assess a single signal and return pass/fail decision

    Filters are stateful and should be initialized once with historical data,
    then called repeatedly with new signals.
    """

    def __init__(self, config: Dict[str, Any], filter_name: str):
        """
        Initialize the filter.

        Args:
            config: Filter-specific configuration parameters
            filter_name: Unique name for this filter instance
        """
        self.config = config
        self.filter_name = filter_name
        self.enabled = config.get('enabled', True)
        self.weight = config.get('weight', 1.0)
        self.is_initialized = False

        logger.info(f"[{self.filter_name}] Filter created with config: {config}")

    @abstractmethod
    def initialize(self, market_data: pd.DataFrame) -> None:
        """
        Initialize filter state with historical data.

        Called once before the filter starts evaluating signals.
        Use this to:
        - Fit models (e.g., Kalman filter state)
        - Estimate parameters (e.g., OU theta, sigma)
        - Compute baseline statistics

        Args:
            market_data: Historical OHLCV bars for initialization
                        Should have columns: [open, high, low, close, volume, timestamp]
        """
        pass

    @abstractmethod
    def evaluate(self, context: FilterContext) -> FilterResult:
        """
        Evaluate a single signal for quality.

        Args:
            context: All information needed to assess the signal
                    (signal itself, recent bars, market state, etc.)

        Returns:
            FilterResult with pass/fail decision, confidence, and reasoning
        """
        pass

    def _create_result(
        self,
        passed: bool,
        confidence: float,
        reason: str,
        metadata: Dict[str, Any] = None
    ) -> FilterResult:
        """
        Helper to create a FilterResult with consistent naming.

        Args:
            passed: Whether signal passed the filter
            confidence: Confidence score (0-1)
            reason: Human-readable explanation
            metadata: Optional diagnostic data

        Returns:
            FilterResult instance
        """
        return FilterResult(
            passed=passed,
            confidence=confidence,
            reason=reason,
            filter_name=self.filter_name,
            metadata=metadata or {}
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.filter_name}, enabled={self.enabled})"
