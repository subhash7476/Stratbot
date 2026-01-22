# core/signal_to_options.py
"""
Signal to Options Adapter
==========================
Convert strategy signals (Squeeze, EHMA, VCB) to UnderlyingSignal format for options trading.

This module bridges the gap between:
- Strategy-specific signal formats (SqueezeSignal, etc.)
- Universal UnderlyingSignal format used by OptionSelector

Author: Trading Bot Pro
Version: 1.0
Date: 2026-01-17
"""

from typing import Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass
import pandas as pd

from core.option_selector import UnderlyingSignal
from core.strategies.indian_market_squeeze import SqueezeSignal


class SqueezeToOptionAdapter:
    """
    Convert Squeeze signals to UnderlyingSignal format for options trading

    Usage:
        >>> adapter = SqueezeToOptionAdapter()
        >>> underlying_signal = adapter.convert(squeeze_signal, symbol, instrument_key)
        >>> # Now use with OptionSelector or OptionRecommender
    """

    @staticmethod
    def convert(
        squeeze_signal: SqueezeSignal,
        symbol: str,
        instrument_key: str
    ) -> UnderlyingSignal:
        """
        Convert SqueezeSignal to UnderlyingSignal

        Args:
            squeeze_signal: SqueezeSignal from Indian Market Squeeze strategy
            symbol: Trading symbol (e.g., "RELIANCE")
            instrument_key: Upstox instrument key (e.g., "NSE_EQ|INE002A01018")

        Returns:
            UnderlyingSignal ready for options analysis

        Mapping:
            - signal_type (LONG/SHORT) → side
            - entry_price → entry
            - sl_price → stop
            - tp_price → target
            - score (4 or 5) → strength (80 or 100)
            - reasons → reason dict

        Example:
            >>> sq_sig = SqueezeSignal(...)  # From scanner
            >>> adapter = SqueezeToOptionAdapter()
            >>> und_sig = adapter.convert(sq_sig, "RELIANCE", "NSE_EQ|INE002A01018")
            >>> print(f"Signal: {und_sig.side} @ {und_sig.entry}")
        """
        # Calculate strength (0-100 scale)
        # Score 5 = 100%, Score 4 = 80%
        strength = squeeze_signal.score * 20.0

        # Build reason dict with all signal metadata
        reason = {
            'score': squeeze_signal.score,
            'reasons': squeeze_signal.reasons,
            'status': squeeze_signal.status,
            'strategy': 'SQUEEZE_15M',
            'timestamp': squeeze_signal.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        }

        # Create UnderlyingSignal
        return UnderlyingSignal(
            instrument_key=instrument_key,
            symbol=symbol.upper(),
            side=squeeze_signal.signal_type.upper(),  # LONG or SHORT
            timeframe='15minute',
            entry=squeeze_signal.entry_price,
            stop=squeeze_signal.sl_price,
            target=squeeze_signal.tp_price,
            strength=strength,
            strategy='SQUEEZE_15M',
            timestamp=squeeze_signal.timestamp,
            reason=reason
        )

    @staticmethod
    def convert_from_dataframe_row(row: pd.Series) -> UnderlyingSignal:
        """
        Convert a row from tradable signals DataFrame to UnderlyingSignal

        This is useful for Tab 5 UI where signals are displayed in a DataFrame.

        Args:
            row: DataFrame row from sq_live_tradable session state

        Expected columns:
            - Symbol: Trading symbol
            - Signal: LONG/SHORT
            - Entry: Entry price
            - SL: Stop loss
            - TP: Target price
            - Score: Signal score (4 or 5)
            - Time: Signal time (HH:MM format)
            - Instrument Key: Upstox instrument key
            - Reasons: Comma-separated reasons

        Returns:
            UnderlyingSignal for options analysis

        Example:
            >>> tradable_df = st.session_state.get("sq_live_tradable")
            >>> for idx, row in tradable_df.iterrows():
            ...     signal = SqueezeToOptionAdapter.convert_from_dataframe_row(row)
            ...     recommendations = recommender.recommend_for_signal(signal)
        """
        # Parse timestamp (combine with today's date)
        time_str = row.get('Time', '00:00')
        today = datetime.now().date()
        timestamp = datetime.combine(
            today,
            datetime.strptime(time_str, '%H:%M').time()
        )

        # Parse reasons
        reasons_str = row.get('Reasons', '')
        reasons = [r.strip() for r in reasons_str.split(',')] if reasons_str else []

        # Calculate strength
        score = row.get('Score', 5)
        strength = score * 20.0

        # Build reason dict
        reason = {
            'score': score,
            'reasons': reasons,
            'strategy': 'SQUEEZE_15M',
            'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S')
        }

        return UnderlyingSignal(
            instrument_key=row.get('Instrument Key', ''),
            symbol=row.get('Symbol', '').upper(),
            side=row.get('Signal', 'LONG').upper(),
            timeframe='15minute',
            entry=float(row.get('Entry', 0)),
            stop=float(row.get('SL', 0)),
            target=float(row.get('TP', 0)),
            strength=strength,
            strategy='SQUEEZE_15M',
            timestamp=timestamp,
            reason=reason
        )

    @staticmethod
    def create_summary(signal: UnderlyingSignal) -> str:
        """
        Create human-readable summary of signal

        Args:
            signal: UnderlyingSignal

        Returns:
            Formatted string summary

        Example:
            >>> summary = adapter.create_summary(signal)
            >>> print(summary)
            RELIANCE LONG @ ₹2,450 | SL: ₹2,430 | TP: ₹2,490
            Score: 5/5 (100% strength)
            Risk: ₹20 (0.8%) | Reward: ₹40 (1.6%) | R:R = 1:2.0
        """
        risk_amt = abs(signal.entry - signal.stop)
        risk_pct = (risk_amt / signal.entry) * 100
        reward_amt = abs(signal.target - signal.entry)
        reward_pct = (reward_amt / signal.entry) * 100
        rr_ratio = reward_amt / risk_amt if risk_amt > 0 else 0

        score = signal.reason.get('score', 5)

        summary = f"{signal.symbol} {signal.side} @ ₹{signal.entry:,.0f}\n"
        summary += f"SL: ₹{signal.stop:,.0f} | TP: ₹{signal.target:,.0f}\n"
        summary += f"Score: {score}/5 ({signal.strength:.0f}% strength)\n"
        summary += f"Risk: ₹{risk_amt:.0f} ({risk_pct:.1f}%) | "
        summary += f"Reward: ₹{reward_amt:.0f} ({reward_pct:.1f}%) | "
        summary += f"R:R = 1:{rr_ratio:.1f}"

        return summary


