from typing import Optional, Dict, Any, List
from collections import deque
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from core.strategies.base import BaseStrategy, StrategyContext
from core.events import OHLCVBar, SignalEvent, SignalType
from core.analytics.indicators.ema import EMA
from core.analytics.indicators.vwap import VWAP
from core.analytics.indicators.atr import ATR
from core.analytics.indicators.adx import ADX
from core.analytics.pixityAI_feature_factory import PixityAIFeatureFactory

class PixityAIEventGenerator(BaseStrategy):
    """
    PixityAI Event Generator
    Identifies Trend and Reversion event candidates.
    These are 'raw' signals that will be filtered by a Meta-Model.
    """
    
    def __init__(self, strategy_id: str = "pixityAI_generator", config: Optional[Dict] = None):
        super().__init__(strategy_id, config)
        self.lookback = self.config.get("lookback", 100)
        self.swing_period = self.config.get("swing_period", 5)
        self.reversion_k = self.config.get("reversion_k", 2.0)
        self.time_stop_bars = self.config.get("time_stop_bars", 12)
        self.bar_minutes = self.config.get("bar_minutes", 1)
        self.entry_basis = self.config.get("entry_basis", "next_open") # "close" or "next_open"
        self.bars = {} # symbol -> deque of OHLCVBar
        
        # Indicators
        self.ema20 = EMA(20)
        self.ema50 = EMA(50)
        self.vwap = VWAP()
        self.atr14 = ATR(14)
        self.adx14 = ADX(14)

    def process_bar(self, bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        symbol = bar.symbol
        if symbol not in self.bars:
            self.bars[symbol] = deque(maxlen=self.lookback)
        
        self.bars[symbol].append(bar)
        
        if len(self.bars[symbol]) < 50: # Need enough bars for EMA50
            return None

        # Convert to DataFrame for indicator calculation
        df = pd.DataFrame([vars(b) for b in self.bars[symbol]])
        
        # Calculate Indicators
        df['ema20'] = self.ema20.calculate(df)
        df['ema50'] = self.ema50.calculate(df)
        df['atr'] = self.atr14.calculate(df)
        df['adx'] = self.adx14.calculate(df)

        # Session VWAP (or TWAP fallback for zero-volume index data)
        hlc3 = (df['high'] + df['low'] + df['close']) / 3
        session_date = df['timestamp'].dt.date
        if df['volume'].sum() > 0:
            pv = hlc3 * df['volume']
            cum_pv = pv.groupby(session_date).cumsum()
            cum_vol = df['volume'].groupby(session_date).cumsum()
            df['vwap'] = cum_pv / cum_vol
        else:
            df['vwap'] = hlc3.groupby(session_date).expanding().mean().droplevel(0)
        
        last_idx = len(df) - 1
        curr = df.iloc[last_idx]
        prev = df.iloc[last_idx - 1] if last_idx > 0 else None
        
        if prev is None:
            return None

        # Feature Snapshot for Meta-Model (via shared FeatureFactory)
        vol_z = (bar.volume - df['volume'].mean()) / df['volume'].std() if df['volume'].std() > 0 else 0.0
        features = PixityAIFeatureFactory.get_features(
            bar=bar,
            indicators={"vwap": curr['vwap'], "ema20": curr['ema20'], "atr": curr['atr'], "adx": curr['adx']},
            prev_indicators={"ema20": prev['ema20']},
            vol_z=vol_z,
        )

        # 1. Trend Event
        if curr['close'] > curr['vwap'] and curr['ema20'] > curr['ema50']:
            swing_high = self._get_last_swing_high(df, self.swing_period)
            if swing_high and prev['close'] <= swing_high < curr['close']:
                return self._create_signal(bar, SignalType.BUY, "TREND", features, curr['atr'])

        # Short Trend
        if curr['close'] < curr['vwap'] and curr['ema20'] < curr['ema50']:
            swing_low = self._get_last_swing_low(df, self.swing_period)
            if swing_low and prev['close'] >= swing_low > curr['close']:
                return self._create_signal(bar, SignalType.SELL, "TREND", features, curr['atr'])

        # 2. Reversion Event
        if curr['adx'] < 25:
            upper_band = curr['vwap'] + (self.reversion_k * curr['atr'])
            lower_band = curr['vwap'] - (self.reversion_k * curr['atr'])
            
            if prev['close'] < lower_band and curr['close'] >= lower_band:
                 return self._create_signal(bar, SignalType.BUY, "REVERSION", features, curr['atr'])
            
            if prev['close'] > upper_band and curr['close'] <= upper_band:
                 return self._create_signal(bar, SignalType.SELL, "REVERSION", features, curr['atr'])

        return None

    def _get_last_swing_high(self, df: pd.DataFrame, period: int) -> Optional[float]:
        """Finds the most recent swing high in the dataframe (excluding the current bar)."""
        for i in range(len(df) - 2, period, -1):
            window = df['high'].iloc[i-period : i+period+1]
            if df['high'].iloc[i] == window.max():
                return df['high'].iloc[i]
        return None

    def _get_last_swing_low(self, df: pd.DataFrame, period: int) -> Optional[float]:
        """Finds the most recent swing low in the dataframe (excluding the current bar)."""
        for i in range(len(df) - 2, period, -1):
            window = df['low'].iloc[i-period : i+period+1]
            if df['low'].iloc[i] == window.min():
                return df['low'].iloc[i]
        return None

    def _create_signal(self, bar: OHLCVBar, signal_type: SignalType, event_type: str, features: Dict, atr: float) -> SignalEvent:
        metadata = features.copy()
        metadata.update({
            "event_type": event_type,
            "side": signal_type.value,
            "entry_price_basis": self.entry_basis,
            "entry_price_at_event": bar.close,
            "atr_at_event": atr,
            "h_bars": self.time_stop_bars,
            "bar_minutes": self.bar_minutes,
            "event_end_time": (bar.timestamp + timedelta(minutes=self.time_stop_bars * self.bar_minutes)).isoformat(),
        })
        
        return SignalEvent(
            strategy_id=self.strategy_id,
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            signal_type=signal_type,
            confidence=0.5,
            metadata=metadata
        )
