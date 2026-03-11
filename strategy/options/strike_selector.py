"""Option strike selection engine for directional entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class OptionCandidate:
    """Normalized option chain row used by the selector."""

    strike: float
    option_type: str  # CE / PE
    delta: float
    bid: float
    ask: float
    open_interest: int
    instrument_key: str = ""
    trading_symbol: str = ""

    @property
    def spread_pct(self) -> float:
        mid = (self.bid + self.ask) / 2.0
        if mid <= 0.0:
            return 1.0
        return max(0.0, (self.ask - self.bid) / mid)


@dataclass(frozen=True)
class StrikeSelectionResult:
    """Selected strike and scoring diagnostics."""

    selected: Optional[OptionCandidate]
    reason: str
    score: float
    evaluated: int


def _is_liquid(candidate: OptionCandidate, min_oi: int, max_spread_pct: float) -> bool:
    return candidate.open_interest >= min_oi and candidate.spread_pct <= max_spread_pct


def select_best_strike(
    *,
    underlying_price: float,
    option_chain_snapshot: Iterable[OptionCandidate],
    direction: str,
    signal_mode: str,
    min_oi_threshold: int = 5000,
    max_bid_ask_spread_pct: float = 0.02,
) -> StrikeSelectionResult:
    """
    Select best strike using directional + liquidity-aware rules.

    Rules:
    - breakout: ATM or slightly OTM
    - continuation: target abs(delta) in [0.25, 0.40]
    - apply OI and spread filters
    """
    normalized_dir = direction.strip().lower()
    option_type = "CE" if normalized_dir in ("long", "up", "bull", "buy") else "PE"
    mode = signal_mode.strip().lower()

    candidates = [
        c for c in option_chain_snapshot
        if c.option_type.upper() == option_type and _is_liquid(c, min_oi_threshold, max_bid_ask_spread_pct)
    ]
    if not candidates:
        return StrikeSelectionResult(
            selected=None,
            reason="No liquid candidate after OI/spread filters",
            score=0.0,
            evaluated=0,
        )

    def score(candidate: OptionCandidate) -> float:
        abs_delta = abs(candidate.delta)
        moneyness = (candidate.strike - underlying_price) / max(underlying_price, 1e-8)

        # Directional OTM penalty: calls prefer slight +moneyness, puts slight -moneyness.
        if option_type == "CE":
            otm_penalty = abs(moneyness - 0.003)
        else:
            otm_penalty = abs(moneyness + 0.003)

        if mode == "continuation":
            target_delta = 0.325
            delta_penalty = abs(abs_delta - target_delta)
        else:  # breakout
            target_delta = 0.50
            delta_penalty = abs(abs_delta - target_delta)

        liquidity_bonus = min(candidate.open_interest / float(max(min_oi_threshold, 1)), 3.0)
        spread_penalty = candidate.spread_pct / max(max_bid_ask_spread_pct, 1e-8)
        return 3.0 - (2.2 * delta_penalty + 0.8 * otm_penalty + 0.6 * spread_penalty) + 0.25 * liquidity_bonus

    ranked = sorted(((score(c), c) for c in candidates), key=lambda x: x[0], reverse=True)
    best_score, best = ranked[0]
    return StrikeSelectionResult(
        selected=best,
        reason=f"Selected {signal_mode} {option_type} strike with best liquidity-adjusted score",
        score=float(best_score),
        evaluated=len(candidates),
    )

