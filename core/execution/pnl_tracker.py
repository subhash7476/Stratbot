"""
PnL Tracker
-----------
Tracks Realized and Unrealized PnL based on fills and positions.
"""
from typing import Dict, Optional
from collections import defaultdict
from core.execution.order_lifecycle import FillEvent
from core.execution.position_tracker import PositionTracker


class PnLTracker:
    def __init__(self, position_tracker: PositionTracker):
        self.position_tracker = position_tracker
        self._realized_pnl: Dict[str, float] = defaultdict(float)

    def update(self, fill: FillEvent, realized_pnl: float):
        """
        Updates realized PnL accumulator from a fill event.
        """
        self._realized_pnl[fill.symbol] += realized_pnl

    def get_realized_pnl(self, symbol: Optional[str] = None) -> float:
        """Get realized PnL for a symbol or total."""
        if symbol:
            return self._realized_pnl[symbol]
        return sum(self._realized_pnl.values())

    def get_unrealized_pnl(self, current_prices: Dict[str, float], symbol: Optional[str] = None) -> float:
        """
        Calculate unrealized PnL based on current positions and prices.
        Unrealized = (Current Price - Avg Price) * Quantity * Direction
        """
        if symbol:
            return self._calculate_single_unrealized(symbol, current_prices.get(symbol))

        total_unrealized = 0.0
        # Iterate over all open positions
        for sym in self.position_tracker._positions:
            price = current_prices.get(sym)
            if price:
                total_unrealized += self._calculate_single_unrealized(
                    sym, price)
        return total_unrealized

    def _calculate_single_unrealized(self, symbol: str, current_price: Optional[float]) -> float:
        if current_price is None:
            return 0.0

        pos = self.position_tracker.get_position(symbol)
        if pos.quantity == 0:
            return 0.0

        # Long: (Current - Avg) * Qty
        # Short: (Avg - Current) * Qty = (Current - Avg) * Qty * -1
        direction = 1 if pos.side.value == "LONG" else -1
        return (current_price - pos.avg_price) * pos.quantity * pos.instrument.multiplier * direction

    def get_total_pnl(self, current_prices: Dict[str, float], symbol: Optional[str] = None) -> float:
        return self.get_realized_pnl(symbol) + self.get_unrealized_pnl(current_prices, symbol)
