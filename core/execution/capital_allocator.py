"""
Capital Allocator
-----------------
Determines trade sizes based on strategy confidence and current equity.
"""
class CapitalAllocator:
    """
    Calculates position sizes based on risk parameters.
    """
    
    def __init__(self, total_capital: float, max_risk_per_trade: float = 0.01):
        self.total_capital = total_capital
        self.max_risk_per_trade = max_risk_per_trade

    def calculate_size(self, price: float, confidence: float) -> int:
        """Calculates quantity based on fixed fractional risk."""
        # Simple placeholder logic
        risk_amount = self.total_capital * self.max_risk_per_trade * confidence
        return int(risk_amount / price) if price > 0 else 0
