from abc import ABC, abstractmethod
from typing import Callable, Any
from core.execution.order_models import NormalizedOrder


class BrokerAdapter(ABC):
    """
    Abstract base class for broker interactions.
    Defines the contract for order placement and fill subscriptions.
    """

    def __init__(self):
        self._fill_callback: Callable[[Any], None] = None

    @abstractmethod
    def place_order(self, order: NormalizedOrder) -> str:
        """
        Places an order with the broker.
        Returns the broker's order ID.
        """
        pass

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        """
        Cancels an order.
        """
        pass

    def subscribe_fills(self, callback: Callable[[Any], None]):
        """
        Registers a callback for fill events.
        The callback should accept a FillEvent.
        """
        self._fill_callback = callback
