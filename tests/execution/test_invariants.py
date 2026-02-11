import pytest
from datetime import datetime
from unittest.mock import MagicMock
from core.execution.handler import ExecutionHandler, ExecutionConfig, ExecutionMode
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
def handler(mock_db, mock_clock, mock_broker):
    config = ExecutionConfig(mode=ExecutionMode.DRY_RUN)
    # Patch load_db_state to avoid DB calls in init
    ExecutionHandler._load_positions_from_db = MagicMock()
    return ExecutionHandler(mock_db, mock_clock, mock_broker, config, load_db_state=False)

def test_enforce_signal_idempotency(handler):
    signal = SignalEvent(
        strategy_id="test_strat",
        symbol="RELIANCE",
        timestamp=datetime.now(),
        signal_type=SignalType.BUY,
        confidence=0.9,
        metadata={"signal_id": "unique_sig_1"}
    )
    
    # First time should pass (or return None in DRY_RUN, but shouldn't raise)
    handler.process_signal(signal, 2500.0)
    
    # Second time with same signal_id must raise ExecutionRuleError
    with pytest.raises(ExecutionRuleError, match="Idempotency violation"):
        handler.process_signal(signal, 2500.0)

def test_enforce_risk_clearance(handler):
    signal = SignalEvent(
        strategy_id="test_strat",
        symbol="RELIANCE",
        timestamp=datetime.now(),
        signal_type=SignalType.BUY,
        confidence=0.9,
        metadata={"signal_id": "unique_sig_2"}
    )
    
    # Mock risk check to fail
    handler._check_risk_limits = MagicMock(return_value=False)
    
    with pytest.raises(ExecutionRuleError, match="Risk clearance violation"):
        handler.process_signal(signal, 2500.0)

def test_enforce_execution_authority_recursion(handler):
    """Verifies that nested calls to process_signal are blocked by the authority guard."""
    signal = SignalEvent(
        strategy_id="test_strat",
        symbol="RELIANCE",
        timestamp=datetime.now(),
        signal_type=SignalType.BUY,
        confidence=0.9,
        metadata={"signal_id": "unique_sig_3"}
    )
    
    # We simulate an authority violation by manually tripping the internal flag
    # or by attempting a recursive call.
    handler._processing_signal = True
    
    with pytest.raises(ExecutionRuleError, match="Authority violation"):
        handler.process_signal(signal, 2500.0)
