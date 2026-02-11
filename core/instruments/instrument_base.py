from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict


class InstrumentType(Enum):
    EQUITY = "EQUITY"
    FUTURE = "FUTURE"
    OPTION = "OPTION"


@dataclass(frozen=True)
class Instrument:
    symbol: str
    type: InstrumentType
    multiplier: float = 1.0

    @property
    def identifier(self) -> str:
        return self.symbol

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "type": self.type.value,
            "multiplier": self.multiplier
        }