# Future: Add adapters for other strategies

class EHMAToOptionAdapter:
    """Convert EHMA signals to UnderlyingSignal (future implementation)"""
    pass


class VCBToOptionAdapter:
    """Convert VCB signals to UnderlyingSignal (future implementation)"""
    pass


# Convenience function

def convert_squeeze_signal(
    squeeze_signal: SqueezeSignal,
    symbol: str,
    instrument_key: str
) -> UnderlyingSignal:
    """
    Quick conversion function for squeeze signals

    Example:
        >>> from core.signal_to_options import convert_squeeze_signal
        >>> underlying = convert_squeeze_signal(sq_sig, "RELIANCE", "NSE_EQ|...")
    """
    adapter = SqueezeToOptionAdapter()
    return adapter.convert(squeeze_signal, symbol, instrument_key)


if __name__ == "__main__":
    # Test adapter
    print("=== Signal to Options Adapter Test ===\n")

    # Create a mock SqueezeSignal
    from datetime import datetime

    mock_signal = SqueezeSignal(
        timestamp=datetime.now(),
        signal_type='LONG',
        entry_price=2450.0,
        sl_price=2430.0,
        tp_price=2490.0,
        score=5.0,
        reasons=['SuperTrend bullish', 'WaveTrend cross up', 'Recent squeeze'],
        status='ACTIVE'
    )

    # Convert to UnderlyingSignal
    adapter = SqueezeToOptionAdapter()
    underlying_signal = adapter.convert(
        mock_signal,
        symbol='RELIANCE',
        instrument_key='NSE_EQ|INE002A01018'
    )

    # Print summary
    print(adapter.create_summary(underlying_signal))
    print(f"\nConverted successfully!")
    print(f"Strategy: {underlying_signal.strategy}")
    print(f"Timeframe: {underlying_signal.timeframe}")
    print(f"Risk Points: {underlying_signal.risk_points}")
    print(f"Reward Points: {underlying_signal.reward_points}")
