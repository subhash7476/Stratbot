"""
Execution Recorder
------------------
Captures all signals and trades for post-hoc analysis.
"""
from core.events import SignalEvent, TradeEvent

class ExecutionRecorder:
    def __init__(self):
        self.signals = []
        self.trades = []

    def record_signal(self, signal: SignalEvent):
        self.signals.append(signal)

    def record_trade(self, trade: TradeEvent):
        self.trades.append(trade)
