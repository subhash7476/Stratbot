"""
Filter 5: ₹500 Feasibility Check (FINAL GATE)
Verify that ₹500 profit is realistic and easy

From your screenshots:
Before entry, system checks:

expected_option_move >= 0.06  # Based on underlying ATR
AND
option_ATR(1m) >= 0.04        # Option itself is volatile enough

If FALSE → BLOCK
This ensures ₹500 is easy, not forced.
"""

import pandas as pd
import numpy as np
from datetime import datetime
import time
from .base_filter import BaseFilter
from typing import Dict, Any

class FeasibilityFilter(BaseFilter):
    """
    Confirms ₹500 profit target is achievable with current conditions
    """
    
    def __init__(self):
        super().__init__("₹500 Feasibility Check")
        
        # Thresholds from your screenshots
        self.min_expected_option_move = 0.06  # Minimum expected option move
        self.min_option_atr = 0.04            # Option must be volatile enough
        self.target_profit = 500              # Your target
        self.option_delta = 0.45              # Assume ATM delta
    
    def check(self, symbol: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check if ₹500 is realistically achievable
        
        Args:
            data must contain:
                'spot_price': Current spot price
                'stock_atr': Stock's ATR(1m)
                'option_premium': Current option premium
                'option_atr': Option's ATR(1m)
                'lot_size': Option lot size
                'expected_move_pct': Expected % move (default 0.5%)
        """
        start_time = time.time()
        
        try:
            # Get required data
            spot_price = data.get('spot_price')
            stock_atr = data.get('stock_atr')
            option_premium = data.get('option_premium')
            option_atr = data.get('option_atr')
            lot_size = data.get('lot_size')
            expected_move_pct = data.get('expected_move_pct', 0.5)  # Default 0.5%
            
            # Validate data
            if None in [spot_price, stock_atr, option_premium, lot_size]:
                result = {
                    'passed': False,
                    'reason': 'Missing data for feasibility check',
                    'metrics': {},
                    'timestamp': datetime.now()
                }
                self._record_result(False, (time.time() - start_time) * 1000)
                return result
            
            # Calculate expected stock move in ₹
            expected_stock_move = spot_price * (expected_move_pct / 100)
            
            # Calculate expected option move
            # Option move ≈ Stock move × Delta
            expected_option_move = expected_stock_move * self.option_delta
            
            # Calculate expected profit
            expected_profit = expected_option_move * lot_size
            
            # Check 1: Expected move is sufficient
            move_sufficient = expected_option_move >= self.min_expected_option_move
            
            # Check 2: Option ATR is sufficient
            if option_atr is not None:
                atr_sufficient = option_atr >= self.min_option_atr
            else:
                # Estimate option ATR from stock ATR if not available
                estimated_option_atr = stock_atr * self.option_delta
                atr_sufficient = estimated_option_atr >= self.min_option_atr
                option_atr = estimated_option_atr
            
            # Check 3: Expected profit meets ₹500 target
            profit_achievable = expected_profit >= self.target_profit
            
            # Combined result
            passed = move_sufficient and atr_sufficient and profit_achievable
            
            if not passed:
                reasons = []
                if not move_sufficient:
                    reasons.append(f"Expected move too small (₹{expected_option_move:.2f} < ₹{self.min_expected_option_move})")
                if not atr_sufficient:
                    reasons.append(f"Option ATR too low (₹{option_atr:.3f} < ₹{self.min_option_atr})")
                if not profit_achievable:
                    reasons.append(f"Expected profit below target (₹{expected_profit:.0f} < ₹{self.target_profit})")
                
                reason = " | ".join(reasons)
            else:
                reason = f"₹500 feasible (expected: ₹{expected_profit:.0f})"
            
            result = {
                'passed': passed,
                'reason': reason,
                'metrics': {
                    'spot_price': round(spot_price, 2),
                    'stock_atr': round(stock_atr, 2),
                    'option_premium': round(option_premium, 2),
                    'option_atr': round(option_atr, 3),
                    'lot_size': lot_size,
                    'expected_stock_move': round(expected_stock_move, 2),
                    'expected_option_move': round(expected_option_move, 2),
                    'expected_profit': round(expected_profit, 0),
                    'target_profit': self.target_profit
                },
                'timestamp': datetime.now()
            }
            
            self._record_result(passed, (time.time() - start_time) * 1000)
            return result
            
        except Exception as e:
            result = {
                'passed': False,
                'reason': f'Error in feasibility filter: {str(e)}',
                'metrics': {},
                'timestamp': datetime.now()
            }
            self._record_result(False, (time.time() - start_time) * 1000)
            return result

if __name__ == "__main__":
    # Test the filter
    print("🧪 Testing ₹500 Feasibility Filter...")
    
    filter = FeasibilityFilter()
    
    # Test 1: PNB - High lot size (should easily pass)
    result = filter.check("PNB", {
        'spot_price': 120.37,
        'stock_atr': 0.50,
        'option_premium': 0.56,
        'option_atr': 0.08,
        'lot_size': 5000,
        'expected_move_pct': 0.5
    })
    print(f"\nTest 1 - PNB (high lot):")
    print(f"  Passed: {result['passed']}")
    print(f"  Reason: {result['reason']}")
    print(f"  Expected profit: ₹{result['metrics']['expected_profit']:.0f}")
    
    # Test 2: HDFCBANK - Lower lot size
    result = filter.check("HDFCBANK", {
        'spot_price': 992.10,
        'stock_atr': 3.50,
        'option_premium': 3.80,
        'option_atr': 0.15,
        'lot_size': 550,
        'expected_move_pct': 0.5
    })
    print(f"\nTest 2 - HDFCBANK (lower lot):")
    print(f"  Passed: {result['passed']}")
    print(f"  Reason: {result['reason']}")
    print(f"  Expected profit: ₹{result['metrics']['expected_profit']:.0f}")
    
    # Test 3: Low volatility scenario (should fail)
    result = filter.check("LOWVOL", {
        'spot_price': 100.0,
        'stock_atr': 0.20,  # Very low ATR
        'option_premium': 0.50,
        'option_atr': 0.02,  # Very low option ATR
        'lot_size': 1000,
        'expected_move_pct': 0.3
    })
    print(f"\nTest 3 - Low volatility (should fail):")
    print(f"  Passed: {result['passed']}")
    print(f"  Reason: {result['reason']}")
    
    print(f"\nFilter Stats: {filter.get_stats()}") 
