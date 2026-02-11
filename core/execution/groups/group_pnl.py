"""
Group PnL Tracker
-----------------
Aggregates PnL for order groups.
"""
from typing import Dict, Optional
from uuid import UUID
from collections import defaultdict
from core.execution.groups.group_tracker import GroupTracker
from core.execution.order_lifecycle import FillEvent
from core.execution.order_tracker import OrderTracker


class GroupPnLTracker:
    def __init__(self, group_tracker: GroupTracker, order_tracker: OrderTracker):
        self.group_tracker = group_tracker
        self.order_tracker = order_tracker
        self._group_realized_pnl: Dict[UUID, float] = defaultdict(float)

    def update(self, fill: FillEvent, realized_pnl: float):
        """
        Updates group realized PnL from a fill event.
        """
        try:
            # fill.order_id might be str or UUID
            order_uuid = UUID(str(fill.order_id))
            group = self.group_tracker.get_group_by_order(order_uuid)
            if group:
                self._group_realized_pnl[group.group_id] += realized_pnl
        except ValueError:
            pass

    def get_group_realized_pnl(self, group_id: UUID) -> float:
        return self._group_realized_pnl[group_id]

    def get_group_unrealized_pnl(self, group_id: UUID, current_prices: Dict[str, float]) -> float:
        """
        Calculates unrealized PnL for the group.
        Sum of (Current Price - Avg Fill Price) * Filled Qty for all legs.
        """
        group = self.group_tracker.get_group(group_id)
        if not group:
            return 0.0

        total_unrealized = 0.0

        for leg in group.legs:
            symbol = leg.symbol
            current_price = current_prices.get(symbol)
            if current_price is None:
                continue

            order_state = self.order_tracker.get_order(leg.correlation_id)
            if not order_state or order_state.filled_quantity == 0:
                continue

            direction = 1 if leg.side.value == "BUY" else -1
            multiplier = leg.instrument.multiplier

            leg_unrealized = (current_price - order_state.average_price) * \
                order_state.filled_quantity * multiplier * direction
            total_unrealized += leg_unrealized

        return total_unrealized
