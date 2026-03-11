"""Liquidity and spread guardrails for option contract selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class LiquidityCheckInput:
    bid: float
    ask: float
    open_interest: int
    last_trade_time: datetime
    now: datetime
    premium: float


@dataclass(frozen=True)
class LiquidityCheckResult:
    trade_allowed: bool
    spread_pct: float
    reasons: tuple[str, ...]


def evaluate_liquidity(
    payload: LiquidityCheckInput,
    *,
    max_spread_pct: float = 0.02,
    min_open_interest: int = 5000,
    max_last_trade_gap_seconds: int = 120,
    min_tradable_premium: float = 5.0,
) -> LiquidityCheckResult:
    """Reject contracts failing spread, OI, freshness, or premium thresholds."""
    reasons: list[str] = []

    mid = (payload.bid + payload.ask) / 2.0
    spread_pct = 1.0 if mid <= 0.0 else max(0.0, (payload.ask - payload.bid) / mid)
    if spread_pct > max_spread_pct:
        reasons.append(f"spread_pct>{max_spread_pct:.4f}")

    if payload.open_interest < min_open_interest:
        reasons.append(f"open_interest<{min_open_interest}")

    gap = max(0, int((payload.now - payload.last_trade_time).total_seconds()))
    if gap > max_last_trade_gap_seconds:
        reasons.append(f"last_trade_gap>{max_last_trade_gap_seconds}s")

    if payload.premium < min_tradable_premium:
        reasons.append(f"premium<{min_tradable_premium}")

    return LiquidityCheckResult(
        trade_allowed=len(reasons) == 0,
        spread_pct=spread_pct,
        reasons=tuple(reasons),
    )

