import pytest
from datetime import datetime
from core.events import SignalEvent, SignalType
from core.execution.order_factory import OrderFactory, OrderFactoryError
from core.execution.order_models import OrderSide, InstrumentType, OrderType


def test_signal_to_normalized_order_mapping():
    signal = SignalEvent(
        strategy_id="breakout_v1",
        symbol="TCS",
        timestamp=datetime.now(),
        signal_type=SignalType.BUY,
        confidence=0.85,
        metadata={"quantity": 50, "signal_id": "sig_123"}
    )

    order = OrderFactory.create_order(signal)

    assert order.symbol == "TCS"
    assert order.side == OrderSide.BUY
    assert order.quantity == 50
    assert order.strategy_id == "breakout_v1"
    assert order.signal_id == "sig_123"
    assert order.instrument_type == InstrumentType.EQUITY
    assert order.order_type == OrderType.MARKET
    assert order.metadata.original_confidence == 0.85
    assert order.correlation_id is not None


def test_unique_correlation_id():
    signal = SignalEvent(
        strategy_id="test",
        symbol="INFY",
        timestamp=datetime.now(),
        signal_type=SignalType.BUY,
        confidence=0.5,
        metadata={"signal_id": "sig_abc"}
    )

    order1 = OrderFactory.create_order(signal)
    order2 = OrderFactory.create_order(signal)

    assert order1.correlation_id != order2.correlation_id


def test_malformed_signal_handling():
    # Signal missing quantity in metadata (should default to 0 in Phase 1 as per factory logic)
    signal = SignalEvent(
        strategy_id="test",
        symbol="INFY",
        timestamp=datetime.now(),
        signal_type=SignalType.BUY,
        confidence=0.5,
        metadata={"signal_id": "sig_no_qty"}
    )

    order = OrderFactory.create_order(signal)
    assert order.quantity == 0
