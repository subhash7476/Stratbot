# core/greeks_calculator.py
"""
Greeks Calculator using Black-Scholes Model
============================================
Calculate option Greeks (Delta, Gamma, Theta, Vega, Rho) using py_vollib library.

Uses Black-Scholes model for European-style options.
For Indian markets (NSE/BSE), this is a reasonable approximation for short-dated options.

Dependencies:
    pip install py_vollib

Author: Trading Bot Pro
Version: 1.0
Date: 2026-01-17
"""

from py_vollib.black_scholes import black_scholes as bs
from py_vollib.black_scholes.greeks import analytical as greeks
from py_vollib.black_scholes.implied_volatility import implied_volatility as iv_calc
import numpy as np
from datetime import datetime, date
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)


class GreeksCalculator:
    """
    Calculate option Greeks using Black-Scholes model

    Greeks Explained:
    ----------------
    - Delta: Rate of change of option price w.r.t. underlying price
             Range: 0 to 1 (calls), -1 to 0 (puts)
             Example: Delta=0.5 means ₹1 move in stock → ₹0.50 move in option

    - Gamma: Rate of change of delta w.r.t. underlying price
             Higher gamma = faster delta changes (good for scalping, risky for sellers)

    - Theta: Time decay - how much option loses per day
             Always negative for long options
             Example: Theta=-2 means option loses ₹2 per day

    - Vega: Sensitivity to volatility changes
            Example: Vega=5 means 1% increase in IV → ₹5 increase in premium

    - Rho: Sensitivity to interest rate changes (less relevant for intraday)

    Usage:
    ------
    >>> calc = GreeksCalculator()
    >>> greeks = calc.calculate_greeks(
    ...     spot_price=2450,
    ...     strike_price=2500,
    ...     time_to_expiry_days=7,
    ...     implied_volatility=0.25,
    ...     option_type='c'
    ... )
    >>> print(f"Delta: {greeks['delta']:.2f}")
    """

    def __init__(self, risk_free_rate: float = 0.065):
        """
        Initialize Greeks Calculator

        Args:
            risk_free_rate: Annual risk-free rate (default: 6.5% - RBI repo rate)
        """
        self.risk_free_rate = risk_free_rate

    def calculate_greeks(
        self,
        spot_price: float,
        strike_price: float,
        time_to_expiry_days: float,
        implied_volatility: float,
        option_type: str
    ) -> Dict[str, float]:
        """
        Calculate all Greeks for an option

        Args:
            spot_price: Current price of underlying (e.g., 2450)
            strike_price: Strike price of option (e.g., 2500)
            time_to_expiry_days: Days until expiry (e.g., 7)
            implied_volatility: IV as decimal (e.g., 0.25 for 25%)
            option_type: 'c' for call, 'p' for put

        Returns:
            Dictionary with keys:
                - price: Theoretical option price (Black-Scholes)
                - delta: Delta (directional exposure)
                - gamma: Gamma (delta acceleration)
                - theta: Daily theta (time decay per day)
                - vega: Vega per 1% vol change
                - rho: Rho per 1% rate change
                - iv: Implied volatility as percentage

        Example:
            >>> greeks = calc.calculate_greeks(2450, 2500, 7, 0.25, 'c')
            >>> print(f"Delta: {greeks['delta']:.3f}")
            Delta: 0.423
        """
        # Validate inputs
        if spot_price <= 0 or strike_price <= 0:
            logger.error(f"Invalid prices: spot={spot_price}, strike={strike_price}")
            return self._zero_greeks()

        if time_to_expiry_days <= 0:
            logger.warning(f"Option expired (days={time_to_expiry_days})")
            return self._zero_greeks()

        if implied_volatility <= 0 or implied_volatility > 5:
            logger.warning(f"Unusual IV: {implied_volatility}")
            implied_volatility = max(0.01, min(implied_volatility, 5.0))

        # Convert days to years
        T = time_to_expiry_days / 365.0

        # Parameters
        S = float(spot_price)
        K = float(strike_price)
        r = self.risk_free_rate
        sigma = float(implied_volatility)
        flag = option_type.lower()

        if flag not in ('c', 'p'):
            logger.error(f"Invalid option_type: {option_type}. Must be 'c' or 'p'")
            return self._zero_greeks()

        try:
            # Calculate theoretical price
            price = bs(flag, S, K, T, r, sigma)

            # Calculate Greeks
            delta = greeks.delta(flag, S, K, T, r, sigma)
            gamma = greeks.gamma(flag, S, K, T, r, sigma)
            theta = greeks.theta(flag, S, K, T, r, sigma)
            vega = greeks.vega(flag, S, K, T, r, sigma)
            rho = greeks.rho(flag, S, K, T, r, sigma)

            return {
                'price': round(price, 2),
                'delta': round(delta, 4),
                'gamma': round(gamma, 6),
                'theta': round(theta / 365, 4),  # Convert to daily theta
                'vega': round(vega / 100, 4),    # Vega per 1% vol change
                'rho': round(rho / 100, 4),      # Rho per 1% rate change
                'iv': round(sigma * 100, 2)      # IV as percentage
            }

        except Exception as e:
            logger.error(f"Error calculating Greeks: {e}")
            logger.error(f"Inputs: S={S}, K={K}, T={T}, r={r}, sigma={sigma}, flag={flag}")
            return self._zero_greeks()

    def calculate_implied_volatility(
        self,
        market_price: float,
        spot_price: float,
        strike_price: float,
        time_to_expiry_days: float,
        option_type: str,
        initial_guess: float = 0.25
    ) -> float:
        """
        Calculate implied volatility from market price using Newton-Raphson method

        Args:
            market_price: Current market price of option
            spot_price: Current price of underlying
            strike_price: Strike price
            time_to_expiry_days: Days to expiry
            option_type: 'c' for call, 'p' for put
            initial_guess: Starting IV guess (default: 25%)

        Returns:
            Implied volatility as decimal (e.g., 0.25 for 25%)
            Returns initial_guess if calculation fails

        Example:
            >>> iv = calc.calculate_implied_volatility(
            ...     market_price=50,
            ...     spot_price=2450,
            ...     strike_price=2500,
            ...     time_to_expiry_days=7,
            ...     option_type='c'
            ... )
            >>> print(f"IV: {iv*100:.1f}%")
            IV: 23.4%
        """
        # Validate inputs
        if market_price <= 0:
            logger.error(f"Invalid market_price: {market_price}")
            return initial_guess

        if time_to_expiry_days <= 0:
            logger.warning(f"Option expired (days={time_to_expiry_days})")
            return 0.0

        # Check intrinsic value
        intrinsic = self._calculate_intrinsic_value(
            spot_price, strike_price, option_type
        )
        if market_price < intrinsic * 0.95:  # 5% tolerance
            logger.warning(f"Market price {market_price} < intrinsic {intrinsic}")
            return initial_guess

        T = time_to_expiry_days / 365.0
        flag = option_type.lower()

        try:
            iv = iv_calc(
                price=float(market_price),
                S=float(spot_price),
                K=float(strike_price),
                t=T,
                r=self.risk_free_rate,
                flag=flag
            )

            # Sanity check
            if iv <= 0 or iv > 5:
                logger.warning(f"Unusual IV calculated: {iv}")
                return initial_guess

            return round(iv, 4)

        except Exception as e:
            logger.warning(f"IV calculation failed: {e}. Using guess {initial_guess}")
            return initial_guess

    def calculate_days_to_expiry(self, expiry_date: date) -> float:
        """
        Calculate days to expiry from today

        Args:
            expiry_date: Expiry date of option

        Returns:
            Number of days to expiry (can be fractional for intraday)

        Example:
            >>> from datetime import date, timedelta
            >>> expiry = date.today() + timedelta(days=7)
            >>> days = calc.calculate_days_to_expiry(expiry)
            >>> print(f"Days to expiry: {days}")
            Days to expiry: 7.0
        """
        if isinstance(expiry_date, str):
            expiry_date = datetime.strptime(expiry_date, '%Y-%m-%d').date()

        today = date.today()
        delta = expiry_date - today

        # Add fractional day based on current time (for intraday accuracy)
        now = datetime.now()
        fraction_of_day = (now.hour * 3600 + now.minute * 60 + now.second) / 86400

        return delta.days + (1 - fraction_of_day)

    def _calculate_intrinsic_value(
        self,
        spot_price: float,
        strike_price: float,
        option_type: str
    ) -> float:
        """Calculate intrinsic value of option"""
        if option_type.lower() == 'c':
            return max(0, spot_price - strike_price)
        else:
            return max(0, strike_price - spot_price)

    def _zero_greeks(self) -> Dict[str, float]:
        """Return zero Greeks when calculation fails"""
        return {
            'price': 0.0,
            'delta': 0.0,
            'gamma': 0.0,
            'theta': 0.0,
            'vega': 0.0,
            'rho': 0.0,
            'iv': 0.0
        }

    def moneyness(self, spot_price: float, strike_price: float) -> str:
        """
        Determine if option is ITM, ATM, or OTM

        Args:
            spot_price: Current underlying price
            strike_price: Strike price

        Returns:
            'ITM', 'ATM', or 'OTM'

        Example:
            >>> calc.moneyness(2450, 2400)
            'ITM'  # For a call
        """
        diff_pct = abs((spot_price - strike_price) / spot_price) * 100

        if diff_pct < 1:  # Within 1%
            return 'ATM'
        elif spot_price > strike_price:
            return 'ITM'  # For calls
        else:
            return 'OTM'


