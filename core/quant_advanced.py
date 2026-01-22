import pandas as pd
import numpy as np

class AdvancedQuantEngine:
    def __init__(self, df):
        self.df = df.copy()

    def _calculate_atr(self, period=10):
        high_low = self.df['High'] - self.df['Low']
        high_close = np.abs(self.df['High'] - self.df['Close'].shift())
        low_close = np.abs(self.df['Low'] - self.df['Close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        return true_range.rolling(period).mean()

    def _add_supertrend(self, period=10, multiplier=3):
        atr = self._calculate_atr(period)
        hl2 = (self.df['High'] + self.df['Low']) / 2
        basic_upper = hl2 + (multiplier * atr)
        basic_lower = hl2 - (multiplier * atr)
        final_upper = basic_upper.copy()
        final_lower = basic_lower.copy()
        trend = np.zeros(len(self.df))
        
        for i in range(1, len(self.df)):
            if basic_upper.iloc[i] < final_upper.iloc[i-1] or self.df['Close'].iloc[i-1] > final_upper.iloc[i-1]:
                final_upper.iloc[i] = basic_upper.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i-1]
            
            if basic_lower.iloc[i] > final_lower.iloc[i-1] or self.df['Close'].iloc[i-1] < final_lower.iloc[i-1]:
                final_lower.iloc[i] = basic_lower.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i-1]
            
            if self.df['Close'].iloc[i] > final_upper.iloc[i-1]:
                trend[i] = 1
            elif self.df['Close'].iloc[i] < final_lower.iloc[i-1]:
                trend[i] = -1
            else:
                trend[i] = trend[i-1]
                if trend[i] == 1: final_lower.iloc[i] = final_lower.iloc[i]
                else: final_upper.iloc[i] = final_upper.iloc[i]
        
        self.df['Trend'] = trend
        return trend

    def _add_efficiency_ratio(self, period=10, threshold=0.25):
        change = self.df['Close'].diff(period).abs()
        volatility = self.df['Close'].diff().abs().rolling(window=period).sum()
        self.df['er'] = change / volatility
        self.df['regime'] = np.where(self.df['er'] > threshold, 'TRENDING', 'NOISE')

    def _add_z_score_momentum(self, period=20):
        mean = self.df['Close'].rolling(window=period).mean()
        std = self.df['Close'].rolling(window=period).std()
        self.df['z_score'] = (self.df['Close'] - mean) / std

    # UPDATED: Now accepts threshold parameters
    def generate_signals(self, er_threshold=0.25, z_threshold=0.2):
        self._add_efficiency_ratio(period=14, threshold=er_threshold)
        self._add_z_score_momentum(period=20)
        self._add_supertrend(period=10, multiplier=3)

        self.df['signal'] = 0
        
        long_condition = (
            (self.df['Trend'] == 1) & 
            (self.df['regime'] == 'TRENDING') & 
            (self.df['z_score'] > z_threshold)
        )

        short_condition = (
            (self.df['Trend'] == -1) & 
            (self.df['regime'] == 'TRENDING') & 
            (self.df['z_score'] < -z_threshold)
        )

        self.df.loc[long_condition, 'signal'] = 1
        self.df.loc[short_condition, 'signal'] = -1
        
        return self.df