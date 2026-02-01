"""
Exponential Moving Average (EMA)
"""
import pandas as pd
from core.analytics.indicators.base import BaseIndicator

class EMA(BaseIndicator):
    def __init__(self, period: int = 20):
        super().__init__(f"EMA_{period}")
        self.period = period

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        return df['close'].ewm(span=self.period, adjust=False).mean()
