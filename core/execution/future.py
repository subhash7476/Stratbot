from dataclasses import dataclass
from datetime import date
from core.instruments.instrument_base import Instrument, InstrumentType


@dataclass(frozen=True)
class Future(Instrument):
    underlying: str
    expiry: date

    def __init__(self, symbol: str, underlying: str, expiry: date, multiplier: float = 1.0):
        object.__setattr__(self, 'symbol', symbol)
        object.__setattr__(self, 'type', InstrumentType.FUTURE)
        object.__setattr__(self, 'multiplier', multiplier)
        object.__setattr__(self, 'underlying', underlying)
        object.__setattr__(self, 'expiry', expiry)
