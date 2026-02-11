import pytest
from datetime import datetime
from unittest.mock import MagicMock
from core.execution.handler import ExecutionHandler, ExecutionConfig, ExecutionMode
from core.execution.risk_manager import RiskManager
from core.execution.risk_models import RiskStatus
from core.execution.rules import ExecutionRuleError
from core.events import SignalEvent, SignalType
from core.clock import Clock

@pytest.fixture
def mock_db():
    return MagicMock()

@pytest.fixture
def mock_clock():
    clock = MagicMock(spec=Clock)
    clock.now.return_value = datetime.now()
    return clock

@pytest.fixture
def mock_broker():
    return MagicMock()

@pytest.fixture
def risk_manager():
    return RiskManager(
        max_order_quantity=100,
        allowed_symbols={"RELIANCE", "TCS"},
        denied_symbols={"ZOMATO"}
    )

@pytest.fixture
def handler(mock_db, mock_clock, mock_broker, risk_manager):
    config = ExecutionConfig(mode=ExecutionMode.DRY_RUN, max_trades_per_day=5)
    ExecutionHandler._load_positions_from_db = MagicMock()
    return ExecutionHandler(
        mock_db, mock_clock, mock_broker, 
        risk_manager=risk_manager, 
        config=config, 
        load_db_state=False
    )

def test_risk_rejection_quantity(handler):
    signal = SignalEvent(
        strategy_id="test",
        symbol="RELIANCE",
        timestamp=datetime.now(),
        signal_type=SignalType.BUY,
        confidence=0.9,
        metadata={"quantity": 500, "signal_id": "sig_qty_fail"}
    )
    
    with pytest.raises(ExecutionRuleError, match="Pre-trade risk rejection: Order quantity 500 exceeds limit 100"):
        handler.process_signal(signal, 2500.0)

def test_risk_rejection_symbol_not_allowed(handler):
    signal = SignalEvent(
        strategy_id="test",
        symbol="INFY",
        timestamp=datetime.now(),
        signal_type=SignalType.BUY,
        confidence=0.9,
        metadata={"quantity": 10, "signal_id": "sig_sym_fail"}
    )
    
    with pytest.raises(ExecutionRuleError, match="Symbol INFY is not in the allow list"):
        handler.process_signal(signal, 1500.0)

def test_risk_rejection_symbol_denied(handler):
    signal = SignalEvent(
        strategy_id="test",
        symbol="ZOMATO",
        timestamp=datetime.now(),
        signal_type=SignalType.BUY,
        confidence=0.9,
        metadata={"quantity": 10, "signal_id": "sig_sym_deny"}
    )
    
    # Even if we added it to allow list, deny list should take precedence or we just test it's blocked
    handler.risk_manager.allowed_symbols.add("ZOMATO")
    
    with pytest.raises(ExecutionRuleError, match="Symbol ZOMATO is in the deny list"):
        handler.process_signal(signal, 150.0)

def test_risk_rejection_daily_limit(handler):
    handler._trades_today = 5 # limit is 5
    signal = SignalEvent(
        strategy_id="test",
        symbol="RELIANCE",
        timestamp=datetime.now(),
        signal_type=SignalType.BUY,
        confidence=0.9,
        metadata={"quantity": 10, "signal_id": "sig_limit_fail"}
    )
    
    with pytest.raises(ExecutionRuleError, match="Daily trade limit \(5\) reached"):
        handler.process_signal(signal, 2500.0)

def test_risk_approval_pass_through(handler):
    signal = SignalEvent(
        strategy_id="test",
        symbol="TCS",
        timestamp=datetime.now(),
        signal_type=SignalType.BUY,
        confidence=0.9,
        metadata={"quantity": 50, "signal_id": "sig_pass"}
    )
    
    order = handler.process_signal(signal, 3500.0)
    assert order is not None
    assert order.symbol == "TCS"
    assert order.quantity == 50
