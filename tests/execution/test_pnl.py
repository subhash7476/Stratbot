"""
Test PnL Tracker
"""
import pytest
from core.execution.order_lifecycle import FillEvent
from core.execution.position_tracker import PositionTracker
from core.execution.pnl_tracker import PnLTracker
from datetime import datetime


@pytest.fixture
def tracker_setup():
    pos_tracker = PositionTracker()
    pnl_tracker = PnLTracker(pos_tracker)
    return pos_tracker, pnl_tracker


def create_fill(symbol, qty, price, side):
    return FillEvent(
        fill_id="fill_1", order_id="ord_1", symbol=symbol,
        quantity=qty, price=price, timestamp=datetime.now(),
        side=side, fee=0.0
    )


def test_long_profit(tracker_setup):
    pos, pnl = tracker_setup

    # Buy 10 @ 100
    f1 = create_fill("AAPL", 10, 100.0, "BUY")
    r1 = pos.update_from_fill(f1, persist=False)
    pnl.update(f1, r1)

    assert pnl.get_realized_pnl() == 0.0

    # Sell 10 @ 110
    f2 = create_fill("AAPL", 10, 110.0, "SELL")
    r2 = pos.update_from_fill(f2, persist=False)
    pnl.update(f2, r2)

    assert r2 == 100.0  # (110 - 100) * 10
    assert pnl.get_realized_pnl() == 100.0


def test_short_loss(tracker_setup):
    pos, pnl = tracker_setup

    # Sell 10 @ 100
    f1 = create_fill("AAPL", 10, 100.0, "SELL")
    r1 = pos.update_from_fill(f1, persist=False)
    pnl.update(f1, r1)

    # Buy 10 @ 110
    f2 = create_fill("AAPL", 10, 110.0, "BUY")
    r2 = pos.update_from_fill(f2, persist=False)
    pnl.update(f2, r2)

    # Loss of 100: (100 - 110) * 10
    assert r2 == -100.0
    assert pnl.get_realized_pnl() == -100.0


def test_partial_close_and_unrealized(tracker_setup):
    pos, pnl = tracker_setup

    # Buy 10 @ 100
    f1 = create_fill("AAPL", 10, 100.0, "BUY")
    pos.update_from_fill(f1, persist=False)

    # Sell 5 @ 120
    f2 = create_fill("AAPL", 5, 120.0, "SELL")
    r2 = pos.update_from_fill(f2, persist=False)
    pnl.update(f2, r2)

    assert r2 == 100.0  # (120 - 100) * 5
    assert pnl.get_realized_pnl() == 100.0

    # Remaining 5 @ 100. Current price 130.
    unrealized = pnl.get_unrealized_pnl({"AAPL": 130.0})
    assert unrealized == 150.0  # (130 - 100) * 5


def test_flip_position(tracker_setup):
    pos, pnl = tracker_setup

    # Buy 10 @ 100
    f1 = create_fill("AAPL", 10, 100.0, "BUY")
    pos.update_from_fill(f1, persist=False)

    # Sell 15 @ 110 (Close 10, Open Short 5)
    f2 = create_fill("AAPL", 15, 110.0, "SELL")
    r2 = pos.update_from_fill(f2, persist=False)
    pnl.update(f2, r2)

    # Realized on 10 closed: (110 - 100) * 10 = 100
    assert r2 == 100.0
    assert pos.net_quantity("AAPL") == -5

    # Check unrealized on new short position. Price 105.
    # Short 5 @ 110. Current 105. Profit 25.
    unrealized = pnl.get_unrealized_pnl({"AAPL": 105.0})
    assert unrealized == 25.0
