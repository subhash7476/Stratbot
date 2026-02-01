"""
Sizing Policy
-------------
Logic for determining position sizes.
"""
from core.events import SignalEvent

class SizingPolicy:
    def __init__(self, default_quantity: float = 100.0):
        self.default_quantity = default_quantity

    def calculate_quantity(self, signal: SignalEvent, current_equity: float) -> float:
        """Determines quantity based on signal confidence."""
        return self.default_quantity * (0.5 + signal.confidence * 0.5)
