"""
PixityAI v4 — Daily Breakout Trigger
======================================
Evaluates compression candidates at end of Day T.
Signal fires if:
    Close[T] > Highest High of last 20 completed daily bars  (LONG)
    Close[T] < Lowest Low  of last 20 completed daily bars  (SHORT)

Entry is at Day T+1 open — strictly causal, no intraday logic.

Key rules:
  - "Last 20 completed daily bars" = bars [T-20 .. T-1], NOT including bar T itself.
    This is the 20-bar prior high used as resistance/support.
  - Candidate must still be active (within expiry window from scan_date).
  - One signal per symbol per compression episode (first trigger wins).
  - Deduplication: one entry per symbol per day across overlapping episodes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from .daily_compression_scanner import CompressionCandidate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class DailyTriggerConfig:
    # Lookback for prior high/low breakout level (completed bars before T)
    breakout_lookback: int = 20
    # Candidate expiry: calendar days after scan_date
    candidate_expiry_days: int = 7   # ~5 trading days
    # Re-entry guard: minimum trading days gap before same symbol re-enters
    reentry_gap_days: int = 10       # matches time stop


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass
class DailySignal:
    symbol: str
    trading_symbol: str
    direction: str                   # LONG / SHORT
    scan_date: date                  # compression detected on this date
    trigger_date: date               # breakout confirmed on this date (Day T)
    entry_date: date                 # enter at open of this date (Day T+1)
    breakout_level: float            # prior 20-bar high/low
    entry_price: float               # T+1 open (filled at simulation time)
    daily_atr: float                 # ATR(20) from Day T — used for stop/target
    close_t: float                   # close of Day T (confirmation close)
    rs_5d: float


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------

class DailyBreakoutTrigger:
    """
    Scans compression candidates against daily OHLCV to find EOD breakout signals.

    Usage:
        trigger = DailyBreakoutTrigger(config)
        signals = trigger.scan_range(candidates_by_date, df_daily_by_symbol, trading_days)
    """

    def __init__(self, config: Optional[DailyTriggerConfig] = None):
        self.cfg = config or DailyTriggerConfig()

    def scan_range(
        self,
        candidates_by_date: Dict[date, List[CompressionCandidate]],
        df_daily_by_symbol: Dict[str, pd.DataFrame],
        trading_days: List[date],
    ) -> List[DailySignal]:
        """
        Iterate trading days in order. For each day T, check all active candidates
        (scan_date <= T < expiry, and symbol not already in a position).
        Returns list of DailySignal sorted by entry_date.
        """
        # Build active candidate pool: {symbol -> [candidate, ...]}
        # A compression episode is identified by (symbol, scan_date).
        # Multiple scan_dates can produce candidates for the same symbol.

        signals: List[DailySignal] = []
        fired_episodes: Set[Tuple[str, date]] = set()   # (symbol, scan_date) already triggered
        fired_symbol_days: Set[Tuple[str, date]] = set() # (symbol, trigger_date) dedup

        # Index all candidates by scan_date for quick lookup
        all_candidates: List[CompressionCandidate] = [
            c for clist in candidates_by_date.values() for c in clist
        ]

        for i, trigger_date in enumerate(trading_days):
            # Collect candidates active on trigger_date
            active = [
                c for c in all_candidates
                if c.scan_date < trigger_date  # strictly causal: scan_date < trigger_date
                and trigger_date <= c.scan_date + timedelta(days=self.cfg.candidate_expiry_days)
                and (c.symbol, c.scan_date) not in fired_episodes
            ]

            if not active:
                continue

            # T+1 must exist (we need an entry date)
            if i + 1 >= len(trading_days):
                continue
            entry_date = trading_days[i + 1]

            for c in active:
                sym = c.symbol
                df = df_daily_by_symbol.get(sym)
                if df is None or df.empty:
                    continue

                # Get daily data up to and including trigger_date
                df_t = df[df["date"] <= trigger_date]
                if len(df_t) < self.cfg.breakout_lookback + 2:
                    continue

                close_t = float(df_t["close"].iloc[-1])
                bar_t_date = df_t["date"].iloc[-1]

                # Confirm the last row IS trigger_date (market was open)
                if bar_t_date != trigger_date:
                    continue

                # Prior 20 completed bars = rows [-(lookback+1) .. -1] (exclude bar T itself)
                prior = df_t.iloc[-(self.cfg.breakout_lookback + 1):-1]
                if len(prior) < self.cfg.breakout_lookback:
                    continue

                prior_high = float(prior["high"].max())
                prior_low  = float(prior["low"].min())

                # ATR from Day T
                daily_atr = _get_atr(df_t, period=20)
                if daily_atr is None or daily_atr <= 0:
                    continue

                # --- Breakout check ---
                triggered = False
                if c.direction == "LONG" and close_t > prior_high:
                    triggered = True
                    breakout_level = prior_high
                elif c.direction == "SHORT" and close_t < prior_low:
                    triggered = True
                    breakout_level = prior_low

                if not triggered:
                    continue

                # Deduplication: one signal per (symbol, trigger_date)
                key_day = (sym, trigger_date)
                if key_day in fired_symbol_days:
                    continue
                fired_symbol_days.add(key_day)
                fired_episodes.add((sym, c.scan_date))

                signals.append(DailySignal(
                    symbol          = sym,
                    trading_symbol  = c.trading_symbol,
                    direction       = c.direction,
                    scan_date       = c.scan_date,
                    trigger_date    = trigger_date,
                    entry_date      = entry_date,
                    breakout_level  = breakout_level,
                    entry_price     = 0.0,          # filled at simulation time (T+1 open)
                    daily_atr       = daily_atr,
                    close_t         = close_t,
                    rs_5d           = c.rs_5d,
                ))

        signals.sort(key=lambda s: s.entry_date)
        logger.info(f"Daily trigger: {len(signals)} signals from "
                    f"{len(all_candidates)} active candidate-days")
        return signals


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_atr(df: pd.DataFrame, period: int = 20) -> Optional[float]:
    """Wilder ATR, returns scalar for the last row."""
    if len(df) < period + 1:
        return None
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"]  - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    val = float(atr.iloc[-1])
    return val if val > 0 else None
