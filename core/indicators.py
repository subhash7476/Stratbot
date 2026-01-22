import pandas as pd
import numpy as np

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Compute Average True Range (ATR)
    
    Parameters:
    -----------
    df : pd.DataFrame
        DataFrame with 'High', 'Low', 'Close' columns
    period : int
        ATR period (default: 14)
    
    Returns:
    --------
    pd.DataFrame
        Original DataFrame with 'ATR' column added
    """
    df = df.copy()
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    # Calculate True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Calculate ATR using exponential moving average
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    
    df['ATR'] = atr
    return df


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Compute Relative Strength Index (RSI)
    
    Parameters:
    -----------
    df : pd.DataFrame
        DataFrame with 'Close' column
    period : int
        RSI period (default: 14)
    
    Returns:
    --------
    pd.Series
        RSI values (NOT a DataFrame)
    """
    close = df['Close']
    
    # Calculate price changes
    delta = close.diff()
    
    # Separate gains and losses
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    # Calculate exponential moving averages of gains and losses
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    
    # Calculate RS (Relative Strength)
    rs = avg_gain / avg_loss
    
    # Calculate RSI
    rsi = 100 - (100 / (1 + rs))
    
    return rsi


def compute_supertrend(df: pd.DataFrame, atr_period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """
    Compute Supertrend indicator
    
    Parameters:
    -----------
    df : pd.DataFrame
        DataFrame with 'High', 'Low', 'Close' columns
    atr_period : int
        ATR period (default: 10)
    multiplier : float
        ATR multiplier (default: 3.0)
    
    Returns:
    --------
    pd.DataFrame
        Original DataFrame with 'Supertrend', 'Trend', 'ATR' columns added
    """
    df = df.copy()
    high = df['High']; low = df['Low']; close = df['Close']
    tr = pd.concat([(high-low), (high-close.shift(1)).abs(), (low-close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/atr_period, adjust=False).mean()
    hl2 = (high + low) / 2
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr
    trend = pd.Series(1, index=df.index)
    st_line = pd.Series(np.nan, index=df.index)
    st_line.iloc[0] = lower.iloc[0]
    for i in range(1, len(df)):
        if close.iat[i] > upper.iat[i-1]:
            trend.iat[i] = 1
        elif close.iat[i] < lower.iat[i-1]:
            trend.iat[i] = -1
        else:
            trend.iat[i] = trend.iat[i-1]
        st_line.iat[i] = lower.iat[i] if trend.iat[i] == 1 else upper.iat[i]
    df['Supertrend'] = st_line
    df['Trend'] = trend
    df['ATR'] = atr
    return df