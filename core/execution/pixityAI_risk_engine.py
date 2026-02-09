from typing import Dict, Any, Optional
from core.events import SignalEvent, SignalType
from core.execution.risk_manager import RiskManager

class PixityAIRiskEngine(RiskManager):
    """
    PixityAI specific risk and sizing engine.
    Calculates quantity based on fixed risk per trade and ATR-based SL.
    Enforces STT only on SELL turnover.
    """
    
    def __init__(self, risk_per_trade: float = 500.0, max_daily_trades: int = 10):
        super().__init__(max_daily_trades=max_daily_trades)
        self.risk_per_trade = risk_per_trade 

    def calculate_position(self, signal: SignalEvent, current_equity: float) -> Dict[str, Any]:
        entry_price = signal.metadata.get("entry_price_at_event", 0)
        atr = signal.metadata.get("atr_at_event", 0)
        
        if entry_price <= 0 or atr <= 0:
            return {"quantity": 0, "sl": 0, "tp": 0}
            
        sl_distance = atr
        quantity = int(self.risk_per_trade / sl_distance) if sl_distance > 0 else 0
        
        max_notional = current_equity * 5.0
        if (quantity * entry_price) > max_notional:
            quantity = int(max_notional / entry_price)

        tp_distance = 2.0 * atr
        
        if signal.signal_type == SignalType.BUY:
            sl = entry_price - sl_distance
            tp = entry_price + tp_distance
        else:
            sl = entry_price + sl_distance
            tp = entry_price - tp_distance
            
        return {
            "quantity": quantity,
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "entry": entry_price
        }

    def calculate_costs(self, side: SignalType, price: float, quantity: int) -> float:
        """
        Total costs for a fill.
        STT for equity intraday is 0.025% on the SELL side only.
        """
        stt = 0.0
        if side == SignalType.SELL:
            stt = 0.00025 * (price * quantity)
        return stt

    # Alias for backward compatibility
    calculate_stt = calculate_costs
