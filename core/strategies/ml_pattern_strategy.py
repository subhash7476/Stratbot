"""
ML Pattern Strategy - Uses trained Random Forest/XGBoost to predict spike-return patterns

Integrates with Trading Bot Pro backtesting system.

Location: core/strategies/ml_pattern_strategy.py
"""

import pandas as pd
import numpy as np
import joblib
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')


def compute_pattern_features(df, lookback=30):
    """
    Compute features that match Pattern Analyzer training data
    
    CRITICAL: Features must match EXACTLY what was used during training!
    
    Parameters:
    -----------
    df : pd.DataFrame
        OHLCV data with indicators (ATR, RSI, ADX, Supertrend/Trend)
    lookback : int
        Lookback period (default: 30, must match training)
    
    Returns:
    --------
    pd.DataFrame
        Features for each candle
    """
    features = pd.DataFrame(index=df.index)
    
    # Volume features
    features['avg_volume_30m'] = df['Volume'].rolling(lookback).mean()
    features['current_volume'] = df['Volume']
    features['volume_spike'] = df['Volume'] / features['avg_volume_30m'].replace(0, 1)
    features['volume_trend'] = (
        df['Volume'].rolling(5).mean() / 
        df['Volume'].rolling(lookback).mean().replace(0, 1)
    )
    
    # Volatility features
    features['atr'] = df.get('ATR', 0)
    features['price_range_pct_30m'] = (
        (df['High'].rolling(lookback).max() - df['Low'].rolling(lookback).min()) / 
        df['Close'].shift(1) * 100
    ).replace([np.inf, -np.inf], 0)
    
    features['price_change_pct_30m'] = (
        (df['Close'] - df['Close'].shift(lookback)) / 
        df['Close'].shift(lookback) * 100
    ).replace([np.inf, -np.inf], 0)
    
    # Momentum features
    features['rsi'] = df.get('RSI', 50.0)
    
    # EMA distance
    ema_20 = df['Close'].ewm(span=20, adjust=False).mean()
    features['ema_distance_pct'] = ((df['Close'] - ema_20) / ema_20 * 100).replace([np.inf, -np.inf], 0)
    
    # Trend features
    features['adx'] = df.get('ADX', 20.0)
    features['supertrend_signal'] = df.get('Trend', 0)
    
    # Candle features
    body_size = (df['Close'] - df['Open']).abs()
    candle_range = (df['High'] - df['Low']).replace(0, 1)
    features['body_to_range_pct'] = (body_size / candle_range * 100).replace([np.inf, -np.inf], 0)
    
    # Time features
    features['hour_of_day'] = df.index.hour
    
    # Fill NaN values
    features = features.fillna(0)
    
    return features


def load_model_and_features(model_path):
    """
    Load trained model and its feature list
    
    Parameters:
    -----------
    model_path : str or Path
        Path to .pkl model file
    
    Returns:
    --------
    tuple: (model, feature_list)
    """
    model_path = Path(model_path)
    
    if not model_path.exists():
        raise FileNotFoundError(
            f"Model not found: {model_path}\n"
            f"Train a model in Pattern Analyzer and save it first!"
        )
    
    # Load model
    model = joblib.load(model_path)
    
    # Load feature list if available
    feature_list_path = model_path.with_name(model_path.stem + '_features.txt')
    if feature_list_path.exists():
        with open(feature_list_path, 'r') as f:
            feature_list = [line.strip() for line in f.readlines()]
    else:
        # Try to infer from model
        if hasattr(model, 'feature_names_in_'):
            feature_list = list(model.feature_names_in_)
        else:
            feature_list = None
    
    return model, feature_list


def generate_ml_pattern_signals(df, model_path='models/rf_latest.pkl', 
                                 threshold=0.7, lookback=30):
    """
    Generate trading signals using trained ML model
    
    This is the main function called by the backtester.
    
    Parameters:
    -----------
    df : pd.DataFrame
        OHLCV data with indicators already computed
        Required: Open, High, Low, Close, Volume
        Recommended: ATR, RSI, ADX, Trend
    model_path : str
        Path to saved .pkl model file (default: models/rf_latest.pkl)
    threshold : float
        Minimum probability to generate signal (0.0 to 1.0)
        0.7 = 70% confidence required
    lookback : int
        Lookback period for features (must match training, default: 30)
    
    Returns:
    --------
    pd.DataFrame
        Original df with added columns:
        - 'pattern_probability': Model's predicted probability (0-1)
        - 'Signal': Trading signal (1 = Long, 0 = No trade)
    """
    df = df.copy()
    
    # Load model
    try:
        model, required_features = load_model_and_features(model_path)
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"{e}\n\n"
            f"To fix:\n"
            f"1. Open Pattern Analyzer (page 9)\n"
            f"2. Train Random Forest or XGBoost\n"
            f"3. Save model to: {model_path}"
        )
    
    # Compute features
    features_df = compute_pattern_features(df, lookback)
    
    # Select required features
    if required_features:
        missing = set(required_features) - set(features_df.columns)
        if missing:
            raise ValueError(
                f"Missing features: {missing}\n"
                f"Available: {list(features_df.columns)}\n"
                f"Required: {required_features}"
            )
        X = features_df[required_features]
    else:
        X = features_df
    
    # Handle NaN
    X = X.fillna(0)
    
    # Predict probabilities
    try:
        probabilities = model.predict_proba(X)[:, 1]
    except Exception as e:
        raise RuntimeError(
            f"Prediction failed: {e}\n"
            f"Features may not match training data"
        )
    
    # Add to dataframe
    df['pattern_probability'] = probabilities
    df['Signal'] = np.where(probabilities >= threshold, 1, 0)
    
    return df


