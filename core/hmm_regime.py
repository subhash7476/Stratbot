"""
HMM-based Market Regime Detection for Trading
Filters Supertrend signals based on detected market regimes
"""

import pandas as pd
import numpy as np
from hmmlearn import hmm
from sklearn.preprocessing import StandardScaler


class MarketRegimeHMM:
    """Detects 4 market regimes using Hidden Markov Model"""
    
    def __init__(self, n_regimes=4, n_iter=100):
        self.n_regimes = n_regimes
        self.model = hmm.GaussianHMM(
            n_components=n_regimes,
            covariance_type="full",
            n_iter=n_iter,
            random_state=42
        )
        self.scaler = StandardScaler()
        self.is_fitted = False
    
    def prepare_features(self, df: pd.DataFrame):
        """Extract features for regime detection"""
        features = pd.DataFrame(index=df.index)
        
        # Returns (momentum)
        features['returns'] = df['Close'].pct_change()
        features['returns_5'] = df['Close'].pct_change(5)
        features['returns_20'] = df['Close'].pct_change(20)
        
        # Volatility
        features['volatility'] = df['High'] / df['Low'] - 1
        if 'ATR' in df.columns:
            features['atr_norm'] = df['ATR'] / df['Close']
        else:
            features['atr_norm'] = features['volatility'].rolling(14).std()
        
        # Volume (if available)
        if 'Volume' in df.columns:
            features['volume_change'] = df['Volume'].pct_change()
        else:
            features['volume_change'] = 0
        
        # Price position vs MA
        features['price_ma20'] = (df['Close'] - df['Close'].rolling(20).mean()) / df['Close'].rolling(20).std()
        features['price_ma50'] = (df['Close'] - df['Close'].rolling(50).mean()) / df['Close'].rolling(50).std()
        
        # Range
        features['high_low_range'] = (df['High'] - df['Low']) / df['Close']
        
        # Fill NaN
        features = features.fillna(method='bfill').fillna(0)
        
        return features
    
    def fit(self, df: pd.DataFrame):
        """Train HMM on historical data"""
        features = self.prepare_features(df)
        X = self.scaler.fit_transform(features.values)
        self.model.fit(X)
        self.is_fitted = True
        return self
    
    def predict(self, df: pd.DataFrame):
        """Predict regime for each candle"""
        if not self.is_fitted:
            raise ValueError("Model not fitted. Call fit() first.")
        
        features = self.prepare_features(df)
        X = self.scaler.transform(features.values)
        states = self.model.predict(X)
        probs = self.model.predict_proba(X)
        
        return states, probs
    
    def label_regimes(self, df: pd.DataFrame, states: np.ndarray):
        """Map numeric states to regime names"""
        df = df.copy()
        df['HMM_State'] = states
        
        # Calculate characteristics per state
        state_chars = []
        for state in range(self.n_regimes):
            mask = df['HMM_State'] == state
            if mask.sum() == 0:
                continue
            
            avg_return = df.loc[mask, 'Close'].pct_change().mean()
            avg_volatility = (df.loc[mask, 'High'] / df.loc[mask, 'Low'] - 1).mean()
            
            state_chars.append({
                'state': state,
                'avg_return': avg_return,
                'avg_volatility': avg_volatility
            })
        
        # Sort by volatility and return
        state_chars = sorted(state_chars, key=lambda x: (x['avg_volatility'], x['avg_return']))
        
        # Map to regime names
        regime_map = {}
        if len(state_chars) >= 4:
            regime_map[state_chars[0]['state']] = 'ranging'
            regime_map[state_chars[1]['state']] = 'trending_up'
            regime_map[state_chars[2]['state']] = 'trending_down'
            regime_map[state_chars[3]['state']] = 'volatile'
        else:
            # Fallback if not enough states
            for i, sc in enumerate(state_chars):
                regime_map[sc['state']] = f'state_{i}'
        
        df['Regime'] = df['HMM_State'].map(regime_map).fillna('unknown')
        
        return df, regime_map


