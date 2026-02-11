from dataclasses import dataclass
from datetime import date
from enum import Enum
from core.instruments.instrument_base import Instrument, InstrumentType


class OptionType(Enum):
    CALL = "CE"
    PUT = "PE"


@dataclass(frozen=True)
class Option(Instrument):
    underlying: str = ""
    expiry: date = None
    strike: float = 0.0
    option_type: OptionType = None
    lot_size: int = 1

    def __init__(self, symbol: str, underlying: str, expiry: date, strike: float, option_type: OptionType, lot_size: int = 1, multiplier: float = 1.0):
        object.__setattr__(self, 'symbol', symbol)
        object.__setattr__(self, 'type', InstrumentType.OPTION)
        object.__setattr__(self, 'multiplier', multiplier)
        object.__setattr__(self, 'underlying', underlying)
        object.__setattr__(self, 'expiry', expiry)
        object.__setattr__(self, 'strike', strike)
        object.__setattr__(self, 'option_type', option_type)
        object.__setattr__(self, 'lot_size', lot_size)
