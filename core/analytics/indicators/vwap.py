"""
Volume Weighted Average Price (VWAP)
"""
import pandas as pd
from core.analytics.indicators.base import BaseIndicator

class VWAP(BaseIndicator):
    def __init__(self):
        super().__init__("VWAP")

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        # VWAP typically resets daily
        # Since we are likely working with 1m bars
        v = df['volume']
        p = (df['high'] + df['low'] + df['close']) / 3
        return (p * v).cumsum() / v.cumsum()
