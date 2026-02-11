import logging
from uuid import uuid4
from datetime import datetime
from typing import Dict
from core.brokers.broker_base import BrokerAdapter
from core.execution.order_models import NormalizedOrder
from core.execution.order_lifecycle import FillEvent


class MockBrokerAdapter(BrokerAdapter):
    """
    Mock broker for testing and simulation.
    Simulates immediate fills for orders.
    """

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.orders: Dict[str, NormalizedOrder] = {}

    def place_order(self, order: NormalizedOrder) -> str:
        broker_order_id = str(uuid4())
        self.orders[broker_order_id] = order
        self.logger.info(
            f"MockBroker: Placed {order.side} {order.quantity} {order.symbol} (ID: {broker_order_id})")

        # Simulate immediate fill
        if self._fill_callback:
            fill = FillEvent(
                fill_id=str(uuid4()),
                order_id=order.correlation_id,
                symbol=order.symbol,
                quantity=order.quantity,
                price=100.0,  # Mock price
                timestamp=datetime.now(),
                side=order.side.value,
                fee=0.0
            )
            self._fill_callback(fill)

        return broker_order_id

    def cancel_order(self, broker_order_id: str) -> bool:
        if broker_order_id in self.orders:
            del self.orders[broker_order_id]
            return True
        return False
