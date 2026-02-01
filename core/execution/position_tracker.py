"""
Position Tracker
----------------
Single source of truth for current holdings.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional
from core.events import TradeEvent

@dataclass
class Position:
    symbol: str
    quantity: float
    avg_entry_price: float
    last_update: datetime

class PositionTracker:
    """
    Manages net positions for all symbols.
    """
    
    def __init__(self):
        self._positions: Dict[str, Position] = {}

    def apply_trade(self, trade: TradeEvent):
        """Updates internal position state based on a filled trade."""
        symbol = trade.symbol
        if symbol not in self._positions:
            self._positions[symbol] = Position(symbol, 0.0, 0.0, trade.timestamp)
        
        pos = self._positions[symbol]
        
        # Calculate new quantity and average price
        if trade.direction == "BUY":
            new_qty = pos.quantity + trade.quantity
            if new_qty != 0:
                # Update average price for increase in position
                if pos.quantity >= 0:
                    pos.avg_entry_price = (pos.quantity * pos.avg_entry_price + trade.quantity * trade.price) / new_qty
            pos.quantity = new_qty
        else: # SELL
            new_qty = pos.quantity - trade.quantity
            if new_qty != 0:
                if pos.quantity <= 0:
                    pos.avg_entry_price = (abs(pos.quantity) * pos.avg_entry_price + trade.quantity * trade.price) / abs(new_qty)
            pos.quantity = new_qty
            
        pos.last_update = trade.timestamp

    def get_position_quantity(self, symbol: str) -> float:
        return self._positions.get(symbol, Position(symbol, 0.0, 0.0, datetime.now())).quantity

    def get_all_positions(self) -> Dict[str, Position]:
        return self._positions.copy()
