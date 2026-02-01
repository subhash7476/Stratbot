"""
Base Indicator Class
"""
from abc import ABC, abstractmethod
import pandas as pd

class BaseIndicator(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def calculate(self, df: pd.DataFrame) -> pd.Series:
        pass
