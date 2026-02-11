"""
Position Models
---------------
Data structures for execution-owned position state.
"""

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from core.instruments.instrument_base import Instrument


class PositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


@dataclass(frozen=True)
class Position:
    """Immutable snapshot of a position."""
    instrument: Instrument
    side: PositionSide = PositionSide.FLAT
    quantity: float = 0.0  # Always positive absolute value
    avg_price: float = 0.0

    @property
    def symbol(self) -> str:
        return self.instrument.symbol
    last_updated: datetime = field(default_factory=datetime.now)
