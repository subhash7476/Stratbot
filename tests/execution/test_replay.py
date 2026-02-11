import pytest
import os
from datetime import datetime
from core.execution.handler import ExecutionHandler
from core.execution.order_models import NormalizedOrder, OrderSide, OrderType, InstrumentType
from core.execution.order_lifecycle import FillEvent
from core.clock import Clock
from core.brokers.broker_base import BrokerAdapter
from core.database.manager import DatabaseManager


class MockBroker(BrokerAdapter):
    pass


class MockBroker(BrokerAdapter):
    def place_order(self, order): return "mock_id"
    def cancel_order(self, id): return True


@pytest.fixture
def clean_env():
    if os.path.exists("data/execution.db"):
        os.remove("data/execution.db")
    yield
    if os.path.exists("data/execution.db"):
        os.remove("data/execution.db")


def test_crash_recovery_replay(clean_env):
    """
    Test that the execution handler can recover state after a 'crash' (restart).
    """
    db = DatabaseManager()
    clock = Clock()
    broker = MockBroker()

    # 1. Start Engine A
    handler_a = ExecutionHandler(db, clock, broker, load_db_state=True)

    # Create an order manually to bypass signal processing for this test
    order = NormalizedOrder(
        correlation_id="test-order-1",
        symbol="RELIANCE",
        instrument_type=InstrumentType.EQUITY,
        side=OrderSide.BUY,
        quantity=100,
        order_type=OrderType.MARKET,
        timestamp=datetime.now(),
        strategy_id="strat1",
        signal_id="sig1"
    )

    # Register order (persists it)
    handler_a.order_tracker.add_order(order)

    # Create a fill (persists it)
    fill = FillEvent(
        fill_id="fill-1",
        order_id="test-order-1",
        symbol="RELIANCE",
        quantity=100,
        price=2500.0,
        side="BUY",
        timestamp=datetime.now()
    )

    handler_a.order_tracker.process_fill(fill)
    handler_a.position_tracker.update_from_fill(fill)

    # Verify State A
    assert handler_a.position_tracker.get_position("RELIANCE").quantity == 100
    assert handler_a.order_tracker.get_order(
        "test-order-1").status.value == "FILLED"

    # 2. Simulate Crash (Delete Handler A, Start Handler B)
    del handler_a

    handler_b = ExecutionHandler(db, clock, broker, load_db_state=True)

    # 3. Verify State B (Replayed)
    # Check Order
    replayed_order = handler_b.order_tracker.get_order("test-order-1")
    assert replayed_order is not None
    assert replayed_order.status.value == "FILLED"
    assert replayed_order.filled_quantity == 100

    # Check Position
    pos = handler_b.position_tracker.get_position("RELIANCE")
    assert pos.quantity == 100
    assert pos.avg_price == 2500.0
