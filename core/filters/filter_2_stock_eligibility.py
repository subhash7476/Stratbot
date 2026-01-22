"""
Filter 2: Stock Eligibility Filter (NON-NEGOTIABLE)
Trade ONLY from approved universe

From your screenshots:
✅ Must satisfy:
   - Symbol in allowed list (PNB, SBIN, ICICIBANK, BANKNIFTY, FINNIFTY...)
   - Option lot size allows ₹400-₹600 profit (UPDATED: All lot sizes equal per your feedback)
   - Underlying volume "actively printing" (no pauses)

❌ Block if:
   - Not in whitelist
   - Volume dried up
"""

import pandas as pd
import numpy as np
from datetime import datetime
import time
from .base_filter import BaseFilter
from typing import Dict, Any, List

class StockEligibilityFilter(BaseFilter):
    """
    Checks if stock is in approved universe and shows active volume
    """
    
    def __init__(self, allowed_symbols: List[str] = None):
        super().__init__("Stock Eligibility Filter")
        
        # Default allowed universe (from your screenshots + expansion)
        if allowed_symbols is None:
            self.allowed_symbols = [
                "PNB", "SBIN", "ICICIBANK", "HDFCBANK", "AXISBANK",
                "INFY", "TCS", "WIPRO", "TECHM",
                "RELIANCE", "ONGC", "COALINDIA",
                "TATASTEEL", "TATAMOTORS", "JSWSTEEL", "HINDALCO",
                "BHARTIARTL", "ITC", "SUNPHARMA", "DRREDDY",
                "M&M", "LT", "BANKNIFTY", "FINNIFTY"
            ]
        else:
            self.allowed_symbols = allowed_symbols
        
        # Volume thresholds
        self.min_volume_1m = 50000  # Minimum 1-min volume
        self.volume_ma_periods = 20  # MA period for volume check
    
    def check(self, symbol: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check if stock is eligible for trading
        
        Args:
            data must contain:
                'df': DataFrame with Volume column
                'lot_size': Option lot size (for reference, not filtering)
                'spot_price': Current spot price (optional)
        """
        start_time = time.time()
        
        try:
            # Check 1: Symbol in whitelist
            symbol_ok = symbol in self.allowed_symbols
            
            if not symbol_ok:
                result = {
                    'passed': False,
                    'reason': f'{symbol} not in approved universe',
                    'metrics': {
                        'symbol': symbol,
                        'in_whitelist': False
                    },
                    'timestamp': datetime.now()
                }
                self._record_result(False, (time.time() - start_time) * 1000)
                return result
            
            # Check 2: Volume actively printing
            df = data.get('df')
            if df is None or len(df) < self.volume_ma_periods:
                result = {
                    'passed': False,
                    'reason': 'Insufficient volume data',
                    'metrics': {'symbol': symbol},
                    'timestamp': datetime.now()
                }
                self._record_result(False, (time.time() - start_time) * 1000)
                return result
            
            # Calculate volume metrics
            recent_volume = df['Volume'].tail(5)
            avg_volume = df['Volume'].rolling(self.volume_ma_periods).mean().iloc[-1]
            current_volume = df['Volume'].iloc[-1]
            
            # Check if volume is "actively printing"
            # 1. Current volume should be reasonable (not zero or extremely low)
            # 2. Recent avg should be above minimum threshold
            volume_ok = (
                current_volume > 0 and
                avg_volume > self.min_volume_1m and
                recent_volume.min() > 0  # No zero-volume candles in last 5
            )
            
            # Get lot size for info (not filtering on it per your feedback)
            lot_size = data.get('lot_size', 0)
            spot_price = data.get('spot_price', 0)
            
            if not volume_ok:
                reason = f"Volume dried up (avg: {avg_volume:.0f} < {self.min_volume_1m})"
            else:
                reason = f"{symbol} eligible - actively printing"
            
            result = {
                'passed': symbol_ok and volume_ok,
                'reason': reason,
                'metrics': {
                    'symbol': symbol,
                    'in_whitelist': symbol_ok,
                    'current_volume': int(current_volume),
                    'avg_volume': int(avg_volume),
                    'lot_size': lot_size,
                    'spot_price': round(spot_price, 2) if spot_price else 0
                },
                'timestamp': datetime.now()
            }
            
            self._record_result(result['passed'], (time.time() - start_time) * 1000)
            return result
            
        except Exception as e:
            result = {
                'passed': False,
                'reason': f'Error in eligibility filter: {str(e)}',
                'metrics': {'symbol': symbol},
                'timestamp': datetime.now()
            }
            self._record_result(False, (time.time() - start_time) * 1000)
            return result
    
    def add_symbol(self, symbol: str):
        """Add a symbol to the allowed list"""
        if symbol not in self.allowed_symbols:
            self.allowed_symbols.append(symbol)
            print(f"✅ Added {symbol} to allowed universe")
    
    def remove_symbol(self, symbol: str):
        """Remove a symbol from the allowed list"""
        if symbol in self.allowed_symbols:
            self.allowed_symbols.remove(symbol)
            print(f"❌ Removed {symbol} from allowed universe")
    
    def get_universe(self) -> List[str]:
        """Get current allowed universe"""
        return self.allowed_symbols.copy()

if __name__ == "__main__":
    # Test the filter
    print("🧪 Testing Stock Eligibility Filter...")
    
    # Create sample data
    dates = pd.date_range('2024-01-01 09:15', periods=30, freq='1min')
    sample_df = pd.DataFrame({
        'Open': np.random.uniform(100, 110, 30),
        'High': np.random.uniform(105, 115, 30),
        'Low': np.random.uniform(95, 105, 30),
        'Close': np.random.uniform(100, 110, 30),
        'Volume': np.random.uniform(80000, 120000, 30)
    }, index=dates)
    
    filter = StockEligibilityFilter()
    
    # Test 1: Allowed symbol
    result = filter.check("PNB", {
        'df': sample_df,
        'lot_size': 5000,
        'spot_price': 120.5
    })
    print(f"\nTest 1 - PNB (should pass):")
    print(f"  Passed: {result['passed']}")
    print(f"  Reason: {result['reason']}")
    
    # Test 2: Not allowed symbol
    result = filter.check("RANDOM", {
        'df': sample_df,
        'lot_size': 1000,
        'spot_price': 50.0
    })
    print(f"\nTest 2 - RANDOM (should fail):")
    print(f"  Passed: {result['passed']}")
    print(f"  Reason: {result['reason']}")
    
    print(f"\nFilter Stats: {filter.get_stats()}")
    print(f"Universe size: {len(filter.get_universe())} stocks") 
