"""Structured metrics payloads for dashboard consumption."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable
import numpy as np


@dataclass(frozen=True)
class OptionSpreadStats:
    mean_spread_pct: float
    p90_spread_pct: float
    max_spread_pct: float


@dataclass(frozen=True)
class StrategyMetricsSnapshot:
    iv_rank: float
    realized_volatility: float
    option_spread_stats: OptionSpreadStats
    liquidity_rejection_rate: float
    slippage_estimates_bps: float

    def to_dict(self) -> dict:
        return asdict(self)


def compute_iv_rank(current_iv: float, iv_history: Iterable[float]) -> float:
    """IV rank in [0, 1] against supplied lookback history."""
    hist = np.asarray(list(iv_history), dtype=float)
    hist = hist[np.isfinite(hist)]
    if hist.size == 0:
        return 0.5
    iv_min = float(np.min(hist))
    iv_max = float(np.max(hist))
    if iv_max <= iv_min:
        return 0.5
    return float(np.clip((current_iv - iv_min) / (iv_max - iv_min), 0.0, 1.0))


def build_strategy_metrics_snapshot(
    *,
    current_iv: float,
    iv_history: Iterable[float],
    realized_volatility: float,
    spread_pcts: Iterable[float],
    liquidity_rejections: int,
    liquidity_checks: int,
    slippage_bps_samples: Iterable[float],
) -> StrategyMetricsSnapshot:
    """Build dashboard-ready metrics snapshot."""
    spread_arr = np.asarray(list(spread_pcts), dtype=float)
    spread_arr = spread_arr[np.isfinite(spread_arr)]
    if spread_arr.size == 0:
        spread_arr = np.array([0.0], dtype=float)

    slippage_arr = np.asarray(list(slippage_bps_samples), dtype=float)
    slippage_arr = slippage_arr[np.isfinite(slippage_arr)]
    slippage_est = float(np.mean(slippage_arr)) if slippage_arr.size else 0.0

    rejection_rate = 0.0
    if liquidity_checks > 0:
        rejection_rate = float(np.clip(liquidity_rejections / liquidity_checks, 0.0, 1.0))

    return StrategyMetricsSnapshot(
        iv_rank=compute_iv_rank(current_iv, iv_history),
        realized_volatility=float(realized_volatility),
        option_spread_stats=OptionSpreadStats(
            mean_spread_pct=float(np.mean(spread_arr)),
            p90_spread_pct=float(np.percentile(spread_arr, 90)),
            max_spread_pct=float(np.max(spread_arr)),
        ),
        liquidity_rejection_rate=rejection_rate,
        slippage_estimates_bps=slippage_est,
    )

