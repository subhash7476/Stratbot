# core/strategies/mean_reversion.py
"""
Mean Reversion Strategy - Bollinger Bands + RSI
Works best in: Ranging markets (60-70% of NSE trading)
Timeframe: 5min, 15min
Win Rate Target: 58-65%
"""

import pandas as pd
import numpy as np


def compute_bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0):
    """Calculate Bollinger Bands"""
    df = df.copy()
    df['BB_middle'] = df['Close'].rolling(period).mean()
    df['BB_std'] = df['Close'].rolling(period).std()
    df['BB_upper'] = df['BB_middle'] + std_dev * df['BB_std']
    df['BB_lower'] = df['BB_middle'] - std_dev * df['BB_std']
    
    # BB Width (volatility measure)
    df['BB_width'] = (df['BB_upper'] - df['BB_lower']) / df['BB_middle']
    
    # Price position in BB (0 = lower band, 1 = upper band)
    df['BB_position'] = (df['Close'] - df['BB_lower']) / (df['BB_upper'] - df['BB_lower'])
    
    return df


def compute_rsi(df: pd.DataFrame, period: int = 14):
    """Calculate RSI (Relative Strength Index)"""
    df = df.copy()
    delta = df['Close'].diff()
    
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    return df


def mean_reversion_basic(df: pd.DataFrame, 
                         bb_period: int = 20,
                         rsi_period: int = 14,
                         rsi_oversold: int = 30,
                         rsi_overbought: int = 70,
                         rsi_exit: int = 50):
    """
    Basic Mean Reversion Strategy
    
    Entry Rules:
    - LONG: RSI < 30 AND Close < Lower BB
    - SHORT: RSI > 70 AND Close > Upper BB
    
    Exit Rules:
    - LONG Exit: RSI > 50 OR Close > Middle BB
    - SHORT Exit: RSI < 50 OR Close < Middle BB
    
    Returns:
        DataFrame with Signal column (1=Buy, -1=Sell, 0=Hold)
    """
    df = df.copy()
    
    # Compute indicators
    df = compute_bollinger_bands(df, period=bb_period)
    df = compute_rsi(df, period=rsi_period)
    
    # Initialize signals
    df['Signal'] = 0
    df['Entry_Type'] = ''
    
    # Long entries (oversold + below lower BB)
    long_entry = (df['RSI'] < rsi_oversold) & (df['Close'] < df['BB_lower'])
    df.loc[long_entry, 'Signal'] = 1
    df.loc[long_entry, 'Entry_Type'] = 'LONG'
    
    # Short entries (overbought + above upper BB)
    short_entry = (df['RSI'] > rsi_overbought) & (df['Close'] > df['BB_upper'])
    df.loc[short_entry, 'Signal'] = -1
    df.loc[short_entry, 'Entry_Type'] = 'SHORT'
    
    # Exit signals (mean reversion complete)
    long_exit = (df['RSI'] > rsi_exit) | (df['Close'] > df['BB_middle'])
    short_exit = (df['RSI'] < (100 - rsi_exit)) | (df['Close'] < df['BB_middle'])
    
    df['Exit_Signal'] = 0
    df.loc[long_exit, 'Exit_Signal'] = -1  # Close long
    df.loc[short_exit, 'Exit_Signal'] = 1  # Close short
    
    return df


def mean_reversion_advanced(df: pd.DataFrame,
                            bb_period: int = 20,
                            rsi_period: int = 14,
                            volume_filter: bool = True,
                            time_filter: bool = True):
    """
    Advanced Mean Reversion with filters
    
    Additional Filters:
    - Volume spike confirmation (institutions buying the dip)
    - Time filter (avoid first/last 30 min volatility)
    - Volatility regime (only trade in normal volatility)
    
    Returns:
        DataFrame with filtered signals
    """
    df = df.copy()
    
    # Base strategy
    df = mean_reversion_basic(df, bb_period, rsi_period)
    
    # Volume Filter: Require above-average volume
    if volume_filter and 'Volume' in df.columns:
        df['Volume_MA'] = df['Volume'].rolling(20).mean()
        df['Volume_Spike'] = df['Volume'] > (df['Volume_MA'] * 1.3)
        
        # Filter signals: only keep those with volume confirmation
        df.loc[df['Signal'] != 0, 'Signal'] = df.loc[df['Signal'] != 0].apply(
            lambda row: row['Signal'] if row['Volume_Spike'] else 0, axis=1
        )
    
    # Time Filter: Avoid first/last 30 minutes
    if time_filter and isinstance(df.index, pd.DatetimeIndex):
        df['time'] = df.index.time
        
        # Market hours: 9:15 AM to 3:30 PM IST
        avoid_start = pd.Timestamp('09:15').time()
        avoid_end = pd.Timestamp('09:45').time()
        close_avoid = pd.Timestamp('15:00').time()
        
        bad_times = ((df['time'] >= avoid_start) & (df['time'] <= avoid_end)) | \
                    (df['time'] >= close_avoid)
        
        df.loc[bad_times, 'Signal'] = 0
    
    # Volatility Regime Filter: Only trade in normal volatility
    # High BB width = high volatility = avoid
    # Low BB width = low volatility = squeeze, avoid
    if 'BB_width' in df.columns:
        bb_width_ma = df['BB_width'].rolling(50).mean()
        bb_width_std = df['BB_width'].rolling(50).std()
        
        # Only trade when BB width is within 1 std dev of mean
        normal_vol = (df['BB_width'] > (bb_width_ma - bb_width_std)) & \
                     (df['BB_width'] < (bb_width_ma + bb_width_std))
        
        df.loc[~normal_vol, 'Signal'] = 0
    
    return df


def generate_strategy_params():
    """
    Return recommended parameter ranges for optimization
    """
    return {
        'bb_period': [15, 20, 25],
        'bb_std_dev': [1.5, 2.0, 2.5],
        'rsi_period': [10, 14, 20],
        'rsi_oversold': [25, 30, 35],
        'rsi_overbought': [65, 70, 75],
        'rsi_exit': [45, 50, 55],
        'volume_filter': [True, False],
        'time_filter': [True, False]
    }


# Strategy metadata for the backtester
STRATEGY_INFO = {
    'name': 'Mean Reversion (BB + RSI)',
    'description': 'Buy oversold, sell overbought. Works in ranging markets.',
    'best_timeframe': '15minute',
    'best_markets': ['Ranging', 'Low Volatility'],
    'expected_win_rate': '58-65%',
    'expected_profit_factor': '1.5-2.0',
    'max_holding_time': '2-4 hours',
    'stop_loss': '0.5-0.8%',
    'take_profit': '1.2-2.0%',
    'risk_reward': '2.0-3.0'
}