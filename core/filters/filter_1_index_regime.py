 
"""
Filter 1: Index Regime Filter (MANDATORY)
Trade ONLY if Nifty/BankNifty satisfies momentum conditions

From your screenshots:
✅ Required:
   - ATR(1m) above its 20-period average
   - No overlapping doji candles (last 5 bars)
   - Directional movement visible (not flat VWAP)

❌ Block if:
   - ATR compressed
   - Sideways chop around VWAP
   - News spike already exhausted
"""

import pandas as pd
import numpy as np
from datetime import datetime
import time
from .base_filter import BaseFilter
from typing import Dict, Any

class IndexRegimeFilter(BaseFilter):
    """
    Checks if index (Nifty/BankNifty) shows favorable regime
    """
    
    def __init__(self):
        super().__init__("Index Regime Filter")
        self.atr_period = 14
        self.atr_ma_period = 20
        self.max_doji_candles = 2
        self.directional_threshold = 0.3  # ATR multiplier
    
    def check(self, symbol: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check if index shows favorable regime
        
        Args:
            data must contain:
                'index_df': DataFrame with columns [Open, High, Low, Close, ATR]
                'index_name': 'NIFTY' or 'BANKNIFTY'
        """
        start_time = time.time()
        
        try:
            index_df = data.get('index_df')
            index_name = data.get('index_name', 'NIFTY')
            
            if index_df is None or len(index_df) < self.atr_ma_period:
                result = {
                    'passed': False,
                    'reason': 'Insufficient index data',
                    'metrics': {},
                    'timestamp': datetime.now()
                }
                self._record_result(False, (time.time() - start_time) * 1000)
                return result
            
            # Get latest candles
            recent = index_df.tail(5).copy()
            
            # Check 1: ATR above its MA20
            if 'ATR' not in index_df.columns:
                # Calculate ATR if not present
                index_df['TR'] = np.maximum(
                    index_df['High'] - index_df['Low'],
                    np.maximum(
                        abs(index_df['High'] - index_df['Close'].shift(1)),
                        abs(index_df['Low'] - index_df['Close'].shift(1))
                    )
                )
                index_df['ATR'] = index_df['TR'].rolling(self.atr_period).mean()
            
            current_atr = index_df['ATR'].iloc[-1]
            atr_ma20 = index_df['ATR'].rolling(self.atr_ma_period).mean().iloc[-1]
            atr_ok = current_atr > atr_ma20
            
            # Check 2: Count doji candles in last 5 bars
            recent['body'] = abs(recent['Close'] - recent['Open'])
            recent['range'] = recent['High'] - recent['Low']
            recent['is_doji'] = recent['body'] < (recent['range'] * 0.2)  # Body < 20% of range
            
            doji_count = recent['is_doji'].sum()
            doji_ok = doji_count <= self.max_doji_candles
            
            # Check 3: Directional movement (not flat)
            # Check if Close is moving away from VWAP or Opening High/Low
            if 'VWAP' in index_df.columns:
                vwap = index_df['VWAP'].iloc[-1]
                close = index_df['Close'].iloc[-1]
                opening_level = index_df['Open'].iloc[0]  # First candle of day
                
                distance_from_vwap = abs(close - vwap)
                directional_ok = distance_from_vwap > (current_atr * self.directional_threshold)
            else:
                # Fallback: Check if recent candles show trend
                closes = recent['Close'].values
                trend_strength = abs(closes[-1] - closes[0]) / (current_atr + 0.01)
                directional_ok = trend_strength > self.directional_threshold
            
            # Combined result
            passed = atr_ok and doji_ok and directional_ok
            
            if not passed:
                reasons = []
                if not atr_ok:
                    reasons.append(f"ATR compressed ({current_atr:.2f} <= {atr_ma20:.2f})")
                if not doji_ok:
                    reasons.append(f"Too many doji ({doji_count} > {self.max_doji_candles})")
                if not directional_ok:
                    reasons.append("Sideways chop around VWAP")
                
                reason = " | ".join(reasons)
            else:
                reason = f"{index_name} regime favorable"
            
            result = {
                'passed': passed,
                'reason': reason,
                'metrics': {
                    'index': index_name,
                    'atr': round(current_atr, 2),
                    'atr_ma20': round(atr_ma20, 2),
                    'doji_count': int(doji_count),
                    'directional': directional_ok
                },
                'timestamp': datetime.now()
            }
            
            self._record_result(passed, (time.time() - start_time) * 1000)
            return result
            
        except Exception as e:
            result = {
                'passed': False,
                'reason': f'Error in index filter: {str(e)}',
                'metrics': {},
                'timestamp': datetime.now()
            }
            self._record_result(False, (time.time() - start_time) * 1000)
            return result

if __name__ == "__main__":
    # Test the filter
    print("🧪 Testing Index Regime Filter...")
    
    # Create sample data
    dates = pd.date_range('2024-01-01 09:15', periods=30, freq='1min')
    sample_df = pd.DataFrame({
        'Open': np.random.uniform(22000, 22100, 30),
        'High': np.random.uniform(22050, 22150, 30),
        'Low': np.random.uniform(21950, 22050, 30),
        'Close': np.random.uniform(22000, 22100, 30),
    }, index=dates)
    
    # Calculate ATR
    sample_df['TR'] = np.maximum(
        sample_df['High'] - sample_df['Low'],
        np.maximum(
            abs(sample_df['High'] - sample_df['Close'].shift(1)),
            abs(sample_df['Low'] - sample_df['Close'].shift(1))
        )
    )
    sample_df['ATR'] = sample_df['TR'].rolling(14).mean()
    
    filter = IndexRegimeFilter()
    result = filter.check("TEST", {
        'index_df': sample_df,
        'index_name': 'NIFTY'
    })
    
    print(f"Passed: {result['passed']}")
    print(f"Reason: {result['reason']}")
    print(f"Metrics: {result['metrics']}")
    print(f"Stats: {filter.get_stats()}")