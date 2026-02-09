"""
Regime Detection Engine
----------------------
Categorizes market state based on volatility, trend, and momentum.
"""
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, Any
import pandas as pd
import numpy as np

from core.analytics.indicators.ema import EMA
from core.analytics.indicators.adx import ADX
from core.analytics.indicators.atr import ATR

@dataclass(frozen=True)
class RegimeSnapshot:
    insight_id: str
    symbol: str
    timestamp: datetime
    regime: str # BULL_TREND, BEAR_TREND, RANGING, VOLATILE_RANGE, UNKNOWN
    momentum_bias: str # BULLISH, BEARISH, NEUTRAL
    trend_strength: float # 0.0 to 1.0 (normalized ADX)
    volatility_level: str # LOW, MEDIUM, HIGH, EXTREME
    persistence_score: float # 0.0 to 1.0
    ma_fast: float
    ma_medium: float
    ma_slow: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class RegimeDetector:
    """
    Engine that analyzes OHLCV data to classify market conditions.
    """
    def __init__(self):
        self.ema_fast = EMA(20)
        self.ema_med = EMA(50)
        self.ema_slow = EMA(200)
        self.adx = ADX(14)
        self.atr = ATR(14)

    def detect(self, symbol: str, df: pd.DataFrame) -> Optional[RegimeSnapshot]:
        """
        Processes the last bar of the provided DataFrame to determine the current regime.
        """
        if len(df) < 50: # Need enough data for EMAs and ADX
            return None

        # 1. Calculate Indicators
        f_ema = self.ema_fast.calculate(df).iloc[-1]
        m_ema = self.ema_med.calculate(df).iloc[-1]
        s_ema = self.ema_slow.calculate(df).iloc[-1]
        adx_val = self.adx.calculate(df).iloc[-1]
        atr_val = self.atr.calculate(df).iloc[-1]
        
        last_close = df['close'].iloc[-1]
        
        # 2. Determine Trend Strength (Normalized ADX)
        # ADX > 25 is trending, > 40 is very strong, < 20 is ranging
        trend_strength = min(adx_val / 50.0, 1.0) 
        
        # 3. Determine Volatility Level
        # ATR as % of price
        atr_pct = (atr_val / last_close) * 100
        if atr_pct < 0.5: vol_level = "LOW"
        elif atr_pct < 1.5: vol_level = "MEDIUM"
        elif atr_pct < 3.0: vol_level = "HIGH"
        else: vol_level = "EXTREME"

        # 4. Determine Directional Bias & Regime
        bias = "NEUTRAL"
        regime = "RANGING"
        
        is_bullish = last_close > f_ema > m_ema
        is_bearish = last_close < f_ema < m_ema
        
        if is_bullish:
            bias = "BULLISH"
            regime = "BULL_TREND" if adx_val > 22 else "BULLISH_CONSOLIDATION"
        elif is_bearish:
            bias = "BEARISH"
            regime = "BEAR_TREND" if adx_val > 22 else "BEARISH_CONSOLIDATION"
        
        if adx_val < 20:
            regime = "VOLATILE_RANGE" if vol_level in ["HIGH", "EXTREME"] else "RANGING"

        return RegimeSnapshot(
            insight_id=f"reg_{symbol}_{int(df['timestamp'].iloc[-1].timestamp())}",
            symbol=symbol,
            timestamp=df['timestamp'].iloc[-1],
            regime=regime,
            momentum_bias=bias,
            trend_strength=trend_strength,
            volatility_level=vol_level,
            persistence_score=0.8, # Placeholder for further logic
            ma_fast=float(f_ema),
            ma_medium=float(m_ema),
            ma_slow=float(s_ema)
        )

