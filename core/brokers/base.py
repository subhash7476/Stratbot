"""
Broker Adapter Interface
------------------------
Abstracts the broker-specific API (REST/WS) behind a standard interface.
"""
from abc import ABC, abstractmethod
from typing import Dict, Optional, List
from core.events import OrderEvent, OrderStatus
from core.execution.position_tracker import Position

class BrokerAdapter(ABC):
    """
    Abstract base class for all broker connections.
    """
    
    @abstractmethod
    def place_order(self, order: OrderEvent) -> str:
        """Dispatches an order to the broker. Returns broker_order_id."""
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderStatus:
        """Queries the current status of an order."""
        pass

    @abstractmethod
    def get_positions(self) -> Dict[str, Position]:
        """Fetches current net positions from the broker."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Attempts to cancel an open order."""
        pass
