from enum import Enum
from dataclasses import dataclass
from datetime import datetime


class OrderStatus(Enum):
    CREATED = "CREATED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class FillEvent:
    """
    Immutable record of an execution fill.
    Represents a trade that has occurred.
    """
    fill_id: str
    order_id: str  # Matches NormalizedOrder.correlation_id
    symbol: str
    quantity: float
    price: float
    timestamp: datetime
    side: str  # "BUY" or "SELL"
    fee: float = 0.0
