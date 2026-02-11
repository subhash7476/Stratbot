"""
Test Reconciliation
"""
import pytest
from core.execution.position_tracker import PositionTracker
from core.execution.reconciliation import ReconciliationEngine
from core.execution.order_lifecycle import FillEvent
from datetime import datetime


def test_reconciliation_match():
    pos = PositionTracker()
    recon = ReconciliationEngine(pos)

    # Internal: Long 10
    fill = FillEvent("f1", "o1", "AAPL", 10, 100.0, datetime.now(), "BUY", 0.0)
    pos.update_from_fill(fill, persist=False)

    broker_positions = [{'symbol': 'AAPL', 'quantity': 10, 'side': 'LONG'}]
    alerts = recon.reconcile(broker_positions)
    assert len(alerts) == 0


def test_reconciliation_mismatch():
    pos = PositionTracker()
    recon = ReconciliationEngine(pos)

    # Internal: Long 10
    fill = FillEvent("f1", "o1", "AAPL", 10, 100.0, datetime.now(), "BUY", 0.0)
    pos.update_from_fill(fill, persist=False)

    # Broker: Long 8
    broker_positions = [{'symbol': 'AAPL', 'quantity': 8, 'side': 'LONG'}]
    alerts = recon.reconcile(broker_positions)

    assert len(alerts) == 1
    assert alerts[0].issue == "QUANTITY_MISMATCH"
    assert alerts[0].internal_value == 10.0
    assert alerts[0].broker_value == 8.0


def test_orphaned_position():
    pos = PositionTracker()
    recon = ReconciliationEngine(pos)

    # Internal: Empty

    # Broker: Long 5
    broker_positions = [{'symbol': 'GOOG', 'quantity': 5}]
    alerts = recon.reconcile(broker_positions)

    assert len(alerts) == 1
    assert alerts[0].issue == "ORPHANED_BROKER_POSITION"
    assert alerts[0].symbol == "GOOG"
