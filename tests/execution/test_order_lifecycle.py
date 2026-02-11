import pytest
from datetime import datetime
from uuid import uuid4
from core.execution.order_models import NormalizedOrder, OrderSide, OrderType, InstrumentType
from core.execution.order_lifecycle import OrderStatus, FillEvent
from core.execution.order_tracker import OrderTracker
from core.execution.position_tracker import PositionTracker
from core.execution.position_models import PositionSide


def test_order_tracker_partial_fill_lifecycle():
    tracker = OrderTracker()
    order = NormalizedOrder(
        symbol="RELIANCE",
        instrument_type=InstrumentType.EQUITY,
        side=OrderSide.BUY,
        quantity=100,
        order_type=OrderType.MARKET,
        strategy_id="test",
        signal_id="sig_1",
        timestamp=datetime.now(),
        correlation_id=uuid4()
    )

    tracker.register(order)
    assert tracker.get_status(order.correlation_id) == OrderStatus.CREATED
    assert tracker.remaining_qty(order.correlation_id) == 100.0

    # First partial fill
    fill1 = FillEvent(order.correlation_id, 40.0, 2500.0, datetime.now())
    status = tracker.add_fill(fill1)

    assert status == OrderStatus.PARTIALLY_FILLED
    assert tracker.remaining_qty(order.correlation_id) == 60.0

    # Final fill
    fill2 = FillEvent(order.correlation_id, 60.0, 2510.0, datetime.now())
    status = tracker.add_fill(fill2)

    assert status == OrderStatus.FILLED
    assert tracker.remaining_qty(order.correlation_id) == 0.0


def test_position_tracker_incremental_avg_price():
    pos_tracker = PositionTracker()
    symbol = "INFY"

    # Fill 1: 50 qty @ 1500
    pos_tracker.update_from_fill(symbol, OrderSide.BUY, 50.0, 1500.0)
    pos = pos_tracker.get_position(symbol)
    assert pos.quantity == 50.0
    assert pos.avg_price == 1500.0

    # Fill 2: 50 qty @ 1600
    pos_tracker.update_from_fill(symbol, OrderSide.BUY, 50.0, 1600.0)
    pos = pos_tracker.get_position(symbol)
    assert pos.quantity == 100.0
    # Weighted average: (50*1500 + 50*1600) / 100 = 1550
    assert pos.avg_price == 1550.0


def test_exit_order_partial_fills_to_flat():
    pos_tracker = PositionTracker()
    symbol = "TCS"

    # Establish initial position: LONG 100 @ 3000
    pos_tracker.update_from_fill(symbol, OrderSide.BUY, 100.0, 3000.0)

    # Partial EXIT fill 1: SELL 40 @ 3100
    pos_tracker.update_from_fill(symbol, OrderSide.SELL, 40.0, 3100.0)
    pos = pos_tracker.get_position(symbol)
    assert pos.quantity == 60.0
    assert pos.side == PositionSide.LONG
    assert pos.avg_price == 3000.0  # Avg price doesn't change on reduction

    # Partial EXIT fill 2: SELL 60 @ 3150
    pos_tracker.update_from_fill(symbol, OrderSide.SELL, 60.0, 3150.0)
    pos = pos_tracker.get_position(symbol)
    assert pos.quantity == 0.0
    assert pos.side == PositionSide.FLAT


def test_position_flip_with_partial_fills():
    pos_tracker = PositionTracker()
    symbol = "SBIN"

    # Start LONG 10
    pos_tracker.update_from_fill(symbol, OrderSide.BUY, 10.0, 500.0)

    # Partial fill that doesn't flip yet: SELL 5
    pos_tracker.update_from_fill(symbol, OrderSide.SELL, 5.0, 510.0)
    assert pos_tracker.net_quantity(symbol) == 5.0

    # Partial fill that flips: SELL 10 (Resulting in SHORT 5)
    pos_tracker.update_from_fill(symbol, OrderSide.SELL, 10.0, 520.0)
    pos = pos_tracker.get_position(symbol)
    assert pos.side == PositionSide.SHORT
    assert pos.quantity == 5.0
    assert pos.avg_price == 520.0
