import pytest
import os
from datetime import datetime
from core.execution.handler import ExecutionHandler
from core.execution.order_models import NormalizedOrder, OrderSide, OrderType, InstrumentType
from core.clock import Clock
from core.brokers.mock_broker_adapter import MockBrokerAdapter
from core.database.manager import DatabaseManager
from core.events import SignalEvent, SignalType


@pytest.fixture
def clean_env():
    if os.path.exists("data/execution.db"):
        os.remove("data/execution.db")
    yield
    if os.path.exists("data/execution.db"):
        os.remove("data/execution.db")


def test_broker_order_placement_and_fill(clean_env):
    """
    Test that orders are routed to the broker and fills are ingested back via callback.
    """
    db = DatabaseManager()
    clock = Clock()
    broker = MockBrokerAdapter()
    handler = ExecutionHandler(db, clock, broker)

    # Create a signal
    signal = SignalEvent(
        strategy_id="test_strat",
        symbol="RELIANCE",
        timestamp=datetime.now(),
        signal_type=SignalType.BUY,
        confidence=1.0,
        metadata={"signal_id": "sig_001"}
    )

    # Process signal
    # This should:
    # 1. Create order
    # 2. Pass risk
    # 3. Register in OrderTracker
    # 4. Call broker.place_order
    # 5. Broker simulates fill -> calls handler._handle_broker_fill
    # 6. Handler updates trackers

    order = handler.process_signal(signal, current_price=2500.0)

    assert order is not None
    assert order.correlation_id in handler.order_tracker._orders

    # Verify Broker Interaction
    # MockBrokerAdapter stores placed orders
    assert len(broker.orders) == 1

    # Verify Fill Ingestion
    # MockBrokerAdapter simulates immediate fill, so order should be FILLED
    order_state = handler.order_tracker.get_order(order.correlation_id)
    assert order_state.status.value == "FILLED"
    assert order_state.filled_quantity == order.quantity

    # Verify Position Update
    pos = handler.position_tracker.get_position("RELIANCE")
    assert pos.quantity == order.quantity
