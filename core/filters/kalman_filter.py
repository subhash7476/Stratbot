"""
Kalman Filter for Signal Quality Assessment

Uses a constant-velocity Kalman filter to track price level and velocity (trend).
Filters signals based on:
1. Signal-to-noise ratio (Kalman state confidence)
2. Trend alignment (Kalman velocity direction matches signal direction)
"""

import numpy as np
import pandas as pd
import logging
from typing import Dict, Any

from core.filters.base import BaseSignalFilter
from core.filters.models import FilterResult, FilterContext
from core.events import SignalType


logger = logging.getLogger(__name__)


class KalmanSignalFilter(BaseSignalFilter):
    """
    Kalman filter-based signal quality assessment.

    Tracks price using a 2-state constant velocity model:
    - State[0]: Price level
    - State[1]: Price velocity (trend direction, momentum)

    Signals pass if:
    1. Signal/noise ratio > threshold (strong trend)
    2. Kalman velocity aligns with signal direction (if alignment required)
    """

    def __init__(self, config: Dict[str, Any], filter_name: str = "kalman"):
        super().__init__(config, filter_name)

        # Config parameters
        self.lookback_periods = config.get('lookback_periods', 50)
        self.min_signal_noise_ratio = config.get('min_signal_noise_ratio', 2.0)
        self.trend_alignment_required = config.get('trend_alignment_required', True)

        # Kalman filter parameters
        self.process_variance = config.get('process_variance', 0.01)
        self.measurement_variance = config.get('measurement_variance', 0.1)

        # State (will be initialized)
        self.x = None  # State vector [price, velocity]
        self.P = None  # Covariance matrix
        self.F = None  # State transition matrix
        self.H = None  # Measurement matrix
        self.Q = None  # Process noise
        self.R = None  # Measurement noise

    def initialize(self, market_data: pd.DataFrame) -> None:
        """
        Initialize Kalman filter state with historical prices.

        Args:
            market_data: Historical OHLCV data
        """
        if len(market_data) < self.lookback_periods:
            logger.warning(
                f"Insufficient data for initialization: {len(market_data)} < {self.lookback_periods}"
            )

        # Use closing prices
        prices = market_data['close'].values[-self.lookback_periods:]

        # Initialize state: [price, velocity]
        initial_price = prices[0]
        initial_velocity = 0.0  # Start with no trend assumption

        self.x = np.array([[initial_price], [initial_velocity]])

        # Initialize covariance (high uncertainty initially)
        self.P = np.array([
            [1.0, 0.0],
            [0.0, 1.0]
        ])

        # State transition matrix (constant velocity model)
        # x_t = F * x_{t-1}
        # price_t = price_{t-1} + velocity_{t-1}
        # velocity_t = velocity_{t-1}
        self.F = np.array([
            [1.0, 1.0],  # price = prev_price + prev_velocity
            [0.0, 1.0]   # velocity = prev_velocity
        ])

        # Measurement matrix (we only observe price)
        self.H = np.array([[1.0, 0.0]])

        # Process noise covariance
        self.Q = np.array([
            [self.process_variance, 0.0],
            [0.0, self.process_variance]
        ])

        # Measurement noise covariance
        self.R = np.array([[self.measurement_variance]])

        # Fit filter to historical data
        for price in prices[1:]:
            self._update(price)

        logger.info(
            f"[{self.filter_name}] Initialized with {len(prices)} bars. "
            f"Final state: price={self.x[0,0]:.2f}, velocity={self.x[1,0]:.4f}"
        )

        self.is_initialized = True

    def evaluate(self, context: FilterContext) -> FilterResult:
        """
        Evaluate signal quality using Kalman filter state.

        Args:
            context: Signal and market context

        Returns:
            FilterResult with pass/fail decision
        """
        if not self.is_initialized:
            raise RuntimeError("Kalman filter not initialized. Call initialize() first.")

        signal = context.signal
        current_price = context.current_price

        # Update Kalman state with latest price
        self._update(current_price)

        # Extract state
        kalman_price = self.x[0, 0]
        kalman_velocity = self.x[1, 0]
        price_variance = self.P[0, 0]
        velocity_variance = self.P[1, 1]

        # Calculate signal-to-noise ratio
        # Higher ratio = more confident trend
        if velocity_variance > 0:
            signal_noise_ratio = abs(kalman_velocity) / np.sqrt(velocity_variance)
        else:
            signal_noise_ratio = 0.0

        # Check trend alignment
        signal_is_long = signal.signal_type in [SignalType.BUY]
        signal_is_short = signal.signal_type in [SignalType.SELL]

        kalman_trend_is_up = kalman_velocity > 0
        kalman_trend_is_down = kalman_velocity < 0

        trend_aligned = True
        if self.trend_alignment_required:
            if signal_is_long and not kalman_trend_is_up:
                trend_aligned = False
            elif signal_is_short and not kalman_trend_is_down:
                trend_aligned = False

        # Decision logic
        passed = signal_noise_ratio >= self.min_signal_noise_ratio and trend_aligned

        # Confidence (normalized signal/noise ratio, capped at 1.0)
        confidence = min(signal_noise_ratio / (self.min_signal_noise_ratio * 2), 1.0)

        # Reason
        if not trend_aligned:
            reason = (
                f"Trend misalignment: signal={signal.signal_type.value}, "
                f"kalman_velocity={kalman_velocity:+.4f}"
            )
        elif signal_noise_ratio < self.min_signal_noise_ratio:
            reason = (
                f"Weak signal: S/N={signal_noise_ratio:.2f} < {self.min_signal_noise_ratio}"
            )
        else:
            reason = (
                f"Strong aligned signal: S/N={signal_noise_ratio:.2f}, "
                f"velocity={kalman_velocity:+.4f}"
            )

        metadata = {
            "kalman_price": float(kalman_price),
            "kalman_velocity": float(kalman_velocity),
            "price_variance": float(price_variance),
            "velocity_variance": float(velocity_variance),
            "signal_noise_ratio": float(signal_noise_ratio),
            "trend_aligned": trend_aligned,
            "current_price": float(current_price)
        }

        return self._create_result(
            passed=passed,
            confidence=confidence,
            reason=reason,
            metadata=metadata
        )

    def _update(self, measurement: float) -> None:
        """
        Kalman filter update step (predict + correct).

        Args:
            measurement: Observed price
        """
        # Predict step
        x_pred = self.F @ self.x
        P_pred = self.F @ self.P @ self.F.T + self.Q

        # Update step
        z = np.array([[measurement]])
        y = z - self.H @ x_pred  # Innovation (measurement residual)
        S = self.H @ P_pred @ self.H.T + self.R  # Innovation covariance
        K = P_pred @ self.H.T @ np.linalg.inv(S)  # Kalman gain

        # Update state and covariance
        self.x = x_pred + K @ y
        self.P = (np.eye(2) - K @ self.H) @ P_pred


# Register the filter
from core.filters.registry import FilterRegistry
FilterRegistry.register("kalman", KalmanSignalFilter)
