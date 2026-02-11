from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4
from typing import Dict, Any, Optional
from core.instruments.instrument_base import Instrument, InstrumentType


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"


@dataclass(frozen=True)
class OrderMetadata:
    original_confidence: float
    strategy_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
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
