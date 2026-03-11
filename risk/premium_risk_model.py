"""Map underlying stop risk into option premium risk and contract sizing."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PremiumRiskResult:
    """Sizing output from underlying-to-premium risk translation."""

    expected_underlying_move: float
    estimated_premium_move: float
    risk_per_contract: float
    risk_budget: float
    max_contracts: int


def map_underlying_stop_to_premium_risk(
    *,
    account_equity: float,
    risk_pct: float,
    underlying_atr: float,
    atr_multiplier: float,
    option_delta: float,
    lot_size: int,
    option_gamma: float = 0.0,
) -> PremiumRiskResult:
    """
    Convert underlying ATR-based stop to option premium risk and contract cap.

    Premium move approximation:
      dPremium ~= delta * dUnderlying + 0.5 * gamma * dUnderlying^2
    """
    equity = max(account_equity, 0.0)
    risk_budget = equity * max(risk_pct, 0.0)
    move = max(underlying_atr, 0.0) * max(atr_multiplier, 0.0)

    premium_move = abs(option_delta) * move + 0.5 * abs(option_gamma) * (move ** 2)
    premium_move = max(premium_move, 0.01)

    contract_risk = premium_move * max(lot_size, 1)
    max_contracts = int(math.floor(risk_budget / contract_risk)) if contract_risk > 0 else 0
    max_contracts = max(0, max_contracts)

    return PremiumRiskResult(
        expected_underlying_move=move,
        estimated_premium_move=premium_move,
        risk_per_contract=contract_risk,
        risk_budget=risk_budget,
        max_contracts=max_contracts,
    )

