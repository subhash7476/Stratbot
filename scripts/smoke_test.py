import sys
import os
import json
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.clock import ReplayClock
from core.runner import TradingRunner, RunnerConfig
from core.events import OHLCVBar, SignalType, TradeStatus
from core.database.providers import MarketDataProvider, AnalyticsProvider
from core.execution.handler import ExecutionHandler, ExecutionConfig, ExecutionMode
from core.execution.position_tracker import PositionTracker
from core.brokers.paper_broker import PaperBroker
from core.strategies.registry import create_strategy

class MockDataProvider(MarketDataProvider):
    def __init__(self, symbols):
        super().__init__(symbols)
        self.count = 0
    def get_next_bar(self, symbol):
        if self.count >= 5: return None
        self.count += 1
        return OHLCVBar(symbol, datetime.now(), 100.0 + self.count, 105.0 + self.count, 95.0 + self.count, 102.0 + self.count, 1000)
    def get_latest_bar(self, symbol): return None
    def is_data_available(self, symbol): return self.count < 5
    def reset(self, symbol): self.count = 0
    def get_progress(self, symbol): return (self.count, 5)

class MockAnalyticsProvider(AnalyticsProvider):
    def get_latest_snapshot(self, symbol, as_of=None):
        from core.analytics.models import ConfluenceInsight, Bias, ConfluenceSignal
        return ConfluenceInsight(datetime.now(), symbol, Bias.BULLISH, 0.7, [], ConfluenceSignal.BUY)
    def get_market_regime(self, symbol, as_of=None):
        return {"regime": "BULL_TREND"}

def run_smoke_test():
    print("="*50)
    print("RUNNING SMOKE TEST")
    print("="*50)
    
    clock = ReplayClock(datetime(2025, 1, 15, 9, 15))
    market_data = MockDataProvider(["SMOKE"])
    analytics = MockAnalyticsProvider()
    strategy = create_strategy("regime_v2", "regime_v2", {})
    
    broker = PaperBroker(clock)
    exec_config = ExecutionConfig(mode=ExecutionMode.PAPER)
    execution = ExecutionHandler(clock, broker, exec_config)
    # Disable kill switch file check for smoke test
    execution._kill_switch_disabled = True
    position_tracker = PositionTracker()
    
    runner = TradingRunner(
        config=RunnerConfig(symbols=["SMOKE"], strategy_ids=["regime_v2"]),
        market_data_provider=market_data,
        analytics_provider=analytics,
        strategies=[strategy],
        execution_handler=execution,
        position_tracker=position_tracker,
        clock=clock
    )
    
    stats = runner.run()
    
    # Assertions
    passed = True
    print("\n" + "="*50)
    if stats['bars_processed'] >= 1:
        print("  [PASS] bars_processed >= 1")
    else:
        print("  [FAIL] bars_processed < 1")
        passed = False
        
    if stats['signals_generated'] >= 1:
        print("  [PASS] signals_generated >= 1")
    else:
        print("  [FAIL] signals_generated < 1")
        passed = False

    if stats['trades_executed'] >= 1:
        print("  [PASS] trades_executed >= 1")
    else:
        print("  [FAIL] trades_executed < 1")
        passed = False
        
    print("="*50)
    if passed:
        print("  SMOKE TEST: PASS")
    else:
        print("  SMOKE TEST: FAIL")
    print("="*50)
    
    # Save smoke metrics for UI
    with open("logs/smoke_metrics.json", "w") as f:
        json.dump(stats, f)

if __name__ == "__main__":
    run_smoke_test()
