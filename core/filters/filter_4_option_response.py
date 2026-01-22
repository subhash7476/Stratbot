"""
Filter 4: Option Response Confirmation (MOST IMPORTANT)
Verify that options are REACTING to underlying impulse

From your screenshots:
After underlying impulse, option MUST show:
✅ Immediate LTP reaction (within seconds)
✅ Bid stepping up, not just last price
✅ No widening spread
✅ No hesitation ticks

❌ If stock moves but option lags → SKIP TRADE
No repricing = no edge.
"""

import pandas as pd
import numpy as np
from datetime import datetime
import time
from .base_filter import BaseFilter
from typing import Dict, Any

class OptionResponseFilter(BaseFilter):
    """
    Confirms option is responding to underlying movement
    """
    
    def __init__(self):
        super().__init__("Option Response Confirmation")
        
        # Thresholds from your screenshots
        self.ltp_change_window_seconds = 5  # Check change in 5 seconds
        self.min_ltp_change_percent = 0      # Any positive change
        self.max_spread_ticks = 1            # Bid-ask spread ≤ 1 tick
    
    def check(self, symbol: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check if option is showing immediate response
        
        Args:
            data must contain:
                'option_ltp': Current LTP
                'option_ltp_prev': LTP 5 seconds ago
                'option_bid': Current bid
                'option_bid_prev': Previous bid
                'option_ask': Current ask
                'tick_size': Option tick size (usually ₹0.05)
                'underlying_move_pct': Underlying % move
        """
        start_time = time.time()
        
        try:
            # Get option data
            option_ltp = data.get('option_ltp')
            option_ltp_prev = data.get('option_ltp_prev')
            option_bid = data.get('option_bid')
            option_bid_prev = data.get('option_bid_prev')
            option_ask = data.get('option_ask')
            tick_size = data.get('tick_size', 0.05)
            underlying_move_pct = data.get('underlying_move_pct', 0)
            
            # Validate data
            if None in [option_ltp, option_ltp_prev, option_bid, option_ask]:
                result = {
                    'passed': False,
                    'reason': 'Option data not available',
                    'metrics': {},
                    'timestamp': datetime.now()
                }
                self._record_result(False, (time.time() - start_time) * 1000)
                return result
            
            # Check 1: Immediate LTP reaction
            ltp_change_pct = ((option_ltp - option_ltp_prev) / (option_ltp_prev + 0.01)) * 100
            ltp_reacting = ltp_change_pct > self.min_ltp_change_percent
            
            # Check 2: Bid stepping up (not just last price)
            if option_bid_prev is not None:
                bid_stepping = option_bid > option_bid_prev
            else:
                # Fallback: Check if bid is close to LTP (shows interest)
                bid_stepping = (option_ltp - option_bid) <= (tick_size * 2)
            
            # Check 3: Spread stable (no widening)
            spread = option_ask - option_bid
            spread_ticks = spread / tick_size
            spread_ok = spread_ticks <= self.max_spread_ticks
            
            # Check 4: No hesitation (LTP continuously updating)
            # If we have timestamp data, check for gaps
            # For now, assume if LTP changed, it's updating
            no_hesitation = ltp_change_pct != 0
            
            # Combined result
            passed = ltp_reacting and bid_stepping and spread_ok and no_hesitation
            
            if not passed:
                reasons = []
                if not ltp_reacting:
                    reasons.append(f"LTP not reacting ({ltp_change_pct:.2f}%)")
                if not bid_stepping:
                    reasons.append("Bid not stepping up")
                if not spread_ok:
                    reasons.append(f"Spread too wide ({spread_ticks:.1f} ticks)")
                if not no_hesitation:
                    reasons.append("LTP frozen/hesitating")
                
                reason = " | ".join(reasons)
            else:
                reason = f"Option responding well (LTP +{ltp_change_pct:.2f}%)"
            
            result = {
                'passed': passed,
                'reason': reason,
                'metrics': {
                    'option_ltp': round(option_ltp, 2),
                    'ltp_change_pct': round(ltp_change_pct, 2),
                    'option_bid': round(option_bid, 2),
                    'option_ask': round(option_ask, 2),
                    'spread': round(spread, 2),
                    'spread_ticks': round(spread_ticks, 1),
                    'underlying_move_pct': round(underlying_move_pct, 2),
                    'bid_stepping': bid_stepping
                },
                'timestamp': datetime.now()
            }
            
            self._record_result(passed, (time.time() - start_time) * 1000)
            return result
            
        except Exception as e:
            result = {
                'passed': False,
                'reason': f'Error in option response filter: {str(e)}',
                'metrics': {},
                'timestamp': datetime.now()
            }
            self._record_result(False, (time.time() - start_time) * 1000)
            return result

if __name__ == "__main__":
    # Test the filter
    print("🧪 Testing Option Response Filter...")
    
    filter = OptionResponseFilter()
    
    # Test 1: Good response
    result = filter.check("PNB 121 CE", {
        'option_ltp': 0.58,
        'option_ltp_prev': 0.56,
        'option_bid': 0.57,
        'option_bid_prev': 0.56,
        'option_ask': 0.59,
        'tick_size': 0.05,
        'underlying_move_pct': 0.5
    })
    print(f"\nTest 1 - Good response:")
    print(f"  Passed: {result['passed']}")
    print(f"  Reason: {result['reason']}")
    print(f"  Metrics: {result['metrics']}")
    
    # Test 2: Lagging option
    result = filter.check("SLOW 100 CE", {
        'option_ltp': 0.56,
        'option_ltp_prev': 0.56,  # No change
        'option_bid': 0.54,
        'option_bid_prev': 0.54,
        'option_ask': 0.58,
        'tick_size': 0.05,
        'underlying_move_pct': 0.5
    })
    print(f"\nTest 2 - Lagging option (should fail):")
    print(f"  Passed: {result['passed']}")
    print(f"  Reason: {result['reason']}")
    
    print(f"\nFilter Stats: {filter.get_stats()}")