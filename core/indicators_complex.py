import pandas as pd
import numpy as np

def atr(df: pd.DataFrame, period: int = 10) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

def compute_supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0):
    """
    CORRECTED Supertrend implementation
    - Fixes trend flip logic (was checking i-1, now checks i)
    - Proper band persistence
    """
    df = df.copy()
    high, low, close = df["High"], df["Low"], df["Close"]
    
    # True Range calculation
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    
    # ATR using EWM
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    
    # Basic bands
    hl2 = (high + low) / 2.0
    upperband = hl2 + mult * atr
    lowerband = hl2 - mult * atr
    
    # Initialize final bands
    final_upper = upperband.copy()
    final_lower = lowerband.copy()
    
    # Band persistence logic
    for i in range(1, len(df)):
        # Upper band
        if upperband.iloc[i] < final_upper.iloc[i-1] or close.iloc[i-1] > final_upper.iloc[i-1]:
            final_upper.iloc[i] = upperband.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i-1]
        
        # Lower band
        if lowerband.iloc[i] > final_lower.iloc[i-1] or close.iloc[i-1] < final_lower.iloc[i-1]:
            final_lower.iloc[i] = lowerband.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i-1]
    
    # CRITICAL FIX: Trend determination using CURRENT bar (not i-1)
    trend = pd.Series(1, index=df.index, dtype=int)
    
    for i in range(1, len(df)):
        if trend.iloc[i-1] == 1:
            # In uptrend: flip down only if close breaks CURRENT lower band
            if close.iloc[i] <= final_lower.iloc[i]:  # ← Was iloc[i-1], now iloc[i]
                trend.iloc[i] = -1
            else:
                trend.iloc[i] = 1
        else:
            # In downtrend: flip up only if close breaks CURRENT upper band
            if close.iloc[i] >= final_upper.iloc[i]:  # ← Was iloc[i-1], now iloc[i]
                trend.iloc[i] = 1
            else:
                trend.iloc[i] = -1
    
    # Supertrend line
    df["Supertrend"] = final_lower.where(trend == 1, final_upper)
    df["Trend"] = trend
    df["ATR"] = atr
    
    return df


def supertrend_adaptive(df: pd.DataFrame, base_period: int = 10):
    """
    Adaptive Supertrend - adjusts multiplier based on volatility
    Use this for better performance in varying market conditions
    """
    df = df.copy()
    high, low, close = df["High"], df["Low"], df["Close"]
    
    # Calculate ATR
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/base_period, adjust=False).mean()
    
    # Adaptive multiplier (2.5 in low vol, 3.5 in high vol)
    atr_ma = atr.rolling(50).mean()
    atr_ratio = (atr / atr_ma).clip(0.5, 1.5)
    multiplier = 2.5 + (atr_ratio - 0.5)
    
    # Rest of calculation with adaptive mult
    hl2 = (high + low) / 2.0
    upperband = hl2 + multiplier * atr
    lowerband = hl2 - multiplier * atr
    
    final_upper = upperband.copy()
    final_lower = lowerband.copy()
    
    for i in range(1, len(df)):
        if upperband.iloc[i] < final_upper.iloc[i-1] or close.iloc[i-1] > final_upper.iloc[i-1]:
            final_upper.iloc[i] = upperband.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i-1]
        
        if lowerband.iloc[i] > final_lower.iloc[i-1] or close.iloc[i-1] < final_lower.iloc[i-1]:
            final_lower.iloc[i] = lowerband.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i-1]
    
    trend = pd.Series(1, index=df.index, dtype=int)
    for i in range(1, len(df)):
        if trend.iloc[i-1] == 1:
            trend.iloc[i] = -1 if close.iloc[i] <= final_lower.iloc[i] else 1
        else:
            trend.iloc[i] = 1 if close.iloc[i] >= final_upper.iloc[i] else -1
    
    df["Supertrend"] = final_lower.where(trend == 1, final_upper)
    df["Trend"] = trend
    df["ATR"] = atr
    df["Multiplier"] = multiplier
    
    return df