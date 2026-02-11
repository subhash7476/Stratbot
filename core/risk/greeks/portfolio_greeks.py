from typing import Dict, List, Optional
from core.execution.position_tracker import PositionTracker
from core.execution.position_models import Position
from core.risk.greeks.greeks_model import Greeks
from core.risk.greeks.greeks_calculator import GreeksCalculator
from core.instruments.instrument_parser import Instrument


class PortfolioGreeks:
    """
    Aggregates Greeks across a portfolio of positions.
    """

    def __init__(self, position_tracker: PositionTracker):
        self.position_tracker = position_tracker

    def calculate_portfolio_greeks(self,
                                   market_prices: Dict[str, float],
                                   volatilities: Dict[str, float],
                                   time_to_expiry_map: Dict[str, float],
                                   risk_free_rate: float = 0.05) -> Greeks:
        """
        Calculate net Greeks for the entire portfolio.

        Args:
            market_prices: Map of symbol (or underlying symbol) to current price.
            volatilities: Map of symbol to IV.
            time_to_expiry_map: Map of symbol to T (years).
        """
        net_greeks = Greeks(0.0, 0.0, 0.0, 0.0, 0.0)

        positions = self.position_tracker.get_all_positions()
        for position in positions:
            greeks = self._calculate_position_greeks(
                position, market_prices, volatilities, time_to_expiry_map, risk_free_rate
            )
            net_greeks += greeks

        return net_greeks

    def _calculate_position_greeks(self,
                                   position: Position,
                                   market_prices: Dict[str, float],
                                   volatilities: Dict[str, float],
                                   time_to_expiry_map: Dict[str, float],
                                   risk_free_rate: float) -> Greeks:

        instrument = position.instrument
        symbol = instrument.symbol

        # Determine underlying price
        # For Options, we need underlying price. For Equity/Future, we need their own price.
        # We assume market_prices contains the necessary price keyed by symbol or underlying symbol.

        price = 0.0
        if hasattr(instrument, 'underlying') and instrument.underlying:
            price = market_prices.get(instrument.underlying, 0.0)
        else:
            price = market_prices.get(symbol, 0.0)

        if price <= 0:
            # Cannot calculate without price
            return Greeks(0.0, 0.0, 0.0, 0.0, 0.0)

        vol = volatilities.get(symbol, 0.20)  # Default 20%
        tte = time_to_expiry_map.get(symbol, 0.0)

        # Net quantity (Long - Short)
        qty = position.quantity if position.side.name == 'LONG' else -position.quantity
        if position.side.name == 'FLAT':
            qty = 0.0

        return GreeksCalculator.calculate(
            instrument=instrument,
            quantity=qty,
            underlying_price=price,
            volatility=vol,
            time_to_expiry=tte,
            risk_free_rate=risk_free_rate
        )
