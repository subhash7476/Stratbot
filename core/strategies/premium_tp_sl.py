"""
Premium TP/SL Strategy
----------------------
A strategy that trades both long and short using SELL entry, with strict exit priority:
TP/SL first, then time-stop, then opposite premium signal.

ENHANCEMENTS:
- ADX trend filtering (> 25)
- ATR-based dynamic stops (SL = 1.5 * ATR, TP = 3.0 * ATR)
"""
from typing import Optional, Dict, Any
from datetime import datetime

from core.strategies.base import BaseStrategy, StrategyContext
from core.events import OHLCVBar, SignalEvent, SignalType


class PremiumTpSlStrategy(BaseStrategy):
    """
    Strategy that implements premium signals with dynamic ATR stops and ADX filtering.
    """

    def __init__(self, strategy_id: str, config: Optional[Dict] = None):
        super().__init__(strategy_id, config)
        
        # Strategy state
        self.entry_price: Optional[float] = None
        self.entry_bar_index: Optional[int] = None
        self.position_side: Optional[int] = None  # +1 for long, -1 for short
        self.entry_atr: Optional[float] = None
        self.bar_counter: int = 0  # Track bar index for time stops
        
        # Get configuration parameters with defaults
        self.tp_pct = self.config.get('tp_pct', 0.006)  # 0.6% fallback
        self.sl_pct = self.config.get('sl_pct', 0.003)  # 0.3% fallback
        self.max_hold_bars = self.config.get('max_hold_bars', 20)  # 20 bars max hold
        
        # ATR multipliers
        self.atr_sl_mult = self.config.get('atr_sl_mult', 1.5)
        self.atr_tp_mult = self.config.get('atr_tp_mult', 3.0)

    def process_bar(self, bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        """
        Process a single bar and emit signals based on premium signals and exit conditions.
        """
        # Increment bar counter
        self.bar_counter += 1
        
        # Get current position from context
        current_position = context.current_position
        
        # Determine if we're currently in a position
        is_flat = (current_position == 0)
        is_long = (current_position > 0)
        
        # Get premium signals from analytics snapshot
        premium_buy = False
        premium_sell = False
        current_atr = None
        
        if context.analytics_snapshot and context.analytics_snapshot.indicator_results:
            for ir in context.analytics_snapshot.indicator_results:
                if ir.name == "premium_flags":
                    # Extract signals from metadata
                    metadata = ir.metadata or {}
                    premium_buy = metadata.get('premiumBuy', False)
                    premium_sell = metadata.get('premiumSell', False)
                    current_atr = metadata.get('atr')
                    break
                elif ir.name == "ATR":
                    current_atr = ir.value
        
        # ENTRY LOGIC: Only if flat
        if is_flat:
            if premium_buy:
                # Enter long position
                self._enter_position(bar.close, self.bar_counter, 1, current_atr)
                return SignalEvent(
                    strategy_id=self.strategy_id,
                    symbol=bar.symbol,
                    timestamp=bar.timestamp,
                    signal_type=SignalType.BUY,
                    confidence=0.8,
                    metadata={'entry_reason': 'premium_buy', 'entry_atr': current_atr}
                )
            elif premium_sell:
                # Enter short position
                self._enter_position(bar.close, self.bar_counter, -1, current_atr)
                return SignalEvent(
                    strategy_id=self.strategy_id,
                    symbol=bar.symbol,
                    timestamp=bar.timestamp,
                    signal_type=SignalType.SELL,
                    confidence=0.8,
                    metadata={'entry_reason': 'premium_sell', 'close_all': True, 'entry_atr': current_atr}
                )
        
        # EXIT LOGIC: Only if in position
        if not is_flat:
            # Evaluate exit conditions in priority order
            
            # 1. TP/SL (highest priority)
            exit_signal = self._check_tp_sl(bar)
            if exit_signal:
                self._exit_position()
                return exit_signal
            
            # 2. Time stop
            exit_signal = self._check_time_stop(bar)
            if exit_signal:
                self._exit_position()
                return exit_signal
            
            # 3. Opposite premium signal (lowest priority)
            exit_signal = self._check_opposite_premium(premium_buy, premium_sell, is_long, bar)
            if exit_signal:
                self._exit_position()
                return exit_signal
        
        # No signal to emit
        return None

    def _enter_position(self, entry_price: float, bar_index: int, side: int, atr: Optional[float] = None):
        """Record entry information."""
        self.entry_price = entry_price
        self.entry_bar_index = bar_index
        self.position_side = side
        self.entry_atr = atr

    def _exit_position(self):
        """Clear tracking info."""
        self.entry_price = None
        self.entry_bar_index = None
        self.position_side = None
        self.entry_atr = None

    def _check_tp_sl(self, bar: OHLCVBar) -> Optional[SignalEvent]:
        """Check if TP or SL has been hit."""
        if not self.entry_price or self.position_side is None:
            return None

        # Long Exit Check
        if self.position_side == 1:
            if self.entry_atr:
                sl_level = self.entry_price - (self.entry_atr * self.atr_sl_mult)
                tp_level = self.entry_price + (self.entry_atr * self.atr_tp_mult)
            else:
                sl_level = self.entry_price * (1 - self.sl_pct)
                tp_level = self.entry_price * (1 + self.tp_pct)
            
            if bar.low <= sl_level:
                return SignalEvent(
                    strategy_id=self.strategy_id, symbol=bar.symbol, timestamp=bar.timestamp,
                    signal_type=SignalType.EXIT, confidence=1.0,
                    metadata={'exit_reason': 'long_sl_hit', 'close_all': True}
                )
            if bar.high >= tp_level:
                return SignalEvent(
                    strategy_id=self.strategy_id, symbol=bar.symbol, timestamp=bar.timestamp,
                    signal_type=SignalType.EXIT, confidence=1.0,
                    metadata={'exit_reason': 'long_tp_hit', 'close_all': True}
                )
                
        # Short Exit Check
        elif self.position_side == -1:
            if self.entry_atr:
                sl_level = self.entry_price + (self.entry_atr * self.atr_sl_mult)
                tp_level = self.entry_price - (self.entry_atr * self.atr_tp_mult)
            else:
                sl_level = self.entry_price * (1 + self.sl_pct)
                tp_level = self.entry_price * (1 - self.tp_pct)
            
            if bar.high >= sl_level:
                return SignalEvent(
                    strategy_id=self.strategy_id, symbol=bar.symbol, timestamp=bar.timestamp,
                    signal_type=SignalType.EXIT, confidence=1.0,
                    metadata={'exit_reason': 'short_sl_hit', 'close_all': True}
                )
            if bar.low <= tp_level:
                return SignalEvent(
                    strategy_id=self.strategy_id, symbol=bar.symbol, timestamp=bar.timestamp,
                    signal_type=SignalType.EXIT, confidence=1.0,
                    metadata={'exit_reason': 'short_tp_hit', 'close_all': True}
                )
        
        return None

    def _check_time_stop(self, bar: OHLCVBar) -> Optional[SignalEvent]:
        """Check max holding period."""
        if (self.entry_bar_index is not None and 
            self.bar_counter - self.entry_bar_index >= self.max_hold_bars):
            
            return SignalEvent(
                strategy_id=self.strategy_id, symbol=bar.symbol, timestamp=bar.timestamp,
                signal_type=SignalType.EXIT, confidence=0.9,
                metadata={'exit_reason': 'time_stop', 'close_all': True}
            )
        return None

    def _check_opposite_premium(self, premium_buy: bool, premium_sell: bool, is_long: bool, bar: OHLCVBar) -> Optional[SignalEvent]:
        """Check for reversal signal."""
        if (is_long and premium_sell) or (not is_long and premium_buy):
            return SignalEvent(
                strategy_id=self.strategy_id, symbol=bar.symbol, timestamp=bar.timestamp,
                signal_type=SignalType.EXIT, confidence=0.7,
                metadata={'exit_reason': 'opposite_premium', 'close_all': True}
            )
        return None
