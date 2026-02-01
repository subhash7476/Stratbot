"""
Relative Strength Index (RSI)
"""
import pandas as pd
import numpy as np
from core.analytics.indicators.base import BaseIndicator

class RSI(BaseIndicator):
    def __init__(self, period: int = 14):
        super().__init__(f"RSI_{period}")
        self.period = period

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
