"""
Volatility-Based Signal Quality Filter

Filters signals based on market volatility regime:
- Rejects trades when volatility is too low (insufficient edge to overcome fees)
- Rejects trades when volatility is too high (wild, unpredictable moves)

Implements EWMA (Exponentially Weighted Moving Average) for volatility estimation.
Based on Financial Models repo (5.3 Volatility Tracking).
"""

import numpy as np
import pandas as pd
import logging
from typing import Dict, Any

from core.filters.base import BaseSignalFilter
from core.filters.models import FilterResult, FilterContext


logger = logging.getLogger(__name__)


class VolatilityRegimeFilter(BaseSignalFilter):
    """
    Volatility-based signal quality filter.

    Estimates current volatility using EWMA of returns, then filters signals based on:
    1. Minimum volatility threshold (must exceed fee impact)
    2. Maximum volatility threshold (skip extreme regimes)

    From MEMORY.md:
    - Fees eat 50-60% of gross edge (Rs 20 + 0.025% STT per leg)
    - Need sufficient volatility to overcome ~0.75% fee impact
    - Too-high volatility (>5%) indicates wild, risky conditions
    """

    def __init__(self, config: Dict[str, Any], filter_name: str = "volatility"):
        super().__init__(config, filter_name)

        # Config parameters
        self.min_volatility_bps = config.get('min_volatility_bps', 75)  # 0.75%
        self.max_volatility_bps = config.get('max_volatility_bps', 500)  # 5%
        self.ewma_alpha = config.get('ewma_alpha', 0.94)  # Decay factor (RiskMetrics standard)
        self.lookback_days = config.get('lookback_days', 20)

        # State
        self.current_volatility = None  # Will be updated per signal
        self.ewma_variance = None  # EWMA variance estimate

    def initialize(self, market_data: pd.DataFrame) -> None:
        """
        Initialize volatility estimator with historical returns.

        Args:
            market_data: Historical OHLCV data
        """
        if len(market_data) < 20:
            logger.warning(
                f"Insufficient data for initialization: {len(market_data)} < 20 bars"
            )

        # Calculate returns
        returns = market_data['close'].pct_change().dropna()

        if len(returns) < 10:
            logger.warning("Very few returns for volatility estimation")
            self.ewma_variance = 0.0001  # Fallback to small variance
        else:
            # Initialize EWMA variance with sample variance
            self.ewma_variance = returns.var()

            # Fit EWMA to historical returns
            for ret in returns:
                self._update_ewma(ret)

        # Calculate current volatility (annualized bps)
        self.current_volatility = self._variance_to_bps(self.ewma_variance)

        logger.info(
            f"[{self.filter_name}] Initialized. Current volatility: {self.current_volatility:.1f} bps "
            f"(threshold: {self.min_volatility_bps}-{self.max_volatility_bps} bps)"
        )

        self.is_initialized = True

    def evaluate(self, context: FilterContext) -> FilterResult:
        """
        Evaluate signal based on current volatility regime.

        Args:
            context: Signal and market context

        Returns:
            FilterResult with pass/fail decision
        """
        if not self.is_initialized:
            raise RuntimeError("Volatility filter not initialized. Call initialize() first.")

        # Calculate latest return and update EWMA
        recent_prices = context.recent_bars['close']
        if len(recent_prices) < 2:
            return self._create_result(
                passed=False,
                confidence=0.0,
                reason="Insufficient price data for volatility calculation"
            )

        latest_return = (recent_prices.iloc[-1] / recent_prices.iloc[-2]) - 1.0
        self._update_ewma(latest_return)
        self.current_volatility = self._variance_to_bps(self.ewma_variance)

        # Decision logic
        too_low = self.current_volatility < self.min_volatility_bps
        too_high = self.current_volatility > self.max_volatility_bps

        passed = not (too_low or too_high)

        # Confidence: normalized distance from bounds
        if too_low:
            confidence = self.current_volatility / self.min_volatility_bps
        elif too_high:
            confidence = 1.0 - ((self.current_volatility - self.max_volatility_bps) /
                               (self.max_volatility_bps * 0.5))
            confidence = max(0.0, confidence)
        else:
            # In acceptable range: confidence based on distance from edges
            mid_vol = (self.min_volatility_bps + self.max_volatility_bps) / 2
            distance_from_mid = abs(self.current_volatility - mid_vol)
            max_distance = (self.max_volatility_bps - self.min_volatility_bps) / 2
            confidence = 1.0 - (distance_from_mid / max_distance)

        # Reason
        if too_low:
            reason = (
                f"Volatility too low: {self.current_volatility:.1f} bps < "
                f"{self.min_volatility_bps} bps (insufficient edge over fees)"
            )
        elif too_high:
            reason = (
                f"Volatility too high: {self.current_volatility:.1f} bps > "
                f"{self.max_volatility_bps} bps (wild regime, skip)"
            )
        else:
            reason = (
                f"Volatility acceptable: {self.current_volatility:.1f} bps "
                f"(range: {self.min_volatility_bps}-{self.max_volatility_bps} bps)"
            )

        metadata = {
            "current_volatility_bps": float(self.current_volatility),
            "min_threshold_bps": float(self.min_volatility_bps),
            "max_threshold_bps": float(self.max_volatility_bps),
            "ewma_variance": float(self.ewma_variance),
            "latest_return": float(latest_return)
        }

        return self._create_result(
            passed=passed,
            confidence=confidence,
            reason=reason,
            metadata=metadata
        )

    def _update_ewma(self, return_value: float) -> None:
        """
        Update EWMA variance estimate with new return.

        Uses RiskMetrics EWMA formula:
        variance_t = alpha * variance_{t-1} + (1 - alpha) * return_t^2

        Args:
            return_value: Latest return
        """
        self.ewma_variance = (
            self.ewma_alpha * self.ewma_variance +
            (1 - self.ewma_alpha) * (return_value ** 2)
        )

    def _variance_to_bps(self, variance: float) -> float:
        """
        Convert variance to annualized volatility in basis points.

        Assumes data is at bar frequency (e.g., 15-min bars).
        Annualization factor depends on trading hours (6.25 hours/day, 252 days/year).

        Args:
            variance: Per-period variance

        Returns:
            Annualized volatility in basis points (1 bps = 0.01%)
        """
        # Volatility (std dev)
        vol = np.sqrt(variance)

        # Annualize (assuming 15-min bars: 25 bars/day, 252 days/year)
        # Adjust this if using different timeframe
        bars_per_day = 25  # 6.25 hours / 0.25 hours (15 min)
        bars_per_year = bars_per_day * 252
        vol_annualized = vol * np.sqrt(bars_per_year)

        # Convert to basis points (1 bps = 0.01%)
        vol_bps = vol_annualized * 10000

        return vol_bps


# Register the filter
from core.filters.registry import FilterRegistry
FilterRegistry.register("volatility", VolatilityRegimeFilter)
