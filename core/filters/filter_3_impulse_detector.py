"""
Filter 3: Underlying Impulse Detector (CORE EDGE)
Detect high-probability impulse moments on 1-minute chart

From your screenshots:
ALL conditions must be TRUE on the SAME candle:

✅ Structural level:
   - Break/reclaim of VWAP OR Opening High/Low

✅ Candle strength:
   - Body ≥ 0.6 × ATR(1m)

✅ Volume:
   - ≥ 1.5× average of last 5 candles

✅ Speed:
   - Move completes in ≤ 2 candles

❌ If ANY fails → NO TRADE
This eliminates fake breakouts.
"""

import pandas as pd
import numpy as np
from datetime import datetime
import time
from .base_filter import BaseFilter
from typing import Dict, Any

class ImpulseDetector(BaseFilter):
    """
    Detects high-momentum impulse candles on underlying stock
    """
    
    def __init__(self):
        super().__init__("Impulse Detector")
        
        # Thresholds from your screenshots
        self.candle_strength_multiplier = 0.6  # Body ≥ 0.6 × ATR
        self.volume_spike_multiplier = 1.5     # Volume ≥ 1.5× avg
        self.max_impulse_candles = 2           # Complete in ≤2 candles
        self.atr_period = 14
    
    def check(self, symbol: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check if current candle shows impulse characteristics
        
        Args:
            data must contain:
                'df': DataFrame with [Open, High, Low, Close, Volume, ATR]
                'vwap': Current VWAP (optional)
                'opening_high': Day's opening high (optional)
                'opening_low': Day's opening low (optional)
        """
        start_time = time.time()
        
        try:
            df = data.get('df')
            if df is None or len(df) < 10:
                result = {
                    'passed': False,
                    'reason': 'Insufficient data for impulse detection',
                    'metrics': {},
                    'timestamp': datetime.now()
                }
                self._record_result(False, (time.time() - start_time) * 1000)
                return result
            
            # Calculate ATR if not present
            if 'ATR' not in df.columns:
                df['TR'] = np.maximum(
                    df['High'] - df['Low'],
                    np.maximum(
                        abs(df['High'] - df['Close'].shift(1)),
                        abs(df['Low'] - df['Close'].shift(1))
                    )
                )
                df['ATR'] = df['TR'].rolling(self.atr_period).mean()
            
            # Get current candle
            current = df.iloc[-1]
            prev = df.iloc[-2]
            
            current_atr = current['ATR']
            if pd.isna(current_atr) or current_atr == 0:
                result = {
                    'passed': False,
                    'reason': 'ATR not available',
                    'metrics': {},
                    'timestamp': datetime.now()
                }
                self._record_result(False, (time.time() - start_time) * 1000)
                return result
            
            # Check 1: Structural level break
            vwap = data.get('vwap')
            opening_high = data.get('opening_high')
            opening_low = data.get('opening_low')
            
            structural_break = False
            break_type = None
            
            if vwap is not None:
                # Check VWAP break/reclaim
                prev_close = prev['Close']
                curr_close = current['Close']
                
                if prev_close < vwap and curr_close > vwap:
                    structural_break = True
                    break_type = "VWAP reclaim"
                elif prev_close > vwap and curr_close < vwap:
                    structural_break = True
                    break_type = "VWAP break down"
            
            if not structural_break and opening_high is not None and opening_low is not None:
                # Check opening range break
                curr_high = current['High']
                curr_low = current['Low']
                
                if curr_high > opening_high:
                    structural_break = True
                    break_type = "Opening High break"
                elif curr_low < opening_low:
                    structural_break = True
                    break_type = "Opening Low break"
            
            if not structural_break:
                # Fallback: Check if breaking recent swing
                recent_high = df['High'].tail(5).max()
                recent_low = df['Low'].tail(5).min()
                
                if current['High'] > recent_high:
                    structural_break = True
                    break_type = "Recent swing high break"
                elif current['Low'] < recent_low:
                    structural_break = True
                    break_type = "Recent swing low break"
            
            # Check 2: Candle strength (Body ≥ 0.6 × ATR)
            candle_body = abs(current['Close'] - current['Open'])
            required_body = current_atr * self.candle_strength_multiplier
            candle_strength_ok = candle_body >= required_body
            
            # Check 3: Volume spike (≥ 1.5× avg of last 5)
            recent_volumes = df['Volume'].tail(6).iloc[:-1]  # Last 5 excluding current
            avg_volume = recent_volumes.mean()
            current_volume = current['Volume']
            
            if avg_volume > 0:
                volume_spike_ok = current_volume >= (avg_volume * self.volume_spike_multiplier)
            else:
                volume_spike_ok = False
            
            # Check 4: Speed (impulse in ≤2 candles)
            # Check if current + previous candles show sustained move
            last_2_candles = df.tail(2)
            direction = 1 if current['Close'] > current['Open'] else -1
            
            # Both candles should be in same direction
            prev_direction = 1 if prev['Close'] > prev['Open'] else -1
            speed_ok = (direction == prev_direction)
            
            # Combined result
            passed = structural_break and candle_strength_ok and volume_spike_ok and speed_ok
            
            if not passed:
                reasons = []
                if not structural_break:
                    reasons.append("No structural level break")
                if not candle_strength_ok:
                    reasons.append(f"Weak candle (body {candle_body:.2f} < {required_body:.2f})")
                if not volume_spike_ok:
                    reasons.append(f"No volume spike ({current_volume:.0f} < {avg_volume*self.volume_spike_multiplier:.0f})")
                if not speed_ok:
                    reasons.append("Slow move (not sustained)")
                
                reason = " | ".join(reasons)
            else:
                reason = f"IMPULSE DETECTED: {break_type}"
            
            result = {
                'passed': passed,
                'reason': reason,
                'metrics': {
                    'structural_break': structural_break,
                    'break_type': break_type,
                    'candle_body': round(candle_body, 2),
                    'required_body': round(required_body, 2),
                    'volume': int(current_volume),
                    'avg_volume': int(avg_volume),
                    'volume_ratio': round(current_volume / (avg_volume + 1), 2),
                    'atr': round(current_atr, 2)
                },
                'timestamp': datetime.now()
            }
            
            self._record_result(passed, (time.time() - start_time) * 1000)
            return result
            
        except Exception as e:
            result = {
                'passed': False,
                'reason': f'Error in impulse detector: {str(e)}',
                'metrics': {},
                'timestamp': datetime.now()
            }
            self._record_result(False, (time.time() - start_time) * 1000)
            return result

if __name__ == "__main__":
    # Test the filter
    print("🧪 Testing Impulse Detector...")
    
    # Create sample data with impulse
    dates = pd.date_range('2024-01-01 09:15', periods=20, freq='1min')
    
    # Normal candles
    opens = np.random.uniform(100, 102, 20)
    closes = opens + np.random.uniform(-0.5, 0.5, 20)
    highs = np.maximum(opens, closes) + np.random.uniform(0.1, 0.3, 20)
    lows = np.minimum(opens, closes) - np.random.uniform(0.1, 0.3, 20)
    volumes = np.random.uniform(50000, 70000, 20)
    
    # Add impulse on last candle
    opens[-1] = 101.0
    closes[-1] = 103.0  # Strong bullish candle
    highs[-1] = 103.2
    lows[-1] = 100.9
    volumes[-1] = 150000  # Volume spike
    
    sample_df = pd.DataFrame({
        'Open': opens,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': volumes
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
    
    filter = ImpulseDetector()
    result = filter.check("PNB", {
        'df': sample_df,
        'vwap': 101.5,
        'opening_high': 102.0,
        'opening_low': 100.0
    })
    
    print(f"Passed: {result['passed']}")
    print(f"Reason: {result['reason']}")
    print(f"Metrics: {result['metrics']}")
    print(f"Stats: {filter.get_stats()}") 
