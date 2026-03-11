"""USDINR filter attribution utilities for backtest comparison."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable
import numpy as np


@dataclass(frozen=True)
class StrategyPerformance:
    total_trades: int
    win_rate: float
    expectancy: float
    max_drawdown: float
    total_pnl: float


@dataclass(frozen=True)
class USDINRAttributionReport:
    strategy_without_usdinr_filter: StrategyPerformance
    strategy_with_usdinr_filter: StrategyPerformance
    win_rate_difference: float
    expectancy_difference: float
    drawdown_difference: float

    def to_dict(self) -> dict:
        return asdict(self)


def _max_drawdown_from_pnl(pnls: np.ndarray) -> float:
    if pnls.size == 0:
        return 0.0
    curve = np.cumsum(pnls)
    peaks = np.maximum.accumulate(curve)
    return float(np.max(peaks - curve))


def summarize_trade_pnls(trade_pnls: Iterable[float]) -> StrategyPerformance:
    """Compute common strategy metrics from per-trade pnl sequence."""
    pnls = np.asarray(list(trade_pnls), dtype=float)
    if pnls.size == 0:
        return StrategyPerformance(
            total_trades=0,
            win_rate=0.0,
            expectancy=0.0,
            max_drawdown=0.0,
            total_pnl=0.0,
        )

    wins = np.sum(pnls > 0.0)
    return StrategyPerformance(
        total_trades=int(pnls.size),
        win_rate=float(wins / pnls.size * 100.0),
        expectancy=float(np.mean(pnls)),
        max_drawdown=_max_drawdown_from_pnl(pnls),
        total_pnl=float(np.sum(pnls)),
    )


def build_usdinr_attribution_report(
    *,
    pnls_without_usdinr_filter: Iterable[float],
    pnls_with_usdinr_filter: Iterable[float],
) -> USDINRAttributionReport:
    """Build filter attribution report from two strategy pnl streams."""
    perf_without = summarize_trade_pnls(pnls_without_usdinr_filter)
    perf_with = summarize_trade_pnls(pnls_with_usdinr_filter)
    return USDINRAttributionReport(
        strategy_without_usdinr_filter=perf_without,
        strategy_with_usdinr_filter=perf_with,
        win_rate_difference=perf_with.win_rate - perf_without.win_rate,
        expectancy_difference=perf_with.expectancy - perf_without.expectancy,
        drawdown_difference=perf_with.max_drawdown - perf_without.max_drawdown,
    )

