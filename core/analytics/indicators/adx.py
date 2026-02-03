import pandas as pd
import numpy as np
from core.analytics.indicators.base import BaseIndicator

class ADX(BaseIndicator):
    """
    Average Directional Index (ADX)
    Measures the strength of a trend.
    Values > 25 indicate a strong trend.
    """
    def __init__(self, period: int = 14):
        super().__init__("ADX")
        self.period = period

    def calculate(self, df: pd.DataFrame, **kwargs) -> pd.Series:
        """
        Calculates ADX for the given DataFrame.
        Expected columns: 'high', 'low', 'close'
        """
        if len(df) < self.period * 2:
            return pd.Series(0.0, index=df.index)

        high = df['high']
        low = df['low']
        close = df['close']

        # 1. TR, +DM, -DM
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs()
        ], axis=1).max(axis=1)

        plus_dm = high.diff()
        minus_dm = low.shift(1) - low

        plus_dm[plus_dm < 0] = 0
        plus_dm[plus_dm < minus_dm] = 0

        minus_dm[minus_dm < 0] = 0
        minus_dm[minus_dm < plus_dm] = 0

        # 2. Smooth TR, +DM, -DM (Wilder's Smoothing)
        def wilders_smoothing(series, period):
            return series.ewm(alpha=1/period, adjust=False).mean()

        str_val = wilders_smoothing(tr, self.period)
        splus_dm = wilders_smoothing(plus_dm, self.period)
        sminus_dm = wilders_smoothing(minus_dm, self.period)

        # 3. +DI, -DI
        plus_di = 100 * (splus_dm / str_val)
        minus_di = 100 * (sminus_dm / str_val)

        # 4. DX and ADX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = wilders_smoothing(dx, self.period)

        return adx
