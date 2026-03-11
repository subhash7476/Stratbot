import pandas as pd
import numpy as np
from typing import Optional, Dict
from datetime import datetime

from core.strategies.base import BaseStrategy, StrategyContext
from core.events import OHLCVBar, SignalEvent, SignalType

class CommodityTrendStrategy(BaseStrategy):
    """
    Trend-following strategy for Commodities (e.g., Crude Oil, Gold).
    Uses Dual EMA (20, 50) and ATR for volatility/SL.
    """
    
    def __init__(self, strategy_id: str, config: Optional[Dict] = None):
        super().__init__(strategy_id, config)
        
        # Default config
        self.fast_ema_period = self.config.get("fast_ema_period", 20)
        self.slow_ema_period = self.config.get("slow_ema_period", 50)
        self.atr_period = self.config.get("atr_period", 14)
        self.rsi_period = self.config.get("rsi_period", 14)
        self.adx_period = self.config.get("adx_period", 14)
        
        # State
        self.bars = {}
        self.current_position = {} # symbol -> position (1 for long, -1 for short, 0 for none)
        self.entry_price = {}
        self.stop_loss = {}
        self.take_profit = {}

    def _calculate_rsi(self, series, period):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def process_bar(self, bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        symbol = bar.symbol
        
        if symbol not in self.bars:
            self.bars[symbol] = []
            self.current_position[symbol] = 0
            
        self.bars[symbol].append({
            'timestamp': bar.timestamp,
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'volume': bar.volume
        })
        
        # We need enough bars for indicators
        if len(self.bars[symbol]) < 100:
            return None
            
        if len(self.bars[symbol]) > 200:
            self.bars[symbol] = self.bars[symbol][-200:]
            
        df = pd.DataFrame(self.bars[symbol])
        
        # Calculate Indicators
        df['ema_fast'] = df['close'].ewm(span=self.fast_ema_period, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.slow_ema_period, adjust=False).mean()
        df['rsi'] = self._calculate_rsi(df['close'], self.rsi_period)
        
        # ATR for SL
        df['prev_close'] = df['close'].shift(1)
        df['tr'] = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['prev_close']).abs(),
            (df['low'] - df['prev_close']).abs()
        ], axis=1).max(axis=1)
        df['atr'] = df['tr'].rolling(window=self.atr_period).mean()
        
        current_idx = -1
        prev_idx = -2
        
        close_current = df['close'].iloc[current_idx]
        ema_fast_current = df['ema_fast'].iloc[current_idx]
        ema_slow_current = df['ema_slow'].iloc[current_idx]
        ema_fast_prev = df['ema_fast'].iloc[prev_idx]
        ema_slow_prev = df['ema_slow'].iloc[prev_idx]
        rsi_current = df['rsi'].iloc[current_idx]
        atr_current = df['atr'].iloc[current_idx]
        
        pos = self.current_position.get(symbol, 0)
        signal_type = None
        metadata = {
            "close": float(close_current),
            "rsi": float(rsi_current),
            "atr": float(atr_current)
        }

        # 1. CHECK FOR EXITS (SL/TP or Reversal)
        if pos == 1: # Long
            if close_current <= self.stop_loss.get(symbol, 0) or close_current >= self.take_profit.get(symbol, 999999):
                signal_type = SignalType.EXIT
                self.current_position[symbol] = 0
                metadata["reason"] = "SL/TP Hit"
            elif ema_fast_current < ema_slow_current: # Trend reversal exit
                signal_type = SignalType.EXIT
                self.current_position[symbol] = 0
                metadata["reason"] = "Trend Reversal"
                
        elif pos == -1: # Short
            if close_current >= self.stop_loss.get(symbol, 999999) or close_current <= self.take_profit.get(symbol, 0):
                signal_type = SignalType.EXIT
                self.current_position[symbol] = 0
                metadata["reason"] = "SL/TP Hit"
            elif ema_fast_current > ema_slow_current: # Trend reversal exit
                signal_type = SignalType.EXIT
                self.current_position[symbol] = 0
                metadata["reason"] = "Trend Reversal"

        # 2. CHECK FOR ENTRIES (Only if no active position)
        if self.current_position[symbol] == 0 and not signal_type:
            # Long Entry: Crossover + RSI filter (not overbought)
            if ema_fast_prev <= ema_slow_prev and ema_fast_current > ema_slow_current:
                if rsi_current < 65: # Avoid entering at the very top
                    signal_type = SignalType.BUY
                    self.current_position[symbol] = 1
                    self.entry_price[symbol] = close_current
                    self.stop_loss[symbol] = close_current - (2.0 * atr_current)
                    self.take_profit[symbol] = close_current + (4.0 * atr_current) # 1:2 Risk-Reward
                    metadata["stop_loss"] = self.stop_loss[symbol]
                    metadata["take_profit"] = self.take_profit[symbol]
            
            # Short Entry: Crossover + RSI filter (not oversold)
            elif ema_fast_prev >= ema_slow_prev and ema_fast_current < ema_slow_current:
                if rsi_current > 35: # Avoid entering at the very bottom
                    signal_type = SignalType.SELL
                    self.current_position[symbol] = -1
                    self.entry_price[symbol] = close_current
                    self.stop_loss[symbol] = close_current + (2.0 * atr_current)
                    self.take_profit[symbol] = close_current - (4.0 * atr_current) # 1:2 Risk-Reward
                    metadata["stop_loss"] = self.stop_loss[symbol]
                    metadata["take_profit"] = self.take_profit[symbol]
                
        if signal_type:
            return SignalEvent(
                strategy_id=self.strategy_id,
                symbol=symbol,
                timestamp=bar.timestamp,
                signal_type=signal_type,
                confidence=0.8,
                metadata=metadata
            )
            
        return None
