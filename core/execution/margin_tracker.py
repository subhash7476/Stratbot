"""
Margin Tracker
--------------
Tracks estimated margin usage based on gross exposure.
"""
from typing import Dict, Optional
from core.execution.position_tracker import PositionTracker


class MarginTracker:
    def __init__(self, position_tracker: PositionTracker, margin_rate: float = 0.2):
        """
        Args:
            margin_rate: Default margin requirement (e.g., 0.2 for 5x leverage)
        """
        self.position_tracker = position_tracker
        self.margin_rate = margin_rate

    def get_exposure(self, current_prices: Dict[str, float], symbol: Optional[str] = None) -> float:
        """Calculate Gross Exposure (Value of all positions)."""
        if symbol:
            return self._calculate_single_exposure(symbol, current_prices.get(symbol))

        total_exposure = 0.0
        for sym in self.position_tracker._positions:
            price = current_prices.get(sym)
            if price:
                total_exposure += self._calculate_single_exposure(sym, price)
        return total_exposure

    def _calculate_single_exposure(self, symbol: str, current_price: Optional[float]) -> float:
        if current_price is None:
            return 0.0
        pos = self.position_tracker.get_position(symbol)
        return pos.quantity * current_price * pos.instrument.multiplier

    def get_used_margin(self, current_prices: Dict[str, float]) -> float:
        """Estimate used margin based on gross exposure."""
        return self.get_exposure(current_prices) * self.margin_rate
