import pytest
from datetime import datetime
from dataclasses import replace
from core.execution.position_tracker import PositionTracker
from core.execution.pnl_tracker import PnLTracker
from core.execution.order_lifecycle import FillEvent


@pytest.fixture
def trackers():
    pos_tracker = PositionTracker()
    pnl_tracker = PnLTracker(pos_tracker)
    return pos_tracker, pnl_tracker


def create_fill(symbol, qty, price, side):
    return FillEvent(
        fill_id="fill_1", order_id="ord_1", symbol=symbol,
        quantity=qty, price=price, timestamp=datetime.now(),
        side=side, fee=0.0
    )


def test_option_pnl_with_multiplier(trackers):
    pos_tracker, pnl_tracker = trackers
    symbol = "NIFTY28JAN2522500CE"

    # 1. Buy 100 qty @ 100
    f1 = create_fill(symbol, 100, 100.0, "BUY")
    pos_tracker.update_from_fill(f1, persist=False)

    # Inject multiplier 50
    current_pos = pos_tracker.get_position(symbol)
    new_instrument = replace(current_pos.instrument, multiplier=50.0)
    new_pos = replace(current_pos, instrument=new_instrument)
    pos_tracker._positions[symbol] = new_pos

    # 2. Sell 100 qty @ 110
    f2 = create_fill(symbol, 100, 110.0, "SELL")
    realized_pnl = pos_tracker.update_from_fill(f2, persist=False)

    # PnL = (110 - 100) * 100 * 50 = 50,000
    assert realized_pnl == 50000.0


def test_option_unrealized_pnl(trackers):
    pos_tracker, pnl_tracker = trackers
    symbol = "NIFTY28JAN2522500CE"

    # Buy 10 qty @ 100
    f1 = create_fill(symbol, 10, 100.0, "BUY")
    pos_tracker.update_from_fill(f1, persist=False)

    current_pos = pos_tracker.get_position(symbol)
    new_instrument = replace(current_pos.instrument, multiplier=50.0)
    new_pos = replace(current_pos, instrument=new_instrument)
    pos_tracker._positions[symbol] = new_pos

    unrealized = pnl_tracker.get_unrealized_pnl({symbol: 120.0}, symbol)
    assert unrealized == 10000.0
