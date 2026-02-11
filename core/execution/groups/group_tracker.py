"""
Group Tracker
-------------
Tracks lifecycle of multi-leg order groups.
"""
from typing import Dict, Optional, List
from uuid import UUID
from core.execution.groups.order_group import OrderGroup, GroupStatus, OrderGroupType
from core.execution.order_models import NormalizedOrder
from core.execution.order_tracker import OrderTracker
from core.execution.order_lifecycle import OrderStatus


class GroupTracker:
    def __init__(self, order_tracker: OrderTracker):
        self.order_tracker = order_tracker
        self._groups: Dict[UUID, OrderGroup] = {}
        self._order_map: Dict[UUID, UUID] = {}  # order_id -> group_id

    def create_group(self, group_type: OrderGroupType, legs: List[NormalizedOrder]) -> OrderGroup:
        """Creates and registers a new order group."""
        # Use the group_id from the first leg if available, else generate new
        group_id = legs[0].group_id if legs and legs[0].group_id else None

        group = OrderGroup(group_type=group_type, legs=legs)
        if group_id:
            group.group_id = group_id

        self._groups[group.group_id] = group
        for leg in legs:
            self._order_map[leg.correlation_id] = group.group_id

        return group

    def get_group(self, group_id: UUID) -> Optional[OrderGroup]:
        return self._groups.get(group_id)

    def get_group_by_order(self, order_id: UUID) -> Optional[OrderGroup]:
        group_id = self._order_map.get(order_id)
        if group_id:
            return self._groups.get(group_id)
        return None

    def update_from_order_status(self, order_id: UUID):
        """
        Updates group status based on a change in one of its legs.
        Should be called whenever an order status changes.
        """
        group = self.get_group_by_order(order_id)
        if not group:
            return

        # Aggregate status from all legs
        total_legs = len(group.legs)
        if total_legs == 0:
            return

        filled_legs = 0
        all_filled = True
        any_filled = False

        for leg in group.legs:
            order_state = self.order_tracker.get_order(leg.correlation_id)
            if not order_state:
                all_filled = False
                continue

            if order_state.status == OrderStatus.FILLED:
                any_filled = True
                filled_legs += 1
            elif order_state.status == OrderStatus.PARTIALLY_FILLED:
                any_filled = True
                all_filled = False
            else:
                all_filled = False

        if all_filled:
            group.status = GroupStatus.FILLED
        elif any_filled:
            group.status = GroupStatus.PARTIALLY_FILLED
        else:
            group.status = GroupStatus.CREATED
