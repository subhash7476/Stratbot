"""
Test Margin Tracker
"""
import pytest
from core.execution.position_tracker import PositionTracker
from core.execution.margin_tracker import MarginTracker
from core.execution.order_lifecycle import FillEvent
from datetime import datetime


def test_margin_calculation():
    pos = PositionTracker()
    margin = MarginTracker(pos, margin_rate=0.2)

    # Buy 10 @ 100
    fill = FillEvent("f1", "o1", "AAPL", 10, 100.0, datetime.now(), "BUY", 0.0)
    pos.update_from_fill(fill, persist=False)

    # Exposure: 10 * 100 = 1000
    # Margin: 1000 * 0.2 = 200
    assert margin.get_exposure({"AAPL": 100.0}) == 1000.0
    assert margin.get_used_margin({"AAPL": 100.0}) == 200.0

    # Price moves to 120
    assert margin.get_exposure({"AAPL": 120.0}) == 1200.0
    assert margin.get_used_margin({"AAPL": 120.0}) == 240.0
