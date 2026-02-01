"""
Drawdown Analyzer
-----------------
Calculates drawdown metrics from equity curves.
"""
import pandas as pd
import numpy as np

class DrawdownAnalyzer:
    def calculate_drawdown(self, equity_curve: pd.Series) -> pd.DataFrame:
        """
        Returns drawdown series and peak equity.
        """
        peaks = equity_curve.expanding(min_periods=1).max()
        drawdowns = (equity_curve - peaks) / peaks
        return pd.DataFrame({
            'equity': equity_curve,
            'peak': peaks,
            'drawdown': drawdowns
        })
