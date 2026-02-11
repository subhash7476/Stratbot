from typing import Dict, Optional, List
from datetime import datetime
from core.execution.order_models import NormalizedOrder
from core.execution.order_lifecycle import OrderStatus, FillEvent
from core.execution.persistence.order_repository import OrderRepository
from core.execution.persistence.fill_repository import FillRepository


class OrderState:
    """
    Mutable state of a single order, tracking its lifecycle and fills.
    """

    def __init__(self, order: NormalizedOrder):
        self.order = order
        self.status = OrderStatus.CREATED
        self.filled_quantity = 0.0
        self.average_price = 0.0
        self.fills: List[FillEvent] = []
        self.created_at = datetime.now()
        self.updated_at = self.created_at

    @property
    def remaining_quantity(self) -> float:
        return self.order.quantity - self.filled_quantity

    def add_fill(self, fill: FillEvent):
        """
        Apply a fill to this order.
        Updates filled quantity, average price, and status.
        """
        if fill.order_id != self.order.correlation_id:
            raise ValueError(
                f"Fill order_id {fill.order_id} does not match order {self.order.correlation_id}")

        if fill.quantity <= 0:
            raise ValueError("Fill quantity must be positive")

        # Update weighted average price
        total_value = (self.filled_quantity * self.average_price) + \
            (fill.quantity * fill.price)
        new_quantity = self.filled_quantity + fill.quantity

        if new_quantity > 0:
            self.average_price = total_value / new_quantity

        self.filled_quantity = new_quantity
        self.fills.append(fill)
        self.updated_at = datetime.now()

        # Update status
        if self.filled_quantity >= self.order.quantity:
            self.status = OrderStatus.FILLED
        elif self.filled_quantity > 0:
            self.status = OrderStatus.PARTIALLY_FILLED


class OrderTracker:
    """
    Central registry for order truth within the execution engine.
    """

    def __init__(self, order_repo: Optional[OrderRepository] = None, fill_repo: Optional[FillRepository] = None):
        self._orders: Dict[str, OrderState] = {}
        self.order_repo = order_repo
        self.fill_repo = fill_repo

    def add_order(self, order: NormalizedOrder, persist: bool = True) -> OrderState:
        if order.correlation_id in self._orders:
            raise ValueError(f"Order {order.correlation_id} already tracked")

        state = OrderState(order)
        self._orders[order.correlation_id] = state

        if persist and self.order_repo:
            self.order_repo.save(order)

        return state

    def get_order(self, order_id: str) -> Optional[OrderState]:
        return self._orders.get(order_id)

    def process_fill(self, fill: FillEvent, persist: bool = True) -> OrderState:
        state = self.get_order(fill.order_id)
        if not state:
            raise ValueError(
                f"Order {fill.order_id} not found for fill {fill.fill_id}")

        state.add_fill(fill)

        if persist and self.fill_repo:
            self.fill_repo.save(fill)

        return state
