"""
Reconciliation Engine
---------------------
Compares internal state with broker state to detect anomalies.
"""
from dataclasses import dataclass
from typing import Dict, List, Any
from core.execution.position_tracker import PositionTracker


@dataclass
class ReconciliationAlert:
    symbol: str
    issue: str
    internal_value: Any
    broker_value: Any
    timestamp: float


class ReconciliationEngine:
    def __init__(self, position_tracker: PositionTracker):
        self.position_tracker = position_tracker

    def reconcile(self, broker_positions: List[Dict[str, Any]]) -> List[ReconciliationAlert]:
        """
        Compare internal positions against broker positions.

        Args:
            broker_positions: List of dicts from broker adapter.
                              Expected format: {'symbol': str, 'quantity': float, 'side': str}
                              Side should be 'LONG', 'SHORT', or inferred from signed quantity.
        """
        alerts = []
        import time
        now = time.time()

        # Convert broker list to dict for O(1) lookup
        broker_map = {}
        for bp in broker_positions:
            # Normalize broker data
            # Assuming broker returns signed quantity or side+qty
            qty = float(bp.get('quantity', 0))
            symbol = bp.get('symbol')

            # Handle side if provided, else assume signed qty
            # If broker gives unsigned qty + side:
            side_str = bp.get('side', '').upper() if bp.get('side') else ''
            if side_str == 'SHORT':
                qty = -abs(qty)
            elif side_str == 'LONG':
                qty = abs(qty)

            if symbol:
                broker_map[symbol] = qty

        # Check all internal positions
        for symbol in self.position_tracker._positions:
            internal_pos = self.position_tracker.get_position(symbol)
            internal_net = self.position_tracker.net_quantity(symbol)

            if internal_net == 0 and symbol not in broker_map:
                continue

            broker_qty = broker_map.get(symbol, 0.0)

            # Check Quantity Mismatch
            if abs(internal_net - broker_qty) > 1e-6:  # Float tolerance
                alerts.append(ReconciliationAlert(
                    symbol=symbol,
                    issue="QUANTITY_MISMATCH",
                    internal_value=internal_net,
                    broker_value=broker_qty,
                    timestamp=now
                ))

        # Check for positions at broker not in internal
        for symbol, broker_qty in broker_map.items():
            if abs(broker_qty) > 0 and not self.position_tracker.has_open_position(symbol):
                alerts.append(ReconciliationAlert(
                    symbol=symbol,
                    issue="ORPHANED_BROKER_POSITION",
                    internal_value=0.0,
                    broker_value=broker_qty,
                    timestamp=now
                ))

        return alerts