# Convenience functions for quick calculations

def quick_greeks(
    spot: float,
    strike: float,
    days: float,
    iv: float,
    option_type: str
) -> Dict[str, float]:
    """
    Quick Greeks calculation without creating calculator instance

    Args:
        spot: Spot price
        strike: Strike price
        days: Days to expiry
        iv: Implied volatility (decimal, e.g., 0.25)
        option_type: 'c' or 'p'

    Returns:
        Dictionary of Greeks

    Example:
        >>> g = quick_greeks(2450, 2500, 7, 0.25, 'c')
        >>> print(f"Delta: {g['delta']}")
    """
    calc = GreeksCalculator()
    return calc.calculate_greeks(spot, strike, days, iv, option_type)


def quick_iv(
    market_price: float,
    spot: float,
    strike: float,
    days: float,
    option_type: str
) -> float:
    """
    Quick IV calculation

    Example:
        >>> iv = quick_iv(50, 2450, 2500, 7, 'c')
        >>> print(f"IV: {iv*100:.1f}%")
    """
    calc = GreeksCalculator()
    return calc.calculate_implied_volatility(
        market_price, spot, strike, days, option_type
    )


if __name__ == "__main__":
    # Test the calculator
    print("=== Greeks Calculator Test ===\n")

    calc = GreeksCalculator()

    # Example: RELIANCE 2500 CE, 7 days to expiry, IV=25%
    greeks_data = calc.calculate_greeks(
        spot_price=2450,
        strike_price=2500,
        time_to_expiry_days=7,
        implied_volatility=0.25,
        option_type='c'
    )

    print("RELIANCE 2500 CE")
    print(f"Spot: ₹2,450 | Strike: ₹2,500 | Days: 7 | IV: 25%\n")
    print(f"Theoretical Price: ₹{greeks_data['price']:.2f}")
    print(f"Delta: {greeks_data['delta']:.4f}")
    print(f"Gamma: {greeks_data['gamma']:.6f}")
    print(f"Theta (daily): ₹{greeks_data['theta']:.2f}")
    print(f"Vega: ₹{greeks_data['vega']:.2f} per 1% IV change")
    print(f"\nMoneyness: {calc.moneyness(2450, 2500)}")
