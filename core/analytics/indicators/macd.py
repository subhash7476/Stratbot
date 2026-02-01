"""
Moving Average Convergence Divergence (MACD)
"""
import pandas as pd
from core.analytics.indicators.base import BaseIndicator

class MACD(BaseIndicator):
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__(f"MACD_{fast}_{slow}_{signal}")
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        fast_ema = df['close'].ewm(span=self.fast, adjust=False).mean()
        slow_ema = df['close'].ewm(span=self.slow, adjust=False).mean()
        macd_line = fast_ema - slow_ema
        signal_line = macd_line.ewm(span=self.signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return pd.DataFrame({
            'macd': macd_line,
            'signal': signal_line,
            'histogram': histogram
        })
