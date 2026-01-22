"""
Base Filter Class
All filters inherit from this to ensure consistent interface
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
from datetime import datetime

class BaseFilter(ABC):
    """
    Abstract base class for all filters
    
    Every filter must:
    1. Return binary PASS/FAIL
    2. Provide clear failure reason
    3. Log metrics for performance tracking
    """
    
    def __init__(self, name: str):
        self.name = name
        self.stats = {
            'scanned': 0,
            'passed': 0,
            'failed': 0,
            'total_time_ms': 0.0
        }
    
    @abstractmethod
    def check(self, symbol: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run the filter check
        
        Args:
            symbol: Stock symbol (e.g., "PNB")
            data: Dictionary containing all required data:
                {
                    'df': DataFrame with OHLCV + indicators,
                    'index_df': Index data (for regime filter),
                    'option_chain': Option chain data (for option filter),
                    'spot_price': Current spot price,
                    'lot_size': Option lot size,
                    etc.
                }
        
        Returns:
            {
                'passed': bool,
                'reason': str,  # Why it passed/failed
                'metrics': dict,  # Supporting data for logging
                'timestamp': datetime
            }
        """
        pass
    
    def _record_result(self, passed: bool, duration_ms: float):
        """Record filter performance"""
        self.stats['scanned'] += 1
        if passed:
            self.stats['passed'] += 1
        else:
            self.stats['failed'] += 1
        self.stats['total_time_ms'] += duration_ms
    
    def get_stats(self) -> Dict:
        """Get filter performance statistics"""
        if self.stats['scanned'] > 0:
            pass_rate = (self.stats['passed'] / self.stats['scanned']) * 100
            avg_time = self.stats['total_time_ms'] / self.stats['scanned']
        else:
            pass_rate = 0
            avg_time = 0
        
        return {
            'name': self.name,
            'scanned': self.stats['scanned'],
            'passed': self.stats['passed'],
            'failed': self.stats['failed'],
            'pass_rate': round(pass_rate, 2),
            'avg_time_ms': round(avg_time, 2)
        }
    
    def reset_stats(self):
        """Reset statistics (e.g., at end of day)"""
        self.stats = {
            'scanned': 0,
            'passed': 0,
            'failed': 0,
            'total_time_ms': 0.0
        }