# Alternative: Generate signals with additional filters
def generate_ml_pattern_signals_filtered(df, model_path='models/rf_latest.pkl',
                                         threshold=0.7, lookback=30,
                                         min_volume_spike=None, rsi_range=None,
                                         time_range=None, min_atr=None):
    """
    Generate ML signals with additional rule-based filters
    
    Example:
        df = generate_ml_pattern_signals_filtered(
            df,
            model_path='models/rf_RELIANCE.pkl',
            threshold=0.7,
            min_volume_spike=2.0,    # Volume must be 2x average
            rsi_range=(40, 60),      # RSI between 40-60
            time_range=(10, 14),     # Only 10 AM - 2 PM
            min_atr=0.3              # Minimum ATR
        )
    """
    # Generate base ML signals
    df = generate_ml_pattern_signals(df, model_path, threshold, lookback)
    
    # Apply additional filters
    mask = df['Signal'] == 1
    
    if min_volume_spike is not None:
        volume_spike = df['Volume'] / df['Volume'].rolling(30).mean()
        mask &= volume_spike >= min_volume_spike
    
    if rsi_range is not None:
        rsi_min, rsi_max = rsi_range
        mask &= (df['RSI'] >= rsi_min) & (df['RSI'] <= rsi_max)
    
    if time_range is not None:
        hour_min, hour_max = time_range
        mask &= (df.index.hour >= hour_min) & (df.index.hour < hour_max)
    
    if min_atr is not None:
        mask &= df['ATR'] >= min_atr
    
    df['Signal'] = np.where(mask, 1, 0)
    
    return df


# Strategy metadata for backtester
STRATEGY_INFO = {
    'name': 'ML Pattern Strategy',
    'description': 'Uses Random Forest/XGBoost to predict spike-return patterns trained in Pattern Analyzer',
    'version': '1.0',
    'author': 'Trading Bot Pro',
    'best_timeframe': '1minute',
    'expected_win_rate': '70-95%',
    'requires_model': True,
    'parameters': {
        'model_path': 'Path to trained model (.pkl)',
        'threshold': 'Probability threshold (0.5-0.95, default: 0.7)',
        'lookback': 'Feature lookback period (default: 30)'
    }
}


if __name__ == "__main__":
    """
    Test the strategy
    """
    print("="*70)
    print("ML PATTERN STRATEGY - INTEGRATION TEST")
    print("="*70)
    
    import sys
    import os
    sys.path.insert(0, os.getcwd())
    
    try:
        from data.data_manager import load_1m_data
        from core.indicators import compute_supertrend, compute_rsi
        
        # Load test data
        symbol = 'RELIANCE'
        print(f"\nLoading {symbol} data...")
        df = load_1m_data(symbol)
        
        if df is not None and not df.empty:
            df = df.iloc[-5000:]  # Last 5k candles
            print(f"✅ Loaded {len(df):,} candles")
            
            # Compute indicators
            print("Computing indicators...")
            df = compute_supertrend(df, 10, 3.0)
            df['RSI'] = compute_rsi(df, 14)
            print("✅ Indicators computed")
            
            # Test strategy
            model_path = 'models/rf_RELIANCE_latest.pkl'
            
            if not Path(model_path).exists():
                print(f"\n⚠️  Model not found: {model_path}")
                print("Run Pattern Analyzer to train and save a model first")
            else:
                print(f"\nGenerating ML signals...")
                df = generate_ml_pattern_signals(df, model_path, threshold=0.7)
                
                print(f"\n✅ SUCCESS!")
                print(f"   Signals: {df['Signal'].sum():,}")
                print(f"   Avg probability: {df['pattern_probability'].mean():.3f}")
                print(f"   Max probability: {df['pattern_probability'].max():.3f}")
                
                print("\n✅ Strategy integration test passed!")
        else:
            print("❌ No data found")
    
    except ImportError as e:
        print(f"⚠️  Import error: {e}")
        print("Make sure you're in the project root directory")
    
    print("\n" + "="*70)