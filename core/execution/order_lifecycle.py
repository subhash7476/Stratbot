from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Union
from uuid import UUID, uuid4


class OrderStatus(Enum):
    CREATED = "CREATED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, init=False)
class FillEvent:
    """
    Immutable record of an execution fill.

    Supports both signatures:
    - New: FillEvent(fill_id, order_id, symbol, quantity, price, timestamp, side, fee=0.0)
    - Legacy: FillEvent(order_id, quantity, price, timestamp)
    """
    fill_id: str
    order_id: str  # Matches NormalizedOrder.correlation_id
    symbol: str
    quantity: float
    price: float
    timestamp: datetime
    side: str  # "BUY" or "SELL"
    fee: float = 0.0

    def __init__(
        self,
        *args,
        fill_id: Optional[str] = None,
        order_id: Optional[Union[str, UUID]] = None,
        symbol: Optional[str] = None,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        timestamp: Optional[datetime] = None,
        side: str = "BUY",
        fee: float = 0.0,
    ):
        if args and len(args) == 4 and order_id is None:
            # Legacy positional form: (order_id, quantity, price, timestamp)
            order_id, quantity, price, timestamp = args
            fill_id = fill_id or str(uuid4())
            symbol = symbol or "UNKNOWN"
            side = side or "BUY"
        elif args:
            # Full positional form fallback
            vals = list(args)
            if len(vals) >= 7:
                fill_id, order_id, symbol, quantity, price, timestamp, side = vals[:7]
                fee = vals[7] if len(vals) > 7 else fee
            else:
                raise TypeError("Unsupported FillEvent constructor signature")

        if order_id is None or quantity is None or price is None or timestamp is None:
            raise TypeError("Missing required fill fields")

        object.__setattr__(self, "fill_id", str(fill_id or uuid4()))
        object.__setattr__(self, "order_id", str(order_id))
        object.__setattr__(self, "symbol", symbol or "UNKNOWN")
        object.__setattr__(self, "quantity", float(quantity))
        object.__setattr__(self, "price", float(price))
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "side", str(side))
        object.__setattr__(self, "fee", float(fee))
