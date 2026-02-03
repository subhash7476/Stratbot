"""
Base Indicator Class
"""
from abc import ABC, abstractmethod
import pandas as pd

class BaseIndicator(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def calculate(self, df: pd.DataFrame, **kwargs):
        """
        Calculate the indicator value(s).

        Args:
            df: Input DataFrame with required columns
            **kwargs: Additional parameters for the calculation

        Returns:
            Result of the indicator calculation (can be Series, DataFrame, or other types)
        """
        pass
