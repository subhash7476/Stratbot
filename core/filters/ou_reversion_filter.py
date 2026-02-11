"""
OU Reversion Filter
-------------------
Estimates the Ornstein-Uhlenbeck process parameters (theta, mu, sigma)
from recent price data via OLS regression on:
    dX(t) = theta * (mu - X(t)) * dt + sigma * dW(t)

Discrete approximation (OLS on log prices):
    X(t+1) - X(t) = a + b * X(t) + residual
    theta = -b / dt
    mu = -a / b
    sigma = std(residuals) / sqrt(dt)

Filter Logic:
- REVERSION signals: pass if theta > threshold (strong mean reversion)
                      AND price is far from mu (|price - mu| > 0.5 * sigma)
- TREND signals: pass if theta < threshold (weak reversion, market trending)
                  OR price is near mu (not in reverting zone)

This avoids the Kalman filter's fatal flaw (regime-dependency) by directly
measuring reversion strength rather than trend direction.
"""

import numpy as np
import pandas as pd
from typing import Dict, Any

from core.filters.base import BaseSignalFilter
from core.filters.models import FilterContext, FilterResult
from core.filters.registry import FilterRegistry


class OUReversionFilter(BaseSignalFilter):
    """Ornstein-Uhlenbeck mean reversion strength filter."""

    def __init__(self, config: Dict[str, Any], filter_name: str = "ou_reversion"):
        super().__init__(config, filter_name)
        self.min_theta = config.get("min_mean_reversion_speed", 0.5)
        self.estimation_window = config.get("estimation_window_bars", 200)
        self.distance_threshold_sigma = config.get("distance_threshold_sigma", 0.5)
        # State
        self._theta = None
        self._mu = None
        self._sigma = None

    def initialize(self, market_data: pd.DataFrame) -> None:
        """Pre-estimate OU parameters from full history."""
        if len(market_data) < self.estimation_window:
            return
        self._estimate_ou(market_data['close'].values[-self.estimation_window:])

    def evaluate(self, context: FilterContext) -> FilterResult:
        """Evaluate signal based on OU reversion regime."""
        # Re-estimate on recent bars for freshness
        prices = context.recent_bars['close'].values
        if len(prices) < 50:
            return self._create_result(True, 0.5, "insufficient_data", {"theta": None})

        self._estimate_ou(prices)

        if self._theta is None:
            return self._create_result(True, 0.5, "estimation_failed")

        event_type = context.signal.metadata.get("event_type", "TREND")
        current_price = context.current_price
        distance_from_mu = abs(current_price - self._mu) / max(self._sigma, 1e-8)

        meta = {
            "theta": round(self._theta, 4),
            "mu": round(self._mu, 2),
            "sigma": round(self._sigma, 4),
            "distance_sigma": round(distance_from_mu, 2),
            "event_type": event_type,
        }

        if event_type == "REVERSION":
            # Reversion signals need: strong reversion (high theta) + price far from mean
            strong_reversion = self._theta > self.min_theta
            far_from_mean = distance_from_mu > self.distance_threshold_sigma
            passed = strong_reversion and far_from_mean
            conf = min(self._theta / (self.min_theta * 2), 1.0) if passed else 0.3
            reason = "reversion_confirmed" if passed else f"weak_reversion(theta={self._theta:.2f})"
        else:
            # Trend signals need: weak reversion (low theta) = market is trending, not reverting
            weak_reversion = self._theta < self.min_theta
            passed = weak_reversion
            conf = 0.7 if passed else 0.3
            reason = "trend_confirmed" if passed else f"strong_reversion(theta={self._theta:.2f})"

        return self._create_result(passed, conf, reason, meta)

    def _estimate_ou(self, prices: np.ndarray):
        """Estimate OU parameters via OLS."""
        try:
            log_prices = np.log(prices)
            dx = np.diff(log_prices)
            x = log_prices[:-1]
            # OLS: dx = a + b * x
            A = np.column_stack([np.ones_like(x), x])
            result = np.linalg.lstsq(A, dx, rcond=None)
            a, b = result[0]
            residuals = dx - A @ result[0]

            if b >= 0:  # No mean reversion (divergent)
                self._theta = 0.0
                self._mu = prices[-1]
                self._sigma = float(np.std(residuals))
            else:
                self._theta = -b  # Mean reversion speed (positive)
                self._mu = float(np.exp(-a / b))  # Long-run mean (price space)
                self._sigma = float(np.std(residuals))
        except Exception:
            self._theta = None
            self._mu = None
            self._sigma = None


FilterRegistry.register("ou_reversion", OUReversionFilter)