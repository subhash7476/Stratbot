"""Volatility regime classifier for options-aware signal gating."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class VolatilityRegime(str, Enum):
    LOW_VOL = "LOW_VOL"
    NORMAL_VOL = "NORMAL_VOL"
    HIGH_VOL = "HIGH_VOL"
    VOL_EXPANSION = "VOL_EXPANSION"


@dataclass(frozen=True)
class VolatilityRegimeSnapshot:
    """Single-point regime output consumed by signal engines."""

    regime: VolatilityRegime
    iv_hv_ratio: float
    short_long_rv_ratio: float
    atr_norm: float
    confidence: float


def classify_volatility_regime(
    realized_volatility_20d: float,
    realized_volatility_5d: float,
    option_implied_volatility: float,
    atr: float,
    *,
    atr_baseline: Optional[float] = None,
    high_vol_iv_multiple: float = 1.4,
    low_vol_iv_multiple: float = 0.7,
    expansion_ratio_threshold: float = 1.2,
) -> VolatilityRegimeSnapshot:
    """
    Classify volatility regime using realized vol, implied vol, and ATR.

    Rule precedence:
    1) VOL_EXPANSION when short-term RV is rising quickly versus long-term RV.
    2) HIGH_VOL when IV >> long-term realized volatility.
    3) LOW_VOL when IV << long-term realized volatility.
    4) NORMAL_VOL fallback.
    """
    hv20 = max(realized_volatility_20d, 1e-8)
    hv5 = max(realized_volatility_5d, 0.0)
    iv = max(option_implied_volatility, 0.0)
    atr_val = max(atr, 0.0)
    atr_base = max(atr_baseline if atr_baseline is not None else hv20, 1e-8)

    iv_hv_ratio = iv / hv20
    short_long_rv_ratio = hv5 / hv20
    atr_norm = atr_val / atr_base

    if short_long_rv_ratio >= expansion_ratio_threshold and iv_hv_ratio >= 1.0:
        regime = VolatilityRegime.VOL_EXPANSION
        confidence = min(1.0, (short_long_rv_ratio - expansion_ratio_threshold) / 0.5 + 0.6)
    elif iv_hv_ratio >= high_vol_iv_multiple:
        regime = VolatilityRegime.HIGH_VOL
        confidence = min(1.0, (iv_hv_ratio - high_vol_iv_multiple) / 0.7 + 0.55)
    elif iv_hv_ratio <= low_vol_iv_multiple:
        regime = VolatilityRegime.LOW_VOL
        confidence = min(1.0, (low_vol_iv_multiple - iv_hv_ratio) / 0.4 + 0.55)
    else:
        regime = VolatilityRegime.NORMAL_VOL
        distance = min(abs(iv_hv_ratio - 1.0), 0.5)
        confidence = max(0.4, 0.8 - distance)

    return VolatilityRegimeSnapshot(
        regime=regime,
        iv_hv_ratio=iv_hv_ratio,
        short_long_rv_ratio=short_long_rv_ratio,
        atr_norm=atr_norm,
        confidence=confidence,
    )

