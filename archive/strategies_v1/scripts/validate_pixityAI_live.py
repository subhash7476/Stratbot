
"""
Verification Script for PixityAI Scanner Integration
----------------------------------------------------
Validates that the live resampling pipeline produces identical signals 
to the direct timeframe execution (backtest logic).

Scenario:
1. Generate synthetic 15m data with a known pattern (Trend Breakout).
2. Expand 15m data into 1m data.
3. Run Strategy directly on 15m data (Reference).
4. Run Strategy via ResamplingMarketDataProvider on 1m data (Live).
5. Compare signals.
"""
import sys
import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Optional
from pathlib import Path

# Fix path to allow importing core modules
sys.path.append(str(Path(__file__).parent.parent))

from core.events import OHLCVBar, SignalType
from core.strategies.pixityAIMetaStrategy import PixityAIMetaStrategy
from core.strategies.base import StrategyContext
from core.database.providers.base import MarketDataProvider
from core.database.providers.resampling_wrapper import ResamplingMarketDataProvider

# --- Mock Infrastructure ---

class MockMarketDataProvider(MarketDataProvider):
    def __init__(self, bars: List[OHLCVBar], symbols: List[str] = None):
        super().__init__(symbols or ["TEST"])
        self.bars = bars
        self.cursor = 0
    
    def get_next_bar(self, symbol: str) -> Optional[OHLCVBar]:
        if self.cursor < len(self.bars):
            bar = self.bars[self.cursor]
            self.cursor += 1
            return bar
        return None

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        if self.cursor > 0:
            return self.bars[self.cursor - 1]
        return None

    def is_data_available(self, symbol: str) -> bool:
        return self.cursor < len(self.bars)

    def reset(self, symbol: str):
        self.cursor = 0

    def get_progress(self, symbol: str) -> tuple:
        return (self.cursor, len(self.bars))

def make_bar(timestamp, open_, high, low, close, volume=1000):
    return OHLCVBar(
        symbol="TEST",
        timestamp=timestamp,
        open=float(open_), high=float(high), low=float(low), close=float(close),
        volume=int(volume)
    )

