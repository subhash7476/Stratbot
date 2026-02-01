"""
Paper Broker Adapter
--------------------
Simulated execution for backtesting and paper trading.
"""
import uuid
import logging
from typing import Dict
from core.brokers.base import BrokerAdapter
from core.events import OrderEvent, OrderStatus, TradeEvent, TradeStatus
from core.execution.position_tracker import Position, PositionTracker
from core.clock import Clock

class PaperBroker(BrokerAdapter):
    """
    Simulates immediate fills with zero slippage.
    """
    
    def __init__(self, clock: Clock):
        self.clock = clock
        self.tracker = PositionTracker()
        self.logger = logging.getLogger(__name__)

    def place_order(self, order: OrderEvent) -> str:
        # Generate random broker ID
        broker_id = str(uuid.uuid4())
        self.logger.debug(f"[PAPER] Filled {order.side} {order.quantity} {order.symbol} @ {order.price}")
        return broker_id

    def get_order_status(self, order_id: str) -> OrderStatus:
        return OrderStatus.FILLED

    def get_positions(self) -> Dict[str, Position]:
        return self.tracker.get_all_positions()

    def cancel_order(self, order_id: str) -> bool:
        return True