def supertrend_with_hmm(df: pd.DataFrame, 
                        period: int = 10, 
                        mult: float = 3.0,
                        min_confidence: float = 0.6):
    """
    Supertrend with HMM regime filtering.
    Blocks signals in unfavorable regimes.
    """
    from backtest.indicators import supertrend
    
    # Calculate base supertrend
    df = supertrend(df.copy(), period=period, mult=mult)
    
    # Need enough data for HMM
    if len(df) < 100:
        df['Regime'] = 'unknown'
        df['RegimeConfidence'] = 0.0
        df['FilteredTrend'] = df['Trend']
        return df
    
    try:
        # Fit HMM
        hmm_model = MarketRegimeHMM(n_regimes=4)
        hmm_model.fit(df)
        states, probs = hmm_model.predict(df)
        df, regime_map = hmm_model.label_regimes(df, states)
        
        # Get confidence
        df['RegimeConfidence'] = probs.max(axis=1)
        
        # Initialize filtered trend
        df['FilteredTrend'] = df['Trend'].copy()
        
        # Apply regime-based filtering
        for i in range(1, len(df)):
            regime = df['Regime'].iloc[i]
            trend = df['Trend'].iloc[i]
            prev_trend = df['FilteredTrend'].iloc[i-1]
            confidence = df['RegimeConfidence'].iloc[i]
            
            # Detect trend change
            trend_changed = trend != prev_trend
            
            if trend_changed:
                # Rule 1: Block ALL signals in volatile regime
                if regime == 'volatile':
                    df.iloc[i, df.columns.get_loc('FilteredTrend')] = prev_trend
                
                # Rule 2: In ranging, require high confidence
                elif regime == 'ranging' and confidence < min_confidence:
                    df.iloc[i, df.columns.get_loc('FilteredTrend')] = prev_trend
                
                # Rule 3: Don't counter-trend trade
                elif regime == 'trending_up' and trend == -1:
                    df.iloc[i, df.columns.get_loc('FilteredTrend')] = prev_trend
                
                elif regime == 'trending_down' and trend == 1:
                    df.iloc[i, df.columns.get_loc('FilteredTrend')] = prev_trend
                
                # Rule 4: Low confidence signals blocked
                elif confidence < min_confidence:
                    df.iloc[i, df.columns.get_loc('FilteredTrend')] = prev_trend
    
    except Exception as e:
        print(f"HMM failed: {e}. Using unfiltered signals.")
        df['Regime'] = 'error'
        df['RegimeConfidence'] = 0.0
        df['FilteredTrend'] = df['Trend']
    
    return df


def analyze_regime_distribution(df: pd.DataFrame):
    """Analyze time spent in each regime"""
    if 'Regime' not in df.columns:
        return pd.DataFrame({'Error': ['No regime data found']})
    
    regime_counts = df['Regime'].value_counts()
    regime_pcts = (regime_counts / len(df) * 100).round(1)
    
    regime_stats = []
    for regime in df['Regime'].unique():
        if regime == 'unknown' or regime == 'error':
            continue
        
        mask = df['Regime'] == regime
        segment = df[mask]
        
        if len(segment) == 0:
            continue
        
        avg_return = segment['Close'].pct_change().mean() * 100
        avg_volatility = ((segment['High'] / segment['Low'] - 1) * 100).mean()
        
        regime_stats.append({
            'Regime': regime,
            'Bars': int(regime_counts.get(regime, 0)),
            'Percentage': f"{regime_pcts.get(regime, 0)}%",
            'Avg Return %': round(avg_return, 3),
            'Avg Volatility %': round(avg_volatility, 2)
        })
    
    return pd.DataFrame(regime_stats)


def compare_signals(df: pd.DataFrame):
    """Compare standard vs HMM-filtered signals"""
    if 'FilteredTrend' not in df.columns:
        return {'Error': 'No filtered trend found'}
    
    # Count signal changes
    standard_changes = (df['Trend'].diff() != 0).sum()
    filtered_changes = (df['FilteredTrend'].diff() != 0).sum()
    
    blocked_signals = standard_changes - filtered_changes
    block_rate = (blocked_signals / standard_changes * 100) if standard_changes > 0 else 0
    
    return {
        'Standard Signals': standard_changes,
        'HMM Filtered Signals': filtered_changes,
        'Blocked Signals': blocked_signals,
        'Block Rate %': round(block_rate, 1)
    }