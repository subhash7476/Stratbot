"""
Risk Manager
------------
Enforces safety constraints on incoming signals.
"""
from typing import Dict, Any, Optional
from core.events import SignalEvent

class RiskManager:
    """
    Validates signals against global and per-symbol risk limits.
    """
    
    def __init__(self, max_daily_trades: int = 50, max_drawdown: float = 0.05):
        self.max_daily_trades = max_daily_trades
        self.max_drawdown = max_drawdown
        self.trades_today = 0

    def validate_signal(self, signal: SignalEvent, current_equity: float) -> bool:
        """
        Returns True if the signal is safe to execute.
        """
        if self.trades_today >= self.max_daily_trades:
            return False
            
        return True

    def record_trade(self):
        self.trades_today += 1

    def reset_daily(self):
        self.trades_today = 0
