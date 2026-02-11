import math
from typing import Literal
from core.risk.greeks.greeks_model import Greeks


class Black76Engine:
    """
    Black-76 Model implementation for pricing options on futures/indexes.
    """

    @staticmethod
    def _norm_cdf(x: float) -> float:
        """Cumulative distribution function for standard normal distribution."""
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

    @staticmethod
    def _norm_pdf(x: float) -> float:
        """Probability density function for standard normal distribution."""
        return math.exp(-x**2 / 2.0) / math.sqrt(2.0 * math.pi)

    @classmethod
    def calculate_greeks(cls,
                         F: float,       # Forward/Underlying Price
                         K: float,       # Strike Price
                         T: float,       # Time to Expiry (years)
                         r: float,       # Risk-free Rate
                         sigma: float,   # Volatility
                         option_type: Literal['CE', 'PE']) -> Greeks:
        """
        Calculate Greeks using Black-76 model.
        """
        if T <= 0:
            # Expired or expiring immediately
            # Intrinsic delta, others 0
            if option_type == 'CE':
                delta = 1.0 if F > K else 0.0
            else:
                delta = -1.0 if K > F else 0.0
            return Greeks(delta, 0.0, 0.0, 0.0, 0.0)

        if sigma <= 0:
            # Zero volatility case
            if option_type == 'CE':
                delta = 1.0 if F > K else 0.0
            else:
                delta = -1.0 if K > F else 0.0
            return Greeks(delta, 0.0, 0.0, 0.0, 0.0)

        sqrt_T = math.sqrt(T)
        d1 = (math.log(F / K) + (0.5 * sigma**2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T

        exp_neg_rT = math.exp(-r * T)
        N_d1 = cls._norm_cdf(d1)
        N_d2 = cls._norm_cdf(d2)
        N_neg_d1 = cls._norm_cdf(-d1)
        N_neg_d2 = cls._norm_cdf(-d2)
        pdf_d1 = cls._norm_pdf(d1)

        # Common Gamma and Vega
        gamma = (exp_neg_rT * pdf_d1) / (F * sigma * sqrt_T)
        vega = F * exp_neg_rT * pdf_d1 * sqrt_T * 0.01  # Scaled for 1% vol change

        if option_type == 'CE':
            delta = exp_neg_rT * N_d1

            # Theta (Black-76 Call)
            # theta = - (F * e^-rT * n(d1) * sigma) / (2 * sqrt(T)) + r * F * e^-rT * N(d1) - r * K * e^-rT * N(d2)
            # Simplified for daily theta (divide by 365)
            term1 = -(F * exp_neg_rT * pdf_d1 * sigma) / (2 * sqrt_T)
            # Note: Black76 usually has different theta terms than Black-Scholes
            term2 = r * F * exp_neg_rT * N_d1
            # Actually, standard Black-76 Theta:
            # Theta_call = - (F * sigma * e^-rT * n(d1)) / (2 * sqrt(T)) + r * (F * e^-rT * N(d1) - K * e^-rT * N(d2))
            # But often we just want the time decay of the option price.
            # Let's use the standard approximation.
            theta = (term1 - r * K * exp_neg_rT * N_d2 +
                     r * F * exp_neg_rT * N_d1) / 365.0

            # Scaled for 1% rate change (approx)
            rho = (T * K * exp_neg_rT * N_d2) * 0.01

        else:  # PE
            delta = -exp_neg_rT * N_neg_d1

            term1 = -(F * exp_neg_rT * pdf_d1 * sigma) / (2 * sqrt_T)
            theta = (term1 + r * K * exp_neg_rT * N_neg_d2 -
                     r * F * exp_neg_rT * N_neg_d1) / 365.0
            rho = (-T * K * exp_neg_rT * N_neg_d2) * 0.01

        return Greeks(delta, gamma, vega, theta, rho)
