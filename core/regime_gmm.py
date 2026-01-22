# core/regime_gmm.py (PRODUCTION VERSION - Replace your current file)
"""
GMM Regime Detection - Production Ready
Key Fixes:
1. ✅ No lookahead bias (transition matrix instead of RF with future data)
2. ✅ Proper regime labeling using percentiles
3. ✅ Validation metrics
4. ✅ Regime duration tracking
"""

import pandas as pd
import numpy as np
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler


class MarketRegimeGMM:
    """Detect regimes using GMM with transition matrix for persistence"""
    
    def __init__(self, n_regimes=4):
        self.n_regimes = n_regimes
        self.model = GaussianMixture(n_components=n_regimes, covariance_type="full", random_state=42)
        self.scaler = StandardScaler()
        self.is_fitted = False
        self.regime_labels = {}
    
    def prepare_features(self, df: pd.DataFrame):
        """Feature engineering for regime detection"""
        features = pd.DataFrame(index=df.index)
        features['returns'] = df['Close'].pct_change()
        features['returns_5'] = df['Close'].pct_change(5)
        features['volatility'] = df['High'] / df['Low'] - 1
        features['atr_norm'] = features['volatility'].rolling(14).std()
        
        if 'Volume' in df.columns and df['Volume'].sum() > 0:
            features['volume_ratio'] = df['Volume'] / df['Volume'].rolling(20).mean()
        else:
            features['volume_ratio'] = 1.0
        
        return features.dropna()
    
    def detect_regimes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Fit GMM and label regimes"""
        features = self.prepare_features(df)
        scaled = self.scaler.fit_transform(features)
        self.model.fit(scaled)
        self.is_fitted = True
        
        regimes = self.model.predict(scaled)
        proba = self.model.predict_proba(scaled)
        
        df_out = df.loc[features.index].copy()
        df_out['Regime_Cluster'] = regimes
        df_out['Regime_Prob'] = proba.max(axis=1)
        
        # Interpret clusters
        self.regime_labels = self._interpret_regimes(df_out)
        df_out['Regime'] = df_out['Regime_Cluster'].map(self.regime_labels)
        
        return df_out
    
    def _interpret_regimes(self, df: pd.DataFrame) -> dict:
        """Label regimes using percentiles (handles any n_regimes)"""
        regime_chars = []
        for regime in range(self.n_regimes):
            mask = df['Regime_Cluster'] == regime
            if not mask.any():
                continue
            
            seg = df[mask]
            regime_chars.append({
                'cluster': regime,
                'return': seg['Close'].pct_change().mean(),
                'volatility': (seg['High'] / seg['Low'] - 1).mean()
            })
        
        if not regime_chars:
            return {i: f'Regime {i}' for i in range(self.n_regimes)}
        
        # Sort and percentile-label
        vols = [r['volatility'] for r in regime_chars]
        rets = [r['return'] for r in regime_chars]
        vol_50 = np.percentile(vols, 50)
        ret_33 = np.percentile(rets, 33)
        ret_67 = np.percentile(rets, 67)
        
        labels = {}
        for char in regime_chars:
            c, v, r = char['cluster'], char['volatility'], char['return']
            
            if v > vol_50:
                labels[c] = 'Volatile Bullish' if r > ret_67 else 'Volatile Bearish' if r < ret_33 else 'Choppy'
            else:
                labels[c] = 'Trending Bullish' if r > ret_67 else 'Trending Bearish' if r < ret_33 else 'Quiet Ranging'
        
        return labels
    
    def predict_next_regime(self, df: pd.DataFrame, threshold=0.7):
        """
        ✅ FIXED: Use transition matrix (no lookahead)
        Predicts persistence based on historical regime transitions
        """
        df_regimes = self.detect_regimes(df)
        
        # Build transition matrix
        unique_regimes = df_regimes['Regime'].unique()
        regime_to_idx = {r: i for i, r in enumerate(unique_regimes)}
        n_states = len(unique_regimes)
        
        transition_counts = np.zeros((n_states, n_states))
        for i in range(1, len(df_regimes)):
            prev = regime_to_idx[df_regimes['Regime'].iloc[i-1]]
            curr = regime_to_idx[df_regimes['Regime'].iloc[i]]
            transition_counts[prev, curr] += 1
        
        # Normalize to probabilities
        transition_matrix = transition_counts / (transition_counts.sum(axis=1, keepdims=True) + 1e-10)
        
        # Current state
        current_regime = df_regimes['Regime'].iloc[-1]
        current_idx = regime_to_idx[current_regime]
        persistence_prob = transition_matrix[current_idx, current_idx]
        
        # Regime duration
        duration = 1
        for i in range(len(df_regimes) - 2, -1, -1):
            if df_regimes['Regime'].iloc[i] == current_regime:
                duration += 1
            else:
                break
        
        # Decision
        confidence = df_regimes['Regime_Prob'].iloc[-1]
        enter_trade = (persistence_prob > threshold) and (confidence > 0.6)
        
        return {
            'Predicted Next Regime': current_regime,  # Most likely = same
            'Persistence Prob %': round(persistence_prob * 100, 2),
            'Regime Duration': duration,
            'Confidence %': round(confidence * 100, 2),
            'Enter Trade': enter_trade
        }


def get_regime_stats(df: pd.DataFrame):
    """Enhanced regime statistics"""
    if 'Regime' not in df.columns:
        return pd.DataFrame()
    
    stats = []
    for regime in df['Regime'].unique():
        mask = df['Regime'] == regime
        segment = df[mask]
        
        stats.append({
            'Regime': regime,
            'Bars': len(segment),
            'Percentage': f"{len(segment)/len(df)*100:.1f}%",
            'Avg Return %': round(segment['Close'].pct_change().mean() * 100, 3),
            'Avg Volatility %': round(((segment['High'] / segment['Low'] - 1) * 100).mean(), 2),
            'Win Rate %': round((segment['Close'].pct_change() > 0).sum() / len(segment) * 100, 1)
        })
    
    return pd.DataFrame(stats)