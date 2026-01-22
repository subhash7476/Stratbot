"""
ML Pattern Strategy - Uses trained Random Forest/XGBoost to predict patterns
"""

import pandas as pd
import numpy as np
import joblib
from pathlib import Path
import os, sys

# Ensure root is in path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def compute_pattern_features(df, lookback=30):
    """
    Compute the same features used during training
    
    Must match EXACTLY what was used in Pattern Analyzer!
    """
    features = pd.DataFrame(index=df.index)
    
    # Volume features
    features['avg_volume_30m'] = df['Volume'].rolling(lookback).mean()
    features['current_volume'] = df['Volume']
    features['volume_spike'] = df['Volume'] / features['avg_volume_30m']
    features['volume_trend'] = (
        df['Volume'].rolling(5).mean() / 
        df['Volume'].rolling(lookback).mean()
    )
    
    # Volatility features
    features['atr'] = df['ATR'] if 'ATR' in df.columns else 0
    features['price_range_pct_30m'] = (
        (df['High'].rolling(lookback).max() - df['Low'].rolling(lookback).min()) / 
        df['Close'] * 100
    )
    features['price_change_pct_30m'] = (
        (df['Close'] - df['Close'].shift(lookback)) / 
        df['Close'].shift(lookback) * 100
    )
    
    # Momentum features
    features['rsi'] = df['RSI'] if 'RSI' in df.columns else 50
    
    # EMA distance
    ema_20 = df['Close'].ewm(span=20, adjust=False).mean()
    features['ema_distance_pct'] = (df['Close'] - ema_20) / ema_20 * 100
    
    # Trend features
    features['adx'] = df['ADX'] if 'ADX' in df.columns else 20
    features['supertrend_signal'] = df['Trend'] if 'Trend' in df.columns else 0
    
    # Candle features
    body_size = (df['Close'] - df['Open']).abs()
    candle_range = df['High'] - df['Low']
    features['body_to_range_pct'] = (body_size / candle_range * 100).fillna(0)
    
    # Time features
    features['hour_of_day'] = df.index.hour
    
    return features


def generate_ml_pattern_signals(df, model_path, threshold=0.7, lookback=30):
    """
    Generate signals using trained ML model
    
    Parameters:
    -----------
    df : pd.DataFrame
        OHLCV data with indicators (ATR, RSI, ADX, Supertrend)
    model_path : str
        Path to saved .pkl model file
    threshold : float
        Probability threshold for signal (0.7 = 70% confidence)
    lookback : int
        Must match training lookback period
    
    Returns:
    --------
    pd.DataFrame
        Original df with 'Signal' and 'pattern_probability' columns
    """
    df = df.copy()
    
    # Load trained model
    try:
        model = joblib.load(model_path)
    except FileNotFoundError:
        raise FileNotFoundError(f"Model not found: {model_path}")
    
    # Compute features (must match training!)
    features_df = compute_pattern_features(df, lookback)
    
    # Get feature columns used during training
    feature_list_path = model_path.replace('.pkl', '_features.txt')
    if Path(feature_list_path).exists():
        with open(feature_list_path, 'r') as f:
            required_features = [line.strip() for line in f.readlines()]
        
        # Ensure we have all required features
        missing = set(required_features) - set(features_df.columns)
        if missing:
            raise ValueError(f"Missing features: {missing}")
        
        X = features_df[required_features]
    else:
        # Use all features (risky if order matters)
        X = features_df
    
    # Fill NaN with 0 (or median)
    X = X.fillna(0)
    
    # Predict probabilities
    probabilities = model.predict_proba(X)[:, 1]  # Probability of pattern
    
    # Generate signals
    df['pattern_probability'] = probabilities
    df['Signal'] = np.where(probabilities >= threshold, 1, 0)
    
    return df


# Strategy info
STRATEGY_INFO = {
    'name': 'ML Pattern Strategy',
    'description': 'Uses Random Forest/XGBoost to predict spike-return patterns',
    'best_timeframe': '1minute',
    'expected_win_rate': '70-90%',  # Based on your model accuracy
    'parameters': {
        'model_path': 'models/rf_RELIANCE_20241222.pkl',
        'threshold': 0.7,  # 70% confidence minimum
        'lookback': 30
    }
}


if __name__ == "__main__":
    # Test the strategy
    print("Testing ML Pattern Strategy...")
    
    # Load test data
    from data.data_manager import load_1m_data
    from core.indicators import compute_supertrend, compute_rsi
    
    df = load_1m_data('RELIANCE')
    df = compute_supertrend(df, 10, 3.0)
    df['RSI'] = compute_rsi(df, 14)
    
    # Apply strategy
    df = generate_ml_pattern_signals(
        df, 
        'models/rf_RELIANCE_220251223_164601.pkl',
        threshold=0.7
    )
    
    print(f"Signals generated: {df['Signal'].sum()}")
    print(f"Avg probability: {df['pattern_probability'].mean():.3f}")