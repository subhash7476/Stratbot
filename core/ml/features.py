# core/ml/features.py
"""
Machine Learning Feature Engineering
Comprehensive feature set for price prediction
"""

import pandas as pd
import numpy as np
from typing import List


def add_price_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Price-based features
    - Returns at multiple timeframes
    - Price momentum indicators
    """
    df = df.copy()
    
    # Returns at different periods
    for period in [1, 3, 5, 10, 20, 30]:
        df[f'return_{period}'] = df['Close'].pct_change(period)
    
    # Price vs moving averages
    for ma in [5, 10, 20, 50]:
        df[f'price_vs_ma{ma}'] = (df['Close'] - df['Close'].rolling(ma).mean()) / df['Close']
    
    # Relative position in recent range
    for period in [10, 20, 50]:
        high_n = df['High'].rolling(period).max()
        low_n = df['Low'].rolling(period).min()
        df[f'price_position_{period}'] = (df['Close'] - low_n) / (high_n - low_n + 1e-9)
    
    return df


def add_volume_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Volume-based features
    - Volume ratios
    - Volume trends
    - Money flow indicators
    """
    df = df.copy()
    
    if 'Volume' not in df.columns:
        return df
    
    # Volume ratios vs moving average
    for period in [5, 10, 20]:
        df[f'volume_ratio_{period}'] = df['Volume'] / (df['Volume'].rolling(period).mean() + 1)
    
    # Volume trend (increasing/decreasing)
    df['volume_trend_5_20'] = df['Volume'].rolling(5).mean() / (df['Volume'].rolling(20).mean() + 1)
    
    # Money Flow (volume-weighted price change)
    df['money_flow'] = df['Close'].diff() * df['Volume']
    df['money_flow_5'] = df['money_flow'].rolling(5).sum()
    df['money_flow_20'] = df['money_flow'].rolling(20).sum()
    
    # On-Balance Volume (OBV)
    df['obv'] = (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
    df['obv_slope'] = df['obv'].diff(5)
    
    return df


def add_volatility_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Volatility-based features
    - ATR and variations
    - Bollinger Band width
    - Price range indicators
    """
    df = df.copy()
    
    # True Range
    df['hl'] = df['High'] - df['Low']
    df['hc'] = abs(df['High'] - df['Close'].shift())
    df['lc'] = abs(df['Low'] - df['Close'].shift())
    df['tr'] = df[['hl', 'hc', 'lc']].max(axis=1)
    
    # ATR at multiple periods
    for period in [5, 10, 20]:
        df[f'atr_{period}'] = df['tr'].rolling(period).mean()
        df[f'atr_{period}_pct'] = df[f'atr_{period}'] / df['Close']
    
    # ATR trend (expanding/contracting volatility)
    df['atr_trend'] = df['atr_10'] - df['atr_20']
    
    # Bollinger Band width
    for period in [20]:
        bb_mid = df['Close'].rolling(period).mean()
        bb_std = df['Close'].rolling(period).std()
        df[f'bb_width_{period}'] = (bb_std * 2) / bb_mid
    
    # Price range as % of close
    df['daily_range_pct'] = (df['High'] - df['Low']) / df['Close']
    
    return df


def add_momentum_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Momentum indicators
    - RSI
    - MACD
    - Stochastic
    - Rate of Change
    """
    df = df.copy()
    
    # RSI
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # MACD
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['macd'] = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    
    # Stochastic Oscillator
    for period in [14]:
        low_n = df['Low'].rolling(period).min()
        high_n = df['High'].rolling(period).max()
        df[f'stoch_{period}'] = 100 * (df['Close'] - low_n) / (high_n - low_n + 1e-9)
        df[f'stoch_{period}_smooth'] = df[f'stoch_{period}'].rolling(3).mean()
    
    # Rate of Change (ROC)
    for period in [5, 10, 20]:
        df[f'roc_{period}'] = (df['Close'] - df['Close'].shift(period)) / df['Close'].shift(period)
    
    return df


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Time-based features
    - Hour, minute, day of week
    - Session indicators
    """
    df = df.copy()
    
    if not isinstance(df.index, pd.DatetimeIndex):
        return df
    
    # Basic time features
    df['hour'] = df.index.hour
    df['minute'] = df.index.minute
    df['day_of_week'] = df.index.dayofweek
    
    # Trading session (1=opening, 2=mid-day, 3=closing)
    df['session'] = 2  # Default: mid-day
    df.loc[df['hour'] == 9, 'session'] = 1  # Opening
    df.loc[df['hour'] >= 15, 'session'] = 3  # Closing
    
    # Minutes since market open (9:15 AM)
    market_open_minutes = 9 * 60 + 15
    current_minutes = df['hour'] * 60 + df['minute']
    df['minutes_since_open'] = current_minutes - market_open_minutes
    
    # Cyclical encoding (sin/cos for hour to preserve periodicity)
    df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
    df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
    
    return df


def add_pattern_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Candlestick pattern features
    - Body/wick ratios
    - Candle strength
    """
    df = df.copy()
    
    # Candle body
    df['body'] = abs(df['Close'] - df['Open'])
    df['body_pct'] = df['body'] / df['Close']
    
    # Wicks
    df['upper_wick'] = df['High'] - df[['Open', 'Close']].max(axis=1)
    df['lower_wick'] = df[['Open', 'Close']].min(axis=1) - df['Low']
    
    df['upper_wick_pct'] = df['upper_wick'] / df['Close']
    df['lower_wick_pct'] = df['lower_wick'] / df['Close']
    
    # Bullish/Bearish candles
    df['is_bullish'] = (df['Close'] > df['Open']).astype(int)
    
    # Consecutive patterns
    df['consecutive_bullish'] = df['is_bullish'].rolling(3).sum()
    df['consecutive_bearish'] = (1 - df['is_bullish']).rolling(3).sum()
    
    return df


def create_target(df: pd.DataFrame, 
                  horizon: int = 15,
                  threshold_pct: float = 0.3,
                  method: str = 'classification') -> pd.DataFrame:
    """
    Create prediction target
    
    Args:
        horizon: Look-ahead period (e.g., 15 candles = 15 minutes for 1-min data)
        threshold_pct: Threshold for classification (0.3% default)
        method: 'classification' (up/down/neutral) or 'regression' (actual return)
    
    Returns:
        DataFrame with 'target' column
    """
    df = df.copy()
    
    # Future return
    df['future_return'] = df['Close'].shift(-horizon).pct_change(horizon)
    
    if method == 'classification':
        # 3-class classification: UP (1), NEUTRAL (0), DOWN (-1)
        df['target'] = 0  # Neutral
        df.loc[df['future_return'] > threshold_pct / 100, 'target'] = 1  # Up
        df.loc[df['future_return'] < -threshold_pct / 100, 'target'] = -1  # Down
    
    elif method == 'regression':
        # Continuous target (actual return)
        df['target'] = df['future_return']
    
    else:
        raise ValueError(f"Unknown method: {method}")
    
    # Drop the last `horizon` rows (no future data)
    df = df.iloc[:-horizon]
    
    return df


def engineer_all_features(df: pd.DataFrame, 
                          for_training: bool = True,
                          target_horizon: int = 15) -> pd.DataFrame:
    """
    Apply all feature engineering steps
    
    Args:
        df: Raw OHLCV DataFrame
        for_training: If True, create target column
        target_horizon: Look-ahead period for target
    
    Returns:
        DataFrame with all features
    """
    df = df.copy()
    
    print("Engineering features...")
    
    # Add all feature groups
    df = add_price_features(df)
    df = add_volume_features(df)
    df = add_volatility_features(df)
    df = add_momentum_features(df)
    df = add_time_features(df)
    df = add_pattern_features(df)
    
    # Create target if training
    if for_training:
        df = create_target(df, horizon=target_horizon)
    
    # Drop rows with NaN (from rolling windows)
    df = df.dropna()
    
    print(f"Features created: {len(df.columns)} columns, {len(df)} rows")
    
    return df


def get_feature_columns() -> List[str]:
    """
    Return list of feature column names (for model training)
    Excludes: OHLCV, target, intermediate calculations
    """
    # This list should match the features created above
    base_features = [
        # Price features
        'return_1', 'return_3', 'return_5', 'return_10', 'return_20', 'return_30',
        'price_vs_ma5', 'price_vs_ma10', 'price_vs_ma20', 'price_vs_ma50',
        'price_position_10', 'price_position_20', 'price_position_50',
        
        # Volume features
        'volume_ratio_5', 'volume_ratio_10', 'volume_ratio_20',
        'volume_trend_5_20', 'money_flow_5', 'money_flow_20', 'obv_slope',
        
        # Volatility features
        'atr_5', 'atr_10', 'atr_20',
        'atr_5_pct', 'atr_10_pct', 'atr_20_pct',
        'atr_trend', 'bb_width_20', 'daily_range_pct',
        
        # Momentum features
        'rsi', 'macd', 'macd_signal', 'macd_hist',
        'stoch_14', 'stoch_14_smooth',
        'roc_5', 'roc_10', 'roc_20',
        
        # Time features
        'hour', 'minute', 'day_of_week', 'session', 'minutes_since_open',
        'hour_sin', 'hour_cos',
        
        # Pattern features
        'body_pct', 'upper_wick_pct', 'lower_wick_pct',
        'is_bullish', 'consecutive_bullish', 'consecutive_bearish'
    ]
    
    return base_features