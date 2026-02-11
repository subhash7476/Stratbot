"""
Paper Broker Adapter
--------------------
Simulated execution for backtesting and paper trading.
"""
import uuid
import logging
from typing import Dict, Union
from core.brokers.base import BrokerAdapter
from core.events import OrderEvent, OrderStatus, TradeEvent, TradeStatus
from core.execution.position_tracker import Position, PositionTracker
from core.execution.order_models import NormalizedOrder
from core.clock import Clock

class PaperBroker(BrokerAdapter):
    """
    Simulates immediate fills with zero slippage.
    """
    
    def __init__(self, clock: Clock):
        self.clock = clock
        self.tracker = PositionTracker()
        self.logger = logging.getLogger(__name__)
        self._fill_callback = None

    def place_order(self, order: Union[OrderEvent, NormalizedOrder]) -> str:
        # Generate random broker ID
        broker_id = str(uuid.uuid4())

        # Handle both old OrderEvent and new NormalizedOrder
        if isinstance(order, NormalizedOrder):
            symbol = order.symbol
            side = order.side.value if hasattr(order.side, 'value') else order.side
            quantity = order.quantity
            price = "MARKET"  # NormalizedOrder doesn't have price (market orders)
        else:
            symbol = order.symbol
            side = order.side
            quantity = order.quantity
            price = getattr(order, 'price', 'MARKET')

        self.logger.debug(f"[PAPER] Filled {side} {quantity} {symbol} @ {price}")
        return broker_id

    def get_order_status(self, order_id: str) -> OrderStatus:
        return OrderStatus.FILLED

    def get_positions(self) -> Dict[str, Position]:
        return self.tracker.get_all_positions()

    def cancel_order(self, order_id: str) -> bool:
        return True

    def subscribe_fills(self, callback):
        """Register a callback for fill events (for compatibility with new execution engine)."""
        self._fill_callback = callback
