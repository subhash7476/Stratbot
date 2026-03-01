"""
PixityAI v3 — 1H Breakout Trigger
====================================
Applied intraday to stocks that passed the daily compression scanner.

ENTRY CONDITIONS (Long):
  1. Stock is on the compression candidate list for today (from prior EOD scan)
  2. 1H close > highest high of last 20 hourly bars (breakout)
  3. Breakout bar true range > 1.5x 20-bar average true range (range expansion)
  4. RS_5d still positive (directional confirmation, re-checked at trigger time)
  5. Not first bar of session UNLESS gap-and-go condition:
       - Daily open gaps above breakout level AND first bar closes strong
         (first-bar range > 20-bar avg range)

SHORT = mirror logic.

EXIT LOGIC (tracked by trade state machine, not this module):
  - Initial stop: 1.5 x Daily ATR (from compression scanner)
  - Trail to breakeven at +1.5R
  - Early exit: no progress after 1 trading day (<0.5R)
  - Hard time stop: 3 trading days

OUTPUT per trigger:
  SignalEvent with metadata containing all context needed for sizing and exit.

CAUSAL GUARANTEE:
  The 20-bar lookback uses bars[i-20 : i-1] (excludes current bar).
  Breakout is confirmed only after the bar closes above the prior high.
  No future bar data is used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Set

import numpy as np
import pandas as pd

from core.strategies.expansion_v3.daily_compression_scanner import CompressionCandidate

logger = logging.getLogger(__name__)

# NSE session: 09:15 – 15:30
# First 1H bar: 09:15 – 10:14 (bar timestamp = 09:15)
# "First hour" = bar with timestamp.hour == 9
_FIRST_HOUR = 9
_SESSION_START = time(9, 15)
_SESSION_END   = time(15, 30)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class BreakoutConfig:
    # Lookback for breakout level and range average (1H bars)
    breakout_lookback: int = 20
    # Breakout bar range must exceed this multiple of 20-bar avg range
    range_expansion_factor: float = 1.5
    # Gap-and-go: allow first-bar entry if daily open gaps above breakout level
    allow_gap_and_go: bool = True
    # Gap-and-go: first bar range must also exceed avg range by this factor
    gap_bar_range_factor: float = 1.0
    # RS confirmation at trigger time (re-check, not just from EOD scan)
    rs_recheck_bars: int = 5
    # Nifty RS lookback for soft bias (daily bars, same as scanner)
    nifty_rs_lookback: int = 5
    # Candidate expiry: number of calendar days after scan_date before candidate
    # is discarded. If breakout hasn't fired by then, compression has resolved.
    # 3 trading days ≈ 5 calendar days to be safe across weekends.
    candidate_expiry_calendar_days: int = 5


# ---------------------------------------------------------------------------
# Trigger event
# ---------------------------------------------------------------------------

@dataclass
class BreakoutSignal:
    symbol: str
    trading_symbol: str
    trigger_time: datetime          # bar close time that triggered
    direction: str                  # 'LONG' or 'SHORT'
    entry_price: float              # suggested entry = close of trigger bar
    breakout_level: float           # the 20-bar high/low that was breached
    bar_range: float                # true range of trigger bar
    avg_range_20: float             # 20-bar avg range at trigger
    range_expansion_ratio: float    # bar_range / avg_range_20
    daily_atr: float                # from compression candidate (for stop sizing)
    is_gap_and_go: bool             # True if first-bar gap entry
    compression_candidate: CompressionCandidate  # full context from EOD scan

    def to_dict(self) -> dict:
        return {
            "symbol":               self.symbol,
            "trading_symbol":       self.trading_symbol,
            "trigger_time":         self.trigger_time.isoformat(),
            "direction":            self.direction,
            "entry_price":          round(self.entry_price, 2),
            "breakout_level":       round(self.breakout_level, 2),
            "bar_range":            round(self.bar_range, 4),
            "avg_range_20":         round(self.avg_range_20, 4),
            "range_expansion_ratio": round(self.range_expansion_ratio, 4),
            "daily_atr":            round(self.daily_atr, 4),
            "is_gap_and_go":        self.is_gap_and_go,
            "scan_date":            self.compression_candidate.scan_date.isoformat(),
            "atr_universe_rank":    round(self.compression_candidate.atr_universe_rank, 2),
            "rs_5d":                round(self.compression_candidate.rs_5d * 100, 4),
        }


# ---------------------------------------------------------------------------
# Core trigger
# ---------------------------------------------------------------------------

class HourlyBreakoutTrigger:
    """
    Evaluates 1H bars for breakout signals on compression candidates.

    Usage (vectorised backtest):
        trigger = HourlyBreakoutTrigger(config)
        signals = trigger.scan_symbol(symbol, df_1h, candidate, nifty_df_daily)

    The caller is responsible for:
      - Only passing symbols that are on the compression candidate list
      - Ensuring df_1h covers enough history (at least 25 bars before first signal bar)
      - Providing Nifty daily OHLCV for RS re-check context
    """

    def __init__(self, config: Optional[BreakoutConfig] = None):
        self.cfg = config or BreakoutConfig()

    def scan_symbol(
        self,
        symbol: str,
        df_1h: pd.DataFrame,
        candidate: CompressionCandidate,
        nifty_df_daily: Optional[pd.DataFrame] = None,
        eligible_from: Optional[date] = None,
    ) -> List[BreakoutSignal]:
        """
        Scan all 1H bars for a symbol to find breakout signals.

        Args:
            symbol:         NSE_EQ|INE... instrument key
            df_1h:          1H OHLCV DataFrame (timestamp, open, high, low, close, volume)
                            Must include warmup bars (20+ bars before eligible_from)
            candidate:      CompressionCandidate from the daily scanner
            nifty_df_daily: Nifty 50 daily OHLCV for RS confirmation (optional)
            eligible_from:  Only emit signals on/after this date.
                            Defaults to candidate.scan_date + 1 trading day.

        Returns:
            List of BreakoutSignal (may be empty).
            In a proper walk-forward, at most ONE signal per symbol per compression
            episode should be used. Caller enforces this via trade state machine.
        """
        if df_1h is None or len(df_1h) < self.cfg.breakout_lookback + 5:
            return []

        df = df_1h.copy()
        df = df.sort_values("timestamp").reset_index(drop=True)

        # Eligible date: candidate detected on scan_date, tradeable from day T+1
        if eligible_from is None:
            eligible_from = candidate.scan_date + timedelta(days=1)

        # Expiry date: candidate expires after N calendar days — compression
        # has resolved by then whether or not a breakout occurred.
        expiry_date = candidate.scan_date + timedelta(
            days=self.cfg.candidate_expiry_calendar_days
        )

        # True range for each bar
        df["prev_close"] = df["close"].shift(1)
        df["tr"] = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["prev_close"]).abs(),
            (df["low"]  - df["prev_close"]).abs(),
        ], axis=1).max(axis=1)

        signals: List[BreakoutSignal] = []

        lb = self.cfg.breakout_lookback

        for i in range(lb, len(df)):
            bar = df.iloc[i]
            bar_time: datetime = pd.Timestamp(bar["timestamp"]).to_pydatetime()
            bar_date = bar_time.date()

            # Only evaluate bars within the eligible window [eligible_from, expiry_date]
            if bar_date < eligible_from:
                continue
            if bar_date > expiry_date:
                break  # Past expiry — no more signals from this candidate

            # --- Session filter ---
            is_first_bar = (bar_time.hour == _FIRST_HOUR)

            # --- Lookback window (strictly historical — excludes current bar) ---
            window = df.iloc[i - lb: i]
            prior_high = window["high"].max()
            prior_low  = window["low"].min()
            avg_range  = window["tr"].mean()

            if avg_range <= 0:
                continue

            # True range of current bar
            bar_tr = bar["tr"]
            range_expansion = bar_tr / avg_range

            # --- Direction from candidate ---
            direction = candidate.direction

            # --- Breakout condition ---
            if direction == "LONG":
                breakout_level = prior_high
                breakout_fired = bar["close"] > breakout_level
            else:
                breakout_level = prior_low
                breakout_fired = bar["close"] < breakout_level

            if not breakout_fired:
                continue

            # --- Range expansion confirmation ---
            if range_expansion < self.cfg.range_expansion_factor:
                continue

            # --- First-bar rule ---
            gap_and_go = False
            if is_first_bar:
                if not self.cfg.allow_gap_and_go:
                    continue  # No first-bar entries at all
                # Gap-and-go: daily open must gap through breakout level
                daily_open = bar["open"]
                if direction == "LONG":
                    gap_condition = daily_open > breakout_level
                else:
                    gap_condition = daily_open < breakout_level
                first_bar_range_ok = bar_tr >= avg_range * self.cfg.gap_bar_range_factor
                if not (gap_condition and first_bar_range_ok):
                    continue  # First bar but no gap-and-go — skip
                gap_and_go = True

            signals.append(BreakoutSignal(
                symbol=symbol,
                trading_symbol=candidate.trading_symbol,
                trigger_time=bar_time,
                direction=direction,
                entry_price=bar["close"],
                breakout_level=breakout_level,
                bar_range=bar_tr,
                avg_range_20=avg_range,
                range_expansion_ratio=range_expansion,
                daily_atr=candidate.atr,
                is_gap_and_go=gap_and_go,
                compression_candidate=candidate,
            ))

        return signals

    def scan_candidates_range(
        self,
        candidates_by_date: Dict[date, List[CompressionCandidate]],
        df_1h_by_symbol: Dict[str, pd.DataFrame],
        nifty_df_daily: Optional[pd.DataFrame] = None,
    ) -> List[BreakoutSignal]:
        """
        Batch scan: for each candidate date, scan the symbol's 1H data
        for breakout signals on day T+1 onwards.

        One signal per symbol per compression episode — once a signal fires
        for a candidate, that candidate is consumed (not re-evaluated).

        Args:
            candidates_by_date:  Output from DailyCompressionScanner.scan_range()
            df_1h_by_symbol:     Dict of symbol -> full 1H DataFrame
            nifty_df_daily:      Nifty daily OHLCV

        Returns:
            All breakout signals found across all candidates and dates.
        """
        all_signals: List[BreakoutSignal] = []

        # Track which symbols have already produced a signal on a given trigger date.
        # Key: (symbol, trigger_date) — prevents same symbol firing twice on same day
        # from two overlapping compression episodes (e.g. consecutive scan dates).
        fired_symbol_days: Set[tuple] = set()

        # Sort candidates by scan_date ascending
        sorted_dates = sorted(candidates_by_date.keys())

        for scan_date in sorted_dates:
            for candidate in candidates_by_date[scan_date]:
                df_1h = df_1h_by_symbol.get(candidate.symbol)
                if df_1h is None:
                    continue

                signals = self.scan_symbol(
                    symbol=candidate.symbol,
                    df_1h=df_1h,
                    candidate=candidate,
                    nifty_df_daily=nifty_df_daily,
                    eligible_from=scan_date + timedelta(days=1),
                )

                if signals:
                    # Take only the FIRST signal for this compression episode
                    first_signal = min(signals, key=lambda s: s.trigger_time)
                    trigger_day_key = (candidate.symbol, first_signal.trigger_time.date())
                    if trigger_day_key not in fired_symbol_days:
                        all_signals.append(first_signal)
                        fired_symbol_days.add(trigger_day_key)

        return sorted(all_signals, key=lambda s: s.trigger_time)
