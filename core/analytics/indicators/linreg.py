"""
Linear Regression Indicator
"""
import pandas as pd
import numpy as np
from core.analytics.indicators.base import BaseIndicator

class LinearRegression(BaseIndicator):
    def __init__(self, period: int = 14):
        super().__init__(f"LinReg_{period}")
        self.period = period

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        def get_linreg(x):
            y = np.array(x)
            x_range = np.arange(len(y))
            slope, intercept = np.polyfit(x_range, y, 1)
            return slope * (len(y) - 1) + intercept

        return df['close'].rolling(window=self.period).apply(get_linreg, raw=True)
