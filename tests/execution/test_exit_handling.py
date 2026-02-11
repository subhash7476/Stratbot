import pytest
from datetime import datetime
from core.events import SignalEvent, SignalType
from core.execution.order_factory import OrderFactory, OrderFactoryError
from core.execution.position_models import Position, PositionSide
from core.execution.order_models import OrderSide


def test_exit_long_position():
    """Test EXIT signal on LONG position converts to SELL order."""
    signal = SignalEvent(
        strategy_id="test_strat",
        symbol="INFY",
        timestamp=datetime.now(),
        signal_type=SignalType.EXIT,
        confidence=1.0,
        metadata={}
    )

    position = Position(
        symbol="INFY",
        side=PositionSide.LONG,
        quantity=100.0,
        avg_price=1500.0
    )

    order = OrderFactory.create_order(signal, current_position=position)

    assert order.side == OrderSide.SELL
    assert order.quantity == 100
    assert order.symbol == "INFY"


def test_exit_short_position():
    """Test EXIT signal on SHORT position converts to BUY order."""
    signal = SignalEvent(
        strategy_id="test_strat",
        symbol="INFY",
        timestamp=datetime.now(),
        signal_type=SignalType.EXIT,
        confidence=1.0,
        metadata={}
    )

    position = Position(
        symbol="INFY",
        side=PositionSide.SHORT,
        quantity=50.0,
        avg_price=1500.0
    )

    order = OrderFactory.create_order(signal, current_position=position)

    assert order.side == OrderSide.BUY
    assert order.quantity == 50


def test_exit_flat_position_rejection():
    """Test EXIT signal on FLAT position raises error."""
    signal = SignalEvent(
        strategy_id="test_strat",
        symbol="INFY",
        timestamp=datetime.now(),
        signal_type=SignalType.EXIT,
        confidence=1.0,
        metadata={}
    )

    position = Position(symbol="INFY", side=PositionSide.FLAT, quantity=0.0)

    with pytest.raises(OrderFactoryError, match="position is FLAT"):
        OrderFactory.create_order(signal, current_position=position)


def test_exit_no_position_rejection():
    """Test EXIT signal with no position context raises error."""
    signal = SignalEvent(
        strategy_id="test_strat",
        symbol="INFY",
        timestamp=datetime.now(),
        signal_type=SignalType.EXIT,
        confidence=1.0,
        metadata={}
    )

    with pytest.raises(OrderFactoryError, match="position is FLAT"):
        OrderFactory.create_order(signal, current_position=None)
