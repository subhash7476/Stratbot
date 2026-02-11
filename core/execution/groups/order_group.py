"""
Order Group Models
------------------
Defines structures for multi-leg order groups.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List
from uuid import UUID, uuid4
from core.execution.order_models import NormalizedOrder


class OrderGroupType(Enum):
    SINGLE = "SINGLE"
    SPREAD = "SPREAD"
    IRON_CONDOR = "IRON_CONDOR"
    STRADDLE = "STRADDLE"
    STRANGLE = "STRANGLE"
    CUSTOM = "CUSTOM"


class GroupStatus(Enum):
    CREATED = "CREATED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"


@dataclass
class OrderGroup:
    group_type: OrderGroupType
    legs: List[NormalizedOrder] = field(default_factory=list)
    group_id: UUID = field(default_factory=uuid4)
    status: GroupStatus = GroupStatus.CREATED

    def add_leg(self, order: NormalizedOrder):
        """Adds a leg to the group."""
        # Ensure the order is linked to this group
        # In a real scenario, NormalizedOrder is frozen, so this check expects
        # the caller to have set it correctly during creation.
        self.legs.append(order)
