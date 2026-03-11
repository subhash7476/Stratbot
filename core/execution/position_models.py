"""
Position Models
---------------
Data structures for execution-owned position state.
"""

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Optional
from core.instruments.instrument_base import Instrument
from core.instruments.instrument_parser import InstrumentParser


class PositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


@dataclass(frozen=True, init=False)
class Position:
    """Immutable snapshot of a position."""
    instrument: Instrument
    side: PositionSide = PositionSide.FLAT
    quantity: float = 0.0  # Always positive absolute value
    avg_price: float = 0.0
    last_updated: datetime = field(default_factory=datetime.now)

    def __init__(
        self,
        instrument: Optional[Instrument] = None,
        *,
        symbol: Optional[str] = None,
        side: PositionSide = PositionSide.FLAT,
        quantity: float = 0.0,
        avg_price: float = 0.0,
        last_updated: Optional[datetime] = None,
    ):
        """
        Backward-compatible constructor.

        Supports both:
        - Position(instrument=...)
        - Position(symbol="INFY", ...)
        """
        resolved_instrument = instrument or InstrumentParser.parse(symbol or "")
        object.__setattr__(self, "instrument", resolved_instrument)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "avg_price", avg_price)
        object.__setattr__(self, "last_updated", last_updated or datetime.now())

    @property
    def symbol(self) -> str:
        return self.instrument.symbol
