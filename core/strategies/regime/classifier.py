"""
Layer 2: CLASSIFIER — HMM regime detection with probabilistic output.

Uses a Gaussian HMM on daily intermarket features to classify market regime.
States are mapped to semantic labels via rank-based scoring (not position-based).
Supports override rules for extreme conditions.
"""
import numpy as np
import pandas as pd
import joblib
from enum import Enum
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Optional
from sklearn.preprocessing import StandardScaler
from hmmlearn.hmm import GaussianHMM


class RegimeState(Enum):
    EXPANSION = "EXPANSION"
    CONTRACTION = "CONTRACTION"
    SHOCK = "SHOCK"


@dataclass
class RegimeClassification:
    date: date
    state: RegimeState
    probabilities: Dict[RegimeState, float]
    entropy: float  # State entropy: high = uncertain
    is_override: bool = False


class HMMRegimeClassifier:
    """
    Gaussian HMM for daily regime classification.

    Training: fit() on standardized daily features.
    Prediction: predict_proba() returns state probabilities per day.
    State mapping: rank-based scoring on emission means.
    """

    # Feature indices for state mapping
    VIX_IDX = 0        # vix_level
    VIX_PCTL_IDX = 1   # vix_pctl_90d
    VIX_ROC_IDX = 2     # vix_roc_5d
    RATIO_IDX = 3       # banknifty_nifty_ratio
    SLOPE_IDX = 4       # nifty_20dma_slope
    VOL_IDX = 5         # realized_vol_10d

    def __init__(self, n_states: int = 3, config: Optional[dict] = None):
        cfg = config or {}
        self.n_states = n_states
        self.model: Optional[GaussianHMM] = None
        self.scaler: Optional[StandardScaler] = None
        self.state_map: Dict[int, RegimeState] = {}

        # HMM parameters
        self.covariance_type = cfg.get('covariance_type', 'full')
        self.n_iter = cfg.get('n_iter', 200)
        self.random_state = cfg.get('random_state', 42)

        # Override thresholds
        self.override_vix_pctl = cfg.get('override_vix_pctl_threshold', 0.85)
        self.override_vix_roc = cfg.get('override_vix_roc_threshold', 0.20)

    def fit(self, features: pd.DataFrame) -> 'HMMRegimeClassifier':
        """
        Train HMM on historical features.

        Args:
            features: DataFrame with HMM feature columns (6 features from Observer.get_hmm_features())

        Returns:
            self (for chaining)
        """
        X = features.values

        # Standardize features (HMM is sensitive to scale)
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        # Fit Gaussian HMM
        self.model = GaussianHMM(
            n_components=self.n_states,
            covariance_type=self.covariance_type,
            n_iter=self.n_iter,
            random_state=self.random_state,
            tol=1e-4,
        )

        try:
            self.model.fit(X_scaled)
        except ValueError:
            # Fallback to diagonal covariance if full fails
            self.model = GaussianHMM(
                n_components=self.n_states,
                covariance_type='diag',
                n_iter=self.n_iter,
                random_state=self.random_state,
                tol=1e-4,
            )
            self.model.fit(X_scaled)

        # Map states to semantic labels
        self._map_states()

        return self

    def predict_proba(self, features: pd.DataFrame) -> pd.DataFrame:
        """
        Return state probabilities for each day.

        Returns DataFrame indexed like input with columns:
            P(EXPANSION), P(CONTRACTION), P(SHOCK), entropy
        """
        if self.model is None or self.scaler is None:
            raise RuntimeError("Model not fitted. Call fit() first.")

        X_scaled = self.scaler.transform(features.values)
        _, posteriors = self.model.score_samples(X_scaled)

        # Map raw state indices to semantic labels
        result = pd.DataFrame(index=features.index)
        for raw_idx, regime_state in self.state_map.items():
            result[f'P({regime_state.value})'] = posteriors[:, raw_idx]

        # Compute entropy: H = -sum(p * log(p))
        eps = 1e-10
        result['entropy'] = -(posteriors * np.log(posteriors + eps)).sum(axis=1)

        return result

    def classify(self, features_row: pd.Series, dt: date,
                 prev_features_row: Optional[pd.Series] = None) -> RegimeClassification:
        """
        Classify a single day with override logic.

        Args:
            features_row: single row of HMM features (6 values)
            dt: the date being classified
            prev_features_row: previous day's raw features (for override, optional)
        """
        if self.model is None or self.scaler is None:
            raise RuntimeError("Model not fitted. Call fit() first.")

        # Check override first (uses raw, unscaled features)
        override = self._check_override(features_row)
        if override is not None:
            probs = {s: 0.0 for s in RegimeState}
            probs[override] = 1.0
            return RegimeClassification(
                date=dt, state=override, probabilities=probs,
                entropy=0.0, is_override=True
            )

        # HMM prediction
        X = features_row.values.reshape(1, -1)
        X_scaled = self.scaler.transform(X)
        _, posteriors = self.model.score_samples(X_scaled)
        probs_raw = posteriors[0]

        # Map to semantic labels
        probs = {}
        for raw_idx, regime_state in self.state_map.items():
            probs[regime_state] = float(probs_raw[raw_idx])

        # Best state
        best_state = max(probs, key=probs.get)

        # Entropy
        eps = 1e-10
        entropy = float(-(probs_raw * np.log(probs_raw + eps)).sum())

        return RegimeClassification(
            date=dt, state=best_state, probabilities=probs,
            entropy=entropy, is_override=False
        )

    def classify_all(self, features: pd.DataFrame,
                     raw_features: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """
        Classify all days in a feature DataFrame.
        Returns DataFrame with probabilities + regime labels.
        """
        proba_df = self.predict_proba(features)

        # Apply overrides if raw features provided
        if raw_features is not None:
            for i, (idx, row) in enumerate(raw_features.iterrows()):
                override = self._check_override(row)
                if override is not None:
                    for state in RegimeState:
                        col = f'P({state.value})'
                        if col in proba_df.columns:
                            proba_df.loc[idx, col] = 1.0 if state == override else 0.0
                    proba_df.loc[idx, 'entropy'] = 0.0

        # Add discrete state label (highest probability)
        state_cols = [f'P({s.value})' for s in RegimeState]
        proba_df['regime'] = proba_df[state_cols].idxmax(axis=1).str.extract(r'P\((\w+)\)')[0]

        return proba_df

    def _map_states(self):
        """
        Map HMM hidden states to semantic labels using rank-based scoring.
        More robust than assuming which state has highest/lowest VIX.
        """
        means = self.model.means_  # shape: (n_states, n_features)
        n = self.n_states

        # Rank each state on each feature (0 = lowest, n-1 = highest)
        def rank_col(col_idx):
            vals = means[:, col_idx]
            return np.argsort(np.argsort(vals))  # Double argsort = rank

        vix_rank = rank_col(self.VIX_IDX)
        vol_rank = rank_col(self.VOL_IDX)
        slope_rank = rank_col(self.SLOPE_IDX)

        # Composite scores
        shock_score = vix_rank + vol_rank           # High VIX + high vol = shock
        expansion_score = (n - 1 - vix_rank) + slope_rank  # Low VIX + positive slope = expansion

        # Assign: highest shock_score → SHOCK
        shock_state = int(np.argmax(shock_score))

        # Among remaining: highest expansion_score → EXPANSION
        remaining = [i for i in range(n) if i != shock_state]
        expansion_state = remaining[int(np.argmax([expansion_score[i] for i in remaining]))]

        # Last one → CONTRACTION
        contraction_state = [i for i in range(n) if i not in (shock_state, expansion_state)][0]

        self.state_map = {
            shock_state: RegimeState.SHOCK,
            expansion_state: RegimeState.EXPANSION,
            contraction_state: RegimeState.CONTRACTION,
        }

    def _check_override(self, features_row: pd.Series) -> Optional[RegimeState]:
        """Override HMM when conditions are extreme (uses raw, unscaled features)."""
        vix_pctl = features_row.get('vix_pctl_90d', features_row.iloc[self.VIX_PCTL_IDX]
                                     if len(features_row) > self.VIX_PCTL_IDX else 0)
        vix_roc = features_row.get('vix_roc_5d', features_row.iloc[self.VIX_ROC_IDX]
                                    if len(features_row) > self.VIX_ROC_IDX else 0)

        # Force SHOCK on VIX spike (>20% in 5 days)
        if vix_roc > self.override_vix_roc:
            return RegimeState.SHOCK

        # Force SHOCK if VIX is at extreme percentile
        if vix_pctl > self.override_vix_pctl:
            return RegimeState.SHOCK

        return None

    def save(self, path: str) -> None:
        """Serialize model + scaler + state_map to disk."""
        joblib.dump({
            'model': self.model,
            'scaler': self.scaler,
            'state_map': self.state_map,
            'n_states': self.n_states,
        }, path)

    def load(self, path: str) -> 'HMMRegimeClassifier':
        """Load model from disk."""
        data = joblib.load(path)
        self.model = data['model']
        self.scaler = data['scaler']
        self.state_map = data['state_map']
        self.n_states = data['n_states']
        return self
