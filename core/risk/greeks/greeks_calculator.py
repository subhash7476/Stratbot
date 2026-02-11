from typing import Optional
from core.instruments.instrument_base import Instrument
from core.instruments.option import Option
from core.instruments.future import Future
from core.instruments.equity import Equity
from core.risk.greeks.greeks_model import Greeks
from core.risk.greeks.black76_engine import Black76Engine


class GreeksCalculator:
    """
    Calculates Greeks for various instrument types.
    """

    @staticmethod
    def calculate(instrument: Instrument,
                  quantity: float,
                  underlying_price: float,
                  volatility: float = 0.20,  # Default 20% if not provided
                  time_to_expiry: float = 0.0,  # Years
                  risk_free_rate: float = 0.05) -> Greeks:
        """
        Compute Greeks for a given instrument and quantity.
        """

        # 1. Equity
        if isinstance(instrument, Equity):
            # Delta = 1 (per share), others 0
            return Greeks(
                delta=quantity,
                gamma=0.0,
                vega=0.0,
                theta=0.0,
                rho=0.0
            )

        # 2. Future
        elif isinstance(instrument, Future):
            # Delta = 1 * multiplier (approx for futures close to expiry/spot)
            # Technically Delta of future wrt spot is e^(rT), but often treated as 1 for delta-one.
            # We will use quantity * multiplier.
            multiplier = getattr(instrument, 'multiplier', 1.0)
            return Greeks(
                delta=quantity * multiplier,
                gamma=0.0,
                vega=0.0,
                theta=0.0,
                rho=0.0
            )

        # 3. Option
        elif isinstance(instrument, Option):
            multiplier = getattr(instrument, 'multiplier', 1.0)

            # Calculate per-unit Greeks
            unit_greeks = Black76Engine.calculate_greeks(
                F=underlying_price,
                K=instrument.strike,
                T=time_to_expiry,
                r=risk_free_rate,
                sigma=volatility,
                option_type=instrument.option_type
            )

            # Scale by quantity and multiplier
            return unit_greeks * (quantity * multiplier)

        # Default / Unknown
        return Greeks(0.0, 0.0, 0.0, 0.0, 0.0)
