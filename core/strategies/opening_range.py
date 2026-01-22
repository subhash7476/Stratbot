# core/strategies/opening_range.py
"""
Opening Range Breakout (ORB) Strategy
Works best in: Trending days, Gap-fill scenarios
Timeframe: 5min, 15min (for intraday)
Win Rate Target: 55-60%
"""

import pandas as pd
import numpy as np
from datetime import time as datetime_time


def identify_opening_range(df: pd.DataFrame, 
                           or_minutes: int = 15,
                           market_open: str = '09:15',
                           buffer_pct: float = 0.003,
                           debug: bool = False):
    """
    Identify the opening range (first N minutes high/low)
    
    Args:
        df: DataFrame with DateTimeIndex
        or_minutes: Opening range duration (default 15 min)
        market_open: Market opening time (IST)
        buffer_pct: Breakout buffer (0.3% default)
    
    Returns:
        DataFrame with OR_High, OR_Low, Breakout levels
    """
    df = df.copy()
    
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("DataFrame must have DateTimeIndex")
    
    # Extract time
    df['time'] = df.index.time
    df['date'] = df.index.date
    
    # Parse market open time
    open_time = pd.Timestamp(market_open).time()
    or_end_time = (pd.Timestamp(market_open) + pd.Timedelta(minutes=or_minutes)).time()
    
    # Initialize columns
    df['OR_High'] = np.nan
    df['OR_Low'] = np.nan
    df['OR_Breakout_High'] = np.nan
    df['OR_Breakout_Low'] = np.nan
    
    # Calculate opening range for each day
    for date in df['date'].unique():
        day_mask = df['date'] == date
        or_mask = day_mask & (df['time'] >= open_time) & (df['time'] <= or_end_time)
        
        if or_mask.sum() > 0:
            or_high = df.loc[or_mask, 'High'].max()
            or_low = df.loc[or_mask, 'Low'].min()
            
            # Apply to entire day
            df.loc[day_mask, 'OR_High'] = or_high
            df.loc[day_mask, 'OR_Low'] = or_low
            
            # Breakout levels with buffer (reduces false breakouts)
            df.loc[day_mask, 'OR_Breakout_High'] = or_high * (1 + buffer_pct)
            df.loc[day_mask, 'OR_Breakout_Low'] = or_low * (1 - buffer_pct)
    
    return df


def opening_range_breakout(df: pd.DataFrame,
                           or_minutes: int = 15,
                           market_open: str = '09:15',
                           buffer_pct: float = 0.003,
                           exit_time: str = '15:15'):
    """
    Opening Range Breakout Strategy
    
    Entry Rules:
    - LONG: Close > OR_High + buffer (breakout upside)
    - SHORT: Close < OR_Low - buffer (breakout downside)
    
    Exit Rules:
    - End of day (15:15 PM)
    - OR breakdown (close back inside range)
    
    Returns:
        DataFrame with Signal column
    """
    df = df.copy()
    
    # Identify opening range
    df = identify_opening_range(df, or_minutes, market_open, buffer_pct)
    
    # Initialize signals
    df['Signal'] = 0
    df['Entry_Type'] = ''
    
    # Parse exit time
    exit_time_obj = pd.Timestamp(exit_time).time()
    
    # Long breakout (bullish)
    long_breakout = df['Close'] > df['OR_Breakout_High']
    df.loc[long_breakout, 'Signal'] = 1
    df.loc[long_breakout, 'Entry_Type'] = 'LONG_BREAKOUT'
    
    # Short breakout (bearish)
    short_breakout = df['Close'] < df['OR_Breakout_Low']
    df.loc[short_breakout, 'Signal'] = -1
    df.loc[short_breakout, 'Entry_Type'] = 'SHORT_BREAKOUT'
    
    # Exit signals
    df['Exit_Signal'] = 0
    
    # Exit at end of day
    eod_exit = df['time'] >= exit_time_obj
    df.loc[eod_exit, 'Exit_Signal'] = 1
    
    # Exit on range breakdown (price returns inside OR)
    long_exit = (df['Close'] < df['OR_Low'])
    short_exit = (df['Close'] > df['OR_High'])
    
    df.loc[long_exit, 'Exit_Signal'] = -1
    df.loc[short_exit, 'Exit_Signal'] = 1
    
    return df


def opening_range_advanced(df: pd.DataFrame,
                           or_minutes: int = 15,
                           volume_confirmation: bool = True,
                           gap_filter: bool = True,
                           min_or_range_pct: float = 0.5):
    """
    Advanced ORB with filters
    
    Filters:
    - Volume confirmation (breakout on high volume)
    - Gap filter (only trade gap-fill scenarios)
    - Minimum OR range (avoid tight ranges)
    
    Returns:
        DataFrame with filtered signals
    """
    df = df.copy()
    
    # Base strategy
    df = opening_range_breakout(df, or_minutes)
    
    # Filter 1: Minimum OR range (avoid tight consolidation)
    df['OR_Range_Pct'] = ((df['OR_High'] - df['OR_Low']) / df['OR_Low']) * 100
    
    narrow_range = df['OR_Range_Pct'] < min_or_range_pct
    df.loc[narrow_range, 'Signal'] = 0
    
    # Filter 2: Volume confirmation
    if volume_confirmation and 'Volume' in df.columns:
        df['Volume_MA'] = df['Volume'].rolling(20).mean()
        
        # Require 1.5x average volume on breakout
        low_volume = df['Volume'] < (df['Volume_MA'] * 1.5)
        df.loc[low_volume & (df['Signal'] != 0), 'Signal'] = 0
    
    # Filter 3: Gap filter (only trade gaps > 0.5%)
    if gap_filter:
        # Detect gap: first bar Open vs previous Close
        df['Gap_Pct'] = ((df['Open'] - df['Close'].shift(1)) / df['Close'].shift(1)) * 100
        
        # For long signals, prefer positive gaps (gap up)
        # For short signals, prefer negative gaps (gap down)
        df.loc[(df['Signal'] == 1) & (df['Gap_Pct'] < 0.3), 'Signal'] = 0
        df.loc[(df['Signal'] == -1) & (df['Gap_Pct'] > -0.3), 'Signal'] = 0
    
    return df


def orb_statistics(df: pd.DataFrame):
    """
    Calculate OR statistics for analysis
    """
    stats = {
        'avg_or_range_pct': df['OR_Range_Pct'].mean(),
        'breakout_up_pct': (df['Close'] > df['OR_High']).mean() * 100,
        'breakout_down_pct': (df['Close'] < df['OR_Low']).mean() * 100,
        'avg_gap_pct': df['Gap_Pct'].mean() if 'Gap_Pct' in df.columns else 0,
    }
    return stats


# Strategy metadata
STRATEGY_INFO = {
    'name': 'Opening Range Breakout',
    'description': 'Trade breakouts from first 15-min range',
    'best_timeframe': '5minute',
    'best_markets': ['Trending', 'Gap Fill', 'High Momentum'],
    'expected_win_rate': '55-60%',
    'expected_profit_factor': '1.6-2.1',
    'max_holding_time': '4-6 hours (till EOD)',
    'stop_loss': '0.5-1.0%',
    'take_profit': '1.5-3.0%',
    'risk_reward': '2.0-3.0',
    'trades_per_day': '1-2 per stock'
}