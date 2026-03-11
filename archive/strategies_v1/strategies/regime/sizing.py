"""Volatility parity position sizing for regime strategy."""
from typing import Dict, Any


class RegimeRiskEngine:
    """
    Position size = risk_amount / (2 * ATR).
    Risk per trade = 0.75% of capital.
    Entropy-adjusted: high entropy reduces size by 50%.
    """

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.risk_pct = cfg.get('risk_pct_per_trade', 0.0075)
        self.atr_sl_mult = cfg.get('atr_sl_multiplier', 1.5)
        self.atr_tp_mult = cfg.get('atr_tp_multiplier', 2.0)
        self.max_notional_mult = cfg.get('max_notional_multiplier', 2.0)
        self.entropy_threshold = cfg.get('entropy_reduction_threshold', 0.5)

    def calculate_position(
        self,
        capital: float,
        price: float,
        atr: float,
        direction: str,
        entropy: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Calculate position size, SL, and TP.

        Args:
            capital: current account equity
            price: entry price
            atr: current ATR value
            direction: 'BUY' or 'SELL'
            entropy: regime entropy (high = uncertain, reduce size)

        Returns:
            dict with quantity, sl, tp, risk_amount
        """
        if atr <= 0 or price <= 0:
            return {'quantity': 0, 'sl': 0, 'tp': 0, 'risk_amount': 0}

        risk_amount = capital * self.risk_pct

        # Entropy-adjusted sizing: reduce by 50% if regime is unclear
        if entropy > self.entropy_threshold:
            risk_amount *= 0.5

        sl_distance = self.atr_sl_mult * atr
        quantity = int(risk_amount / sl_distance) if sl_distance > 0 else 0

        # Cap at max notional
        max_notional = capital * self.max_notional_mult
        if quantity * price > max_notional:
            quantity = int(max_notional / price)

        if quantity <= 0:
            return {'quantity': 0, 'sl': 0, 'tp': 0, 'risk_amount': 0}

        if direction == 'BUY':
            sl = round(price - sl_distance, 2)
            tp = round(price + self.atr_tp_mult * atr, 2)
        else:
            sl = round(price + sl_distance, 2)
            tp = round(price - self.atr_tp_mult * atr, 2)

        return {
            'quantity': quantity,
            'sl': sl,
            'tp': tp,
            'risk_amount': round(risk_amount, 2),
            'entry': round(price, 2),
        }
