"""
Test Multi-Leg Order Groups
---------------------------
Tests for Phase 9B: Spread Engine.
"""
import pytest
from uuid import UUID
from datetime import datetime
from core.execution.handler import ExecutionHandler
from core.execution.groups.order_group import OrderGroupType, GroupStatus
from core.events import SignalEvent, SignalType
from core.clock import Clock
from core.brokers.mock_broker_adapter import MockBrokerAdapter
from core.database.manager import DatabaseManager


@pytest.fixture
def handler():
    db = DatabaseManager()  # Mock or in-memory
    clock = Clock()
    broker = MockBrokerAdapter()
    return ExecutionHandler(db, clock, broker, load_db_state=False)


def test_vertical_spread_lifecycle(handler):
    """
    Test a 2-leg vertical spread:
    1. Create group
    2. Fill legs
    3. Verify group status
    """
    # Create 2 signals
    sig1 = SignalEvent("strat", "NIFTY25JAN22500CE",
                       datetime.now(), SignalType.BUY, 1.0)
    sig2 = SignalEvent("strat", "NIFTY25JAN22600CE",
                       datetime.now(), SignalType.SELL, 1.0)

    # Process group
    group_id_str = handler.process_group_signal(
        [sig1, sig2], OrderGroupType.SPREAD)
    group_id = UUID(group_id_str)

    # Get group
    group = handler.group_tracker.get_group(group_id)

    assert group is not None
    assert len(group.legs) == 2

    # MockBroker fills immediately, so it should be FILLED
    assert group.status == GroupStatus.FILLED

    # Check PnL (Mock price 100.0 for both)
    unrealized = handler.group_pnl_tracker.get_group_unrealized_pnl(group_id, {
        "NIFTY25JAN22500CE": 110.0,
        "NIFTY25JAN22600CE": 110.0
    })
    # Leg 1: +1000, Leg 2: -1000 => 0
    assert unrealized == 0.0
