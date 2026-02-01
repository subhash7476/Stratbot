from core.execution.risk_manager import RiskManager
from core.events import SignalEvent, SignalType
from datetime import datetime

def test_daily_trade_limit():
    rm = RiskManager(max_daily_trades=2)
    signal = SignalEvent("test", "SYM", datetime.now(), SignalType.BUY, 0.8)
    
    assert rm.validate_signal(signal, 100000) == True
    rm.record_trade()
    assert rm.validate_signal(signal, 100000) == True
    rm.record_trade()
    assert rm.validate_signal(signal, 100000) == False
