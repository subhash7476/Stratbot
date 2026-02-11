from dataclasses import dataclass
from core.instruments.instrument_base import Instrument, InstrumentType


@dataclass(frozen=True)
class Equity(Instrument):
    def __init__(self, symbol: str):
        object.__setattr__(self, 'symbol', symbol)
        object.__setattr__(self, 'type', InstrumentType.EQUITY)
        object.__setattr__(self, 'multiplier', 1.0)