class TestPixityAIIntegration(unittest.TestCase):
    
    def setUp(self):
        self.start_time = datetime(2024, 1, 1, 9, 15)
        self.symbol = "TEST"
        self.context = StrategyContext(
            symbol=self.symbol,
            current_position=0,
            analytics_snapshot=None,
            market_regime=None,
            strategy_params={}
        )
        
        # Strategy Config (skip model to ensure raw signals are generated)
        self.config = {
            "lookback": 100,
            "swing_period": 3,
            "cooldown_bars": 0,
            "bar_minutes": 15,
            "model_path": "nonexistent" # Force no-model mode
        }

    def _generate_trend_pattern_15m(self):
        """Generates 60 bars of 15m data with a trend breakout at the end."""
        bars = []
        price = 100.0
        
        # 1. 50 bars of slow uptrend to warm up EMAs
        for i in range(50):
            timestamp = self.start_time + timedelta(minutes=15 * i)
            # Gentle trend up
            price += 0.5 
            b = make_bar(timestamp, price-0.2, price+0.5, price-0.5, price)
            bars.append(b)
            
        # 2. 5 bars of corrective pullback (create swing high)
        swing_high_price = price
        for i in range(50, 55):
            timestamp = self.start_time + timedelta(minutes=15 * i)
            price -= 0.3
            b = make_bar(timestamp, price+0.1, price+0.2, price-0.2, price)
            bars.append(b)
            
        # 3. 5 bars of consolidation
        for i in range(55, 60):
            timestamp = self.start_time + timedelta(minutes=15 * i)
            b = make_bar(timestamp, price-0.1, price+0.1, price-0.1, price)
            bars.append(b)
            
        # 4. Breakout bar (crossing previous swing high)
        timestamp = self.start_time + timedelta(minutes=15 * 60)
        # Jump above swing_high_price
        final_price = swing_high_price + 2.0
        b = make_bar(timestamp, price, final_price, price, final_price, volume=5000)
        bars.append(b)
        
        return bars

    def _expand_to_1m(self, bars_15m: List[OHLCVBar]) -> List[OHLCVBar]:
        """Expands 15m bars into 1m bars."""
        bars_1m = []
        for b_15 in bars_15m:
            base_ts = b_15.timestamp
            open_ = b_15.open
            close_ = b_15.close
            
            # Interpolate 15 steps
            for i in range(15):
                ts = base_ts + timedelta(minutes=i)
                # Linear interpolation
                curr_price = open_ + (close_ - open_) * (i / 14.0)
                
                # Add some noise to high/low to encompass the 15m high/low
                # We need to ensure that the aggregate 1m high/low matches 15m high/low
                # For simplicity, let's just make every 1m bar touch the 15m limits? 
                # No, that's unrealistic.
                # Let's just make the first 1m bar touch Open, last touch Close,
                # and distribute High/Low somewhere in between.
                
                h = curr_price + 0.05
                l = curr_price - 0.05
                
                # Force High/Low on specific minutes
                if i == 5: h = b_15.high
                if i == 10: l = b_15.low
                
                # Sanity check
                h = max(h, curr_price)
                l = min(l, curr_price)
                
                bars_1m.append(make_bar(ts, curr_price, h, l, curr_price, volume=b_15.volume / 15))
        
        return bars_1m

    def test_pipeline_equivalence(self):
        """Verify that streaming 1m bars yields same signals as using 15m bars directly."""
        
        # 1. Prepare Data
        bars_15m = self._generate_trend_pattern_15m()
        bars_1m = self._expand_to_1m(bars_15m)
        
        # Add a final dummy bar to flush the last 15m period in resampler
        last_ts = bars_15m[-1].timestamp
        flush_ts = last_ts + timedelta(minutes=15)
        bars_1m.append(make_bar(flush_ts, 0, 0, 0, 0)) # Just to trigger emission
        
        # 2. Run Reference (Backtest Mode)
        ref_strategy = PixityAIMetaStrategy("pixity_ref", self.config)
        ref_signals = []
        
        # Pre-feed to warm up internal state if needed (though process_bar handles it)
        # PixityAI needs 50 bars to start.
        for bar in bars_15m:
            sig = ref_strategy.process_bar(bar, self.context)
            if sig:
                ref_signals.append(sig)
        
        # 3. Run Live (Resampling Mode)
        live_strategy = PixityAIMetaStrategy("pixity_live", self.config)
        # Inject Mock Provider
        mock_provider = MockMarketDataProvider(bars_1m)
        resampler = ResamplingMarketDataProvider(mock_provider, target_tf="15m")
        live_signals = []
        
        while True:
            # Emulate the TradingRunner loop
            bar_15m = resampler.get_next_bar(self.symbol)
            if not bar_15m:
                if not mock_provider.is_data_available(self.symbol):
                    break
                continue
            
            sig = live_strategy.process_bar(bar_15m, self.context)
            if sig:
                live_signals.append(sig)
                
        # 4. Compare
        print(f"\nSignals Generated (Reference): {len(ref_signals)}")
        print(f"Signals Generated (Live):      {len(live_signals)}")
        
        self.assertEqual(len(ref_signals), len(live_signals), "Signal count mismatch")
        
        for ref, live in zip(ref_signals, live_signals):
            self.assertEqual(ref.timestamp, live.timestamp, "Timestamp mismatch")
            self.assertEqual(ref.signal_type, live.signal_type, "Signal type mismatch")
            self.assertEqual(ref.metadata['event_type'], live.metadata['event_type'], "Event Type mismatch")
            # Rounding differences possible due to float math on aggregated candles vs original?
            # They should be identical if 1m aggregation perfectly reconstructs the components used for calculation.
            # Actually, resampler rebuilds the bar. As long as input data is consistent, output is consistent.

            # Note: The timestamps might differ slightly depending on when the signal is emitted?
            # OHLCVBar timestamp is the open time.
            # PixityAI signal timestamp is the bar timestamp.
            # Correct.

if __name__ == "__main__":
    unittest.main()
