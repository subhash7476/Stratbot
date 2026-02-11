from dataclasses import dataclass
from enum import Enum

class RiskStatus(Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

@dataclass(frozen=True)
class RiskDecision:
    status: RiskStatus
    reason: str = ""

    @property
    def approved(self) -> bool:
        return self.status == RiskStatus.APPROVED
