import pandas as pd
import numpy as np
from core.analytics.indicators.base import BaseIndicator

class ATR(BaseIndicator):
    """
    Average True Range (ATR)
    Measures market volatility.
    Useful for dynamic stop-loss and take-profit levels.
    """
    def __init__(self, period: int = 14):
        super().__init__("ATR")
        self.period = period

    def calculate(self, df: pd.DataFrame, **kwargs) -> pd.Series:
        """
        Calculates ATR for the given DataFrame.
        Expected columns: 'high', 'low', 'close'
        """
        if len(df) < self.period + 1:
            return pd.Series(0.0, index=df.index)

        high = df['high']
        low = df['low']
        close = df['close']

        # 1. Calculate True Range (TR)
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # 2. Calculate ATR (RMA - Wilder's Moving Average)
        # ATR = tr.ewm(alpha=1/period, adjust=False).mean()
        atr = tr.ewm(alpha=1/self.period, min_periods=self.period, adjust=False).mean()

        return atr
