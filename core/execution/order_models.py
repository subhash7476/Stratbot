from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4
from typing import Dict, Any, Optional

from core.instruments.instrument_base import Instrument, InstrumentType
from core.instruments.equity import Equity


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"


@dataclass(frozen=True)
class OrderMetadata:
    original_confidence: float
    strategy_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, init=False)
class NormalizedOrder:
    instrument: Instrument
    side: OrderSide
    quantity: int
    order_type: OrderType
    strategy_id: str
    signal_id: str
    timestamp: datetime
    correlation_id: UUID = field(default_factory=uuid4)
    metadata: OrderMetadata = field(default_factory=lambda: OrderMetadata(0.0))
    group_id: Optional[UUID] = None

    def __init__(
        self,
        instrument: Optional[Instrument] = None,
        *,
        symbol: Optional[str] = None,
        instrument_type: Optional[InstrumentType] = None,
        side: OrderSide,
        quantity: int,
        order_type: OrderType,
        strategy_id: str,
        signal_id: str,
        timestamp: datetime,
        correlation_id: Optional[UUID] = None,
        metadata: Optional[OrderMetadata] = None,
        group_id: Optional[UUID] = None,
    ):
        """
        Backward-compatible constructor.

        Supports both:
        - NormalizedOrder(instrument=...)
        - NormalizedOrder(symbol=..., instrument_type=...)
        """
        resolved_instrument = instrument
        if resolved_instrument is None:
            resolved_symbol = symbol or ""
            resolved_type = instrument_type or InstrumentType.EQUITY
            if resolved_type == InstrumentType.EQUITY:
                resolved_instrument = Equity(resolved_symbol)
            else:
                # Fallback for legacy call sites.
                resolved_instrument = Equity(resolved_symbol)

        object.__setattr__(self, "instrument", resolved_instrument)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "quantity", int(quantity))
        object.__setattr__(self, "order_type", order_type)
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "signal_id", signal_id)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "correlation_id", correlation_id or uuid4())
        object.__setattr__(self, "metadata", metadata or OrderMetadata(0.0))
        object.__setattr__(self, "group_id", group_id)

    @property
    def symbol(self) -> str:
        return self.instrument.symbol

    @property
    def instrument_type(self) -> InstrumentType:
        return self.instrument.type

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.instrument.symbol,
            "instrument_type": self.instrument.type.value,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "strategy_id": self.strategy_id,
            "signal_id": self.signal_id,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": str(self.correlation_id),
            "metadata": {
                "original_confidence": self.metadata.original_confidence,
                "strategy_metadata": self.metadata.strategy_metadata
            },
            "group_id": str(self.group_id) if self.group_id else None
        }
