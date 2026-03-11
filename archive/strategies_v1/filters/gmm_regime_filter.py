"""
GMM Regime Filter
-----------------
Uses sklearn GaussianMixture to classify market regime into:
  - TRENDING: directional moves, high returns magnitude
  - REVERTING: oscillating, low returns magnitude, high vol
  - CHAOTIC: high vol, no structure

Features: [rolling_return, rolling_volatility, volume_z_score]

Filter Logic:
  - TREND signals pass in TRENDING regime
  - REVERSION signals pass in REVERTING regime
  - Both rejected in CHAOTIC regime

GMM is re-fit on initialize() with the full history, then
regime is classified on each evaluate() call using recent bars.
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

# Import sklearn inside methods to handle cases where it's not available
try:
    from sklearn.mixture import GaussianMixture
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

from core.filters.base import BaseSignalFilter
from core.filters.models import FilterContext, FilterResult
from core.filters.registry import FilterRegistry


class GMMRegimeFilter(BaseSignalFilter):
    """Gaussian Mixture Model regime classification filter."""

    def __init__(self, config: Dict[str, Any], filter_name: str = "gmm_regime"):
        super().__init__(config, filter_name)
        self.n_regimes = config.get("n_regimes", 3)
        self.feature_window = config.get("feature_window", 20)
        self.vol_window = config.get("vol_window", 20)
        self._gmm: Optional[GaussianMixture] = None
        self._regime_map: Dict[int, str] = {}  # cluster_id -> regime_label

    def initialize(self, market_data: pd.DataFrame) -> None:
        """Fit GMM on full history."""
        if not SKLEARN_AVAILABLE:
            return
            
        features = self._compute_features(market_data)
        if features is None or len(features) < 100:
            return
        self._gmm = GaussianMixture(
            n_components=self.n_regimes,
            covariance_type='full',
            n_init=5,
            random_state=42,
        )
        self._gmm.fit(features)
        self._label_regimes(features)

    def evaluate(self, context: FilterContext) -> FilterResult:
        if not SKLEARN_AVAILABLE:
            return self._create_result(True, 0.5, "sklearn_not_available")
            
        if self._gmm is None:
            return self._create_result(True, 0.5, "gmm_not_fitted")

        features = self._compute_features(context.recent_bars)
        if features is None or len(features) == 0:
            return self._create_result(True, 0.5, "insufficient_features")

        # Classify most recent bar's regime
        latest_features = features[-1:].reshape(1, -1)
        regime_id = int(self._gmm.predict(latest_features)[0])
        regime_label = self._regime_map.get(regime_id, "UNKNOWN")
        proba = self._gmm.predict_proba(latest_features)[0]

        event_type = context.signal.metadata.get("event_type", "TREND")

        meta = {
            "regime": regime_label,
            "regime_id": regime_id,
            "regime_probabilities": {self._regime_map.get(i, f"R{i}"): round(float(p), 3) for i, p in enumerate(proba)},
            "event_type": event_type,
        }

        if regime_label == "CHAOTIC":
            return self._create_result(False, 0.2, "chaotic_regime", meta)
        elif regime_label == "TRENDING" and event_type == "TREND":
            return self._create_result(True, 0.8, "trend_in_trending", meta)
        elif regime_label == "REVERTING" and event_type == "REVERSION":
            return self._create_result(True, 0.8, "reversion_in_reverting", meta)
        elif regime_label == "TRENDING" and event_type == "REVERSION":
            return self._create_result(False, 0.3, "reversion_in_trending", meta)
        elif regime_label == "REVERTING" and event_type == "TREND":
            return self._create_result(False, 0.3, "trend_in_reverting", meta)
        else:
            return self._create_result(True, 0.5, "unknown_regime", meta)

    def _compute_features(self, df: pd.DataFrame) -> Optional[np.ndarray]:
        """Compute [rolling_return, rolling_volatility, volume_z] features."""
        if len(df) < self.feature_window + 10:
            return None
        close = df['close'].values
        volume = df['volume'].values if 'volume' in df.columns else np.ones(len(close))

        returns = np.diff(np.log(close))
        if len(returns) < self.feature_window:
            return None

        # Rolling features
        roll_ret = pd.Series(returns).rolling(self.feature_window).mean().values
        roll_vol = pd.Series(returns).rolling(self.vol_window).std().values
        vol_mean = pd.Series(volume[1:]).rolling(100, min_periods=20).mean().values
        vol_std = pd.Series(volume[1:]).rolling(100, min_periods=20).std().values
        vol_z = np.where(vol_std > 0, (volume[1:] - vol_mean) / vol_std, 0.0)

        # Stack and drop NaN rows
        features = np.column_stack([roll_ret, roll_vol, vol_z])
        valid_mask = ~np.isnan(features).any(axis=1)
        return features[valid_mask]

    def _label_regimes(self, features: np.ndarray):
        """Label clusters as TRENDING, REVERTING, CHAOTIC based on feature means."""
        if not SKLEARN_AVAILABLE or self._gmm is None:
            return
            
        labels = self._gmm.predict(features)

        cluster_stats = []
        for i in range(self.n_regimes):
            mask = labels == i
            if mask.sum() == 0:
                cluster_stats.append((i, 0.0, 0.0))
                continue
            ret_mag = float(np.mean(np.abs(features[mask, 0])))
            vol = float(np.mean(features[mask, 1]))
            cluster_stats.append((i, ret_mag, vol))

        # Sort: highest return magnitude with lowest vol = TRENDING
        #        lowest return magnitude = REVERTING
        #        highest vol = CHAOTIC
        sorted_by_vol = sorted(cluster_stats, key=lambda x: x[2], reverse=True)
        sorted_by_ret = sorted(cluster_stats, key=lambda x: x[1], reverse=True)

        # Highest vol cluster = CHAOTIC
        chaotic_id = sorted_by_vol[0][0]
        # Highest directional return (excluding chaotic) = TRENDING
        trending_id = None
        for cid, ret, vol in sorted_by_ret:
            if cid != chaotic_id:
                trending_id = cid
                break
        # Remaining = REVERTING
        reverting_ids = [c[0] for c in cluster_stats if c[0] not in (chaotic_id, trending_id)]

        self._regime_map[chaotic_id] = "CHAOTIC"
        if trending_id is not None:
            self._regime_map[trending_id] = "TRENDING"
        for rid in reverting_ids:
            self._regime_map[rid] = "REVERTING"


FilterRegistry.register("gmm_regime", GMMRegimeFilter)