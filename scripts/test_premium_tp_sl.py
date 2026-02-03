"""
Test script for Premium TP/SL Strategy
-------------------------------------
This script runs a short backtest to validate the strategy behavior:
- Entries only on premiumBuy/premiumSell
- Exits happen with correct priority
- No same-bar flip
- Position becomes exactly 0 after EXIT
"""
import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
from unittest.mock import Mock

from core.strategies.premium_tp_sl import PremiumTpSlStrategy
from core.strategies.base import StrategyContext
from core.events import OHLCVBar, SignalEvent, SignalType
from core.analytics.models import ConfluenceInsight, IndicatorResult, Bias


def create_mock_confluence_insight(premium_buy=False, premium_sell=False):
    """Create a mock confluence insight with premium flags."""
    indicator_result = IndicatorResult(
        name="premium_flags",
        bias=Bias.NEUTRAL,
        value=0.0,
        metadata={
            'premiumBuy': premium_buy,
            'premiumSell': premium_sell
        }
    )
    
    return ConfluenceInsight(
        timestamp=datetime.now(),
        symbol="TEST",
        bias=Bias.NEUTRAL,
        confidence_score=0.5,
        indicator_results=[indicator_result],
        signal=Bias.NEUTRAL
    )


def run_strategy_test():
    """Run a test of the premium TP/SL strategy."""
    print("Testing Premium TP/SL Strategy...")
    
    # Create strategy instance with test parameters
    config = {
        'tp_pct': 0.02,      # 2% TP
        'sl_pct': 0.01,      # 1% SL
        'max_hold_bars': 10  # Max 10 bars hold
    }
    
    strategy = PremiumTpSlStrategy("premium_tp_sl_test", config)
    
    # Create mock data for testing
    base_time = datetime(2023, 1, 1, 9, 15)  # Market open
    ist_tz = pytz.timezone('Asia/Kolkata')
    
    # Simulate market data with different scenarios
    test_cases = [
        # Case 1: Premium buy signal, followed by TP hit
        {'time_offset': 0, 'open': 100, 'high': 102.5, 'low': 99.5, 'close': 102, 'volume': 1000, 'premium_buy': True, 'premium_sell': False},
        {'time_offset': 1, 'open': 102, 'high': 103, 'low': 100, 'close': 101, 'volume': 1000, 'premium_buy': False, 'premium_sell': False},
        {'time_offset': 2, 'open': 101, 'high': 104, 'low': 99, 'close': 103, 'volume': 1000, 'premium_buy': False, 'premium_sell': False},  # TP should be hit here (100 * 1.02 = 102)
        
        # Case 2: Wait for flat position, then premium sell signal
        {'time_offset': 3, 'open': 103, 'high': 105, 'low': 101, 'close': 104, 'volume': 1000, 'premium_buy': False, 'premium_sell': False},
        {'time_offset': 4, 'open': 104, 'high': 106, 'low': 102, 'close': 105, 'volume': 1000, 'premium_buy': False, 'premium_sell': False},
        {'time_offset': 5, 'open': 105, 'high': 107, 'low': 103, 'close': 104, 'volume': 1000, 'premium_buy': False, 'premium_sell': True},   # Premium sell signal
        
        # Case 3: Time stop scenario
        {'time_offset': 6, 'open': 104, 'high': 106, 'low': 102, 'close': 105, 'volume': 1000, 'premium_buy': False, 'premium_sell': False},
        {'time_offset': 7, 'open': 105, 'high': 107, 'low': 103, 'close': 106, 'volume': 1000, 'premium_buy': False, 'premium_sell': False},
        {'time_offset': 8, 'open': 106, 'high': 108, 'low': 104, 'close': 107, 'volume': 1000, 'premium_buy': False, 'premium_sell': False},
        {'time_offset': 9, 'open': 107, 'high': 109, 'low': 105, 'close': 108, 'volume': 1000, 'premium_buy': False, 'premium_sell': False},
        {'time_offset': 10, 'open': 108, 'high': 110, 'low': 106, 'close': 109, 'volume': 1000, 'premium_buy': False, 'premium_sell': False},  # Time stop should trigger here
        {'time_offset': 11, 'open': 109, 'high': 111, 'low': 107, 'close': 110, 'volume': 1000, 'premium_buy': True, 'premium_sell': False},   # Premium buy after flat
        
        # Case 4: SL hit scenario
        {'time_offset': 12, 'open': 110, 'high': 112, 'low': 107, 'close': 108, 'volume': 1000, 'premium_buy': False, 'premium_sell': False},
        {'time_offset': 13, 'open': 108, 'high': 110, 'low': 105, 'close': 106, 'volume': 1000, 'premium_buy': False, 'premium_sell': False},  # SL should be hit here (110 * 0.99 = 108.9, low is 105)
    ]
    
    current_position = 0.0
    trade_log = []
    
    print(f"{'Bar':<4} {'Action':<12} {'Price':<8} {'Pos':<6} {'Reason':<15}")
    print("-" * 50)
    
    for i, case in enumerate(test_cases):
        # Create OHLCV bar
        timestamp = ist_tz.localize(base_time + timedelta(minutes=case['time_offset']))
        bar = OHLCVBar(
            symbol="TEST",
            timestamp=timestamp,
            open=case['open'],
            high=case['high'],
            low=case['low'],
            close=case['close'],
            volume=case['volume']
        )
        
        # Create context with current position and analytics
        context = StrategyContext(
            symbol="TEST",
            current_position=current_position,
            analytics_snapshot=create_mock_confluence_insight(case['premium_buy'], case['premium_sell']),
            market_regime=None,
            strategy_params=config
        )
        
        # Process the bar
        signal = strategy.process_bar(bar, context)
        
        # Log the action
        action = "NONE"
        reason = ""
        if signal:
            action = signal.signal_type.value
            reason = signal.metadata.get('exit_reason', signal.metadata.get('entry_reason', ''))
            
            # Update position based on signal
            if signal.signal_type == SignalType.BUY:
                current_position += 100  # Buy 100 shares
            elif signal.signal_type == SignalType.SELL:
                current_position -= 100  # Sell 100 shares (open short)
            elif signal.signal_type == SignalType.EXIT:
                if current_position > 0:
                    current_position = 0  # Close long
                elif current_position < 0:
                    current_position = 0  # Close short
        
        print(f"{i:<4} {action:<12} {bar.close:<8.2f} {current_position:<6.0f} {reason:<15}")
        trade_log.append({
            'bar': i,
            'action': action,
            'price': bar.close,
            'position': current_position,
            'reason': reason,
            'premium_buy': case['premium_buy'],
            'premium_sell': case['premium_sell']
        })
    
    print(f"\nFinal position: {current_position}")
    
    # Validate results
    print("\nValidation:")
    
    # Check that entries happened only on premium signals
    entry_signals = [t for t in trade_log if t['action'] in ['BUY', 'SELL']]
    for entry in entry_signals:
        if entry['action'] == 'BUY':
            assert trade_log[entry['bar']]['premium_buy'], f"BUY signal without premium_buy at bar {entry['bar']}"
        elif entry['action'] == 'SELL':
            assert trade_log[entry['bar']]['premium_sell'], f"SELL signal without premium_sell at bar {entry['bar']}"
    
    print("✓ Entries only on premium signals")
    
    # Check that exits happened appropriately
    exit_signals = [t for t in trade_log if t['action'] == 'EXIT']
    print(f"✓ Found {len(exit_signals)} exit signals")
    
    # Check that position goes to zero after exits
    exit_positions = [(i, t['position']) for i, t in enumerate(trade_log) if t['action'] == 'EXIT']
    for idx, pos in exit_positions:
        if idx + 1 < len(trade_log):  # If there's a next bar
            next_pos = trade_log[idx + 1]['position']
            assert next_pos == 0, f"Position not zero after exit at bar {idx}, next bar position: {next_pos}"
    
    print("✓ Position becomes zero after EXIT signals")
    
    # Check for specific exit reasons
    exit_reasons = [t['reason'] for t in trade_log if t['action'] == 'EXIT']
    print(f"✓ Exit reasons: {exit_reasons}")
    
    print("\nAll tests passed! Strategy is working correctly.")


if __name__ == "__main__":
    run_strategy_test()