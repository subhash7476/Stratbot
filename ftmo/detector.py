"""Sweep detection, structure shift confirmation, displacement candle, and entry logic.

Fully mechanical — no ML, no discretion. This is the strategy's edge.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pandas as pd

from datetime import time as dtime

from ftmo.config import (
    SWEEP_ATR_MULT,
    DISPLACEMENT_BODY_MULT,
    SL_BUFFER_ATR_MULT,
    RR_RATIO,
    NY_END,
)


@dataclass(frozen=True)
class SweepEvent:
    timestamp: datetime
    direction: str         # HIGH_SWEEP | LOW_SWEEP
    sweep_price: float     # The extreme price of the sweep candle
    range_level: float     # The pre-NY high or low that was swept
    atr_threshold: float   # 0.25 × M15 ATR used for validation
    bar_idx: int           # Index in the NY session DataFrame


@dataclass(frozen=True)
class StructureShift:
    timestamp: datetime
    direction: str         # BEARISH | BULLISH
    displacement_bar_idx: int
    displacement_open: float
    displacement_close: float


@dataclass(frozen=True)
class TradeSetup:
    timestamp: datetime
    direction: str         # SHORT | LONG
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_points: float     # |entry - SL|
    sweep: SweepEvent
    shift: StructureShift


def detect_sweeps(
    df_ny: pd.DataFrame,
    pre_ny_high: float,
    pre_ny_low: float,
    m15_atr: float,
) -> list[SweepEvent]:
    """Detect liquidity sweeps of pre-NY range during NY session.

    Only the first sweep per direction counts.
    Sweep = price breaks range level AND extends ≥ 0.25 × M15 ATR.
    """
    threshold = SWEEP_ATR_MULT * m15_atr
    sweeps = []
    found_high = False
    found_low = False

    for i in range(len(df_ny)):
        bar = df_ny.iloc[i]

        if not found_high and bar["high"] > pre_ny_high:
            extension = bar["high"] - pre_ny_high
            if extension >= threshold:
                sweeps.append(SweepEvent(
                    timestamp=bar["timestamp"],
                    direction="HIGH_SWEEP",
                    sweep_price=bar["high"],
                    range_level=pre_ny_high,
                    atr_threshold=threshold,
                    bar_idx=i,
                ))
                found_high = True

        if not found_low and bar["low"] < pre_ny_low:
            extension = pre_ny_low - bar["low"]
            if extension >= threshold:
                sweeps.append(SweepEvent(
                    timestamp=bar["timestamp"],
                    direction="LOW_SWEEP",
                    sweep_price=bar["low"],
                    range_level=pre_ny_low,
                    atr_threshold=threshold,
                    bar_idx=i,
                ))
                found_low = True

        if found_high and found_low:
            break

    return sweeps


def detect_structure_shift(
    df_after_sweep: pd.DataFrame,
    sweep: SweepEvent,
    m5_atr_series: pd.Series,
    cutoff: dtime = None,
) -> Optional[StructureShift]:
    """Detect structure shift + displacement candle after a sweep.

    After HIGH_SWEEP: look for lower high + bearish displacement candle.
    After LOW_SWEEP: look for higher low + bullish displacement candle.
    """
    if len(df_after_sweep) < 3:
        return None

    _cutoff = cutoff if cutoff is not None else NY_END
    if sweep.direction == "HIGH_SWEEP":
        return _detect_bearish_shift(df_after_sweep, sweep.sweep_price, m5_atr_series, _cutoff)
    else:
        return _detect_bullish_shift(df_after_sweep, sweep.sweep_price, m5_atr_series, _cutoff)


def _detect_bearish_shift(
    df: pd.DataFrame,
    sweep_high: float,
    m5_atr_series: pd.Series,
    cutoff: dtime = None,
) -> Optional[StructureShift]:
    """After high sweep: look for lower high + bearish displacement candle."""
    _cutoff = cutoff if cutoff is not None else NY_END
    recent_high = sweep_high

    for i in range(1, len(df)):
        bar = df.iloc[i]

        # Check if time is past cutoff
        if bar["timestamp"].time() >= _cutoff:
            return None

        prev_bar = df.iloc[i - 1]

        # Track if we see a lower high (price failing to reach the sweep)
        if prev_bar["high"] < recent_high and bar["high"] < prev_bar["high"]:
            # Lower high structure formed — now check for displacement
            # Look at recent bars (current and up to 2 before) for bearish displacement
            search_start = max(0, i - 2)
            for j in range(search_start, i + 1):
                check = df.iloc[j]
                body = abs(check["close"] - check["open"])
                atr_val = m5_atr_series.iloc[j] if j < len(m5_atr_series) else m5_atr_series.iloc[-1]

                is_bearish = check["close"] < check["open"]
                if is_bearish and body >= DISPLACEMENT_BODY_MULT * atr_val:
                    return StructureShift(
                        timestamp=check["timestamp"],
                        direction="BEARISH",
                        displacement_bar_idx=j,
                        displacement_open=check["open"],
                        displacement_close=check["close"],
                    )

        # Update tracking — only raise recent_high if new bar exceeds it
        if bar["high"] > recent_high:
            recent_high = bar["high"]

    return None


def _detect_bullish_shift(
    df: pd.DataFrame,
    sweep_low: float,
    m5_atr_series: pd.Series,
    cutoff: dtime = None,
) -> Optional[StructureShift]:
    """After low sweep: look for higher low + bullish displacement candle."""
    _cutoff = cutoff if cutoff is not None else NY_END
    recent_low = sweep_low

    for i in range(1, len(df)):
        bar = df.iloc[i]

        if bar["timestamp"].time() >= _cutoff:
            return None

        prev_bar = df.iloc[i - 1]

        # Higher low: price fails to reach the sweep low
        if prev_bar["low"] > recent_low and bar["low"] > prev_bar["low"]:
            search_start = max(0, i - 2)
            for j in range(search_start, i + 1):
                check = df.iloc[j]
                body = abs(check["close"] - check["open"])
                atr_val = m5_atr_series.iloc[j] if j < len(m5_atr_series) else m5_atr_series.iloc[-1]

                is_bullish = check["close"] > check["open"]
                if is_bullish and body >= DISPLACEMENT_BODY_MULT * atr_val:
                    return StructureShift(
                        timestamp=check["timestamp"],
                        direction="BULLISH",
                        displacement_bar_idx=j,
                        displacement_open=check["open"],
                        displacement_close=check["close"],
                    )

        if bar["low"] < recent_low:
            recent_low = bar["low"]

    return None


def compute_trade_setup(
    sweep: SweepEvent,
    shift: StructureShift,
    df_after_shift: pd.DataFrame,
    m15_atr: float,
    cutoff: dtime = None,
) -> Optional[TradeSetup]:
    """Find pullback entry into displacement zone. Compute SL and TP."""
    if len(df_after_shift) == 0:
        return None

    _cutoff = cutoff if cutoff is not None else NY_END

    # Displacement zone
    zone_top = max(shift.displacement_open, shift.displacement_close)
    zone_bottom = min(shift.displacement_open, shift.displacement_close)

    if shift.direction == "BEARISH":
        entry_price = zone_bottom
        stop_loss = sweep.sweep_price + SL_BUFFER_ATR_MULT * m15_atr
        risk = stop_loss - entry_price
        if risk <= 0:
            return None
        take_profit = entry_price - (RR_RATIO * risk)

        for i in range(len(df_after_shift)):
            bar = df_after_shift.iloc[i]
            if bar["timestamp"].time() >= _cutoff:
                return None
            if bar["high"] >= zone_bottom:
                return TradeSetup(
                    timestamp=bar["timestamp"],
                    direction="SHORT",
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    risk_points=risk,
                    sweep=sweep,
                    shift=shift,
                )

    else:
        entry_price = zone_top
        stop_loss = sweep.sweep_price - SL_BUFFER_ATR_MULT * m15_atr
        risk = entry_price - stop_loss
        if risk <= 0:
            return None
        take_profit = entry_price + (RR_RATIO * risk)

        for i in range(len(df_after_shift)):
            bar = df_after_shift.iloc[i]
            if bar["timestamp"].time() >= _cutoff:
                return None
            if bar["low"] <= zone_top:
                return TradeSetup(
                    timestamp=bar["timestamp"],
                    direction="LONG",
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    risk_points=risk,
                    sweep=sweep,
                    shift=shift,
                )

    return None


def scan_session(
    df_ny: pd.DataFrame,
    pre_ny_high: float,
    pre_ny_low: float,
    m15_atr: float,
    cutoff: dtime = None,
) -> list[TradeSetup]:
    """Top-level: find all valid trade setups in a single NY session.

    Returns 0, 1, or 2 setups (one per sweep direction max).
    """
    if len(df_ny) < 3 or m15_atr <= 0:
        return []

    sweeps = detect_sweeps(df_ny, pre_ny_high, pre_ny_low, m15_atr)
    setups = []

    for sweep in sweeps:
        # Bars after the sweep
        after_sweep = df_ny.iloc[sweep.bar_idx + 1:].reset_index(drop=True)
        if len(after_sweep) < 2:
            continue

        # M5 ATR series aligned to after_sweep
        m5_atr = after_sweep["atr"] if "atr" in after_sweep.columns else pd.Series([m15_atr] * len(after_sweep))

        shift = detect_structure_shift(after_sweep, sweep, m5_atr, cutoff)
        if shift is None:
            continue

        # Bars after the displacement candle
        after_disp = after_sweep.iloc[shift.displacement_bar_idx + 1:].reset_index(drop=True)
        if len(after_disp) == 0:
            continue

        setup = compute_trade_setup(sweep, shift, after_disp, m15_atr, cutoff)
        if setup is not None:
            setups.append(setup)

    return setups
