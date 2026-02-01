"""
Confluence Engine
-----------------
Aggregates multiple indicator signals into a single insight.
"""
from datetime import datetime
from typing import List, Dict, Any
import pandas as pd

from core.analytics.models import ConfluenceInsight, IndicatorResult, Bias, ConfluenceSignal
from core.analytics.indicators.ema import EMA
from core.analytics.indicators.rsi import RSI
from core.analytics.indicators.macd import MACD

class ConfluenceEngine:
    """
    Combines indicator facts into actionable insights.
    """
    
    def __init__(self):
        self.indicators = {
            'EMA_20': EMA(20),
            'EMA_50': EMA(50),
            'RSI': RSI(14),
            'MACD': MACD()
        }

    def generate_insight(self, symbol: str, df: pd.DataFrame) -> ConfluenceInsight:
        """
        Calculates all indicators and determines overall bias.
        """
        if len(df) < 50:
            return None

        results = []
        last_row = df.iloc[-1]
        
        # EMA Bias
        ema20 = self.indicators['EMA_20'].calculate(df).iloc[-1]
        ema50 = self.indicators['EMA_50'].calculate(df).iloc[-1]
        
        ema_bias = Bias.BULLISH if ema20 > ema50 else Bias.BEARISH
        results.append(IndicatorResult("EMA_Cross", ema_bias, ema20, {"ema50": ema50}))
        
        # RSI Bias
        rsi_val = self.indicators['RSI'].calculate(df).iloc[-1]
        rsi_bias = Bias.NEUTRAL
        if rsi_val > 60: rsi_bias = Bias.BULLISH
        elif rsi_val < 40: rsi_bias = Bias.BEARISH
        results.append(IndicatorResult("RSI", rsi_bias, rsi_val))
        
        # Aggregate
        bullish_count = sum(1 for r in results if r.bias == Bias.BULLISH)
        bearish_count = sum(1 for r in results if r.bias == Bias.BEARISH)
        
        total = len(results)
        confidence = max(bullish_count, bearish_count) / total
        
        overall_bias = Bias.NEUTRAL
        if bullish_count > bearish_count: overall_bias = Bias.BULLISH
        elif bearish_count > bullish_count: overall_bias = Bias.BEARISH
        
        signal = ConfluenceSignal.NEUTRAL
        if confidence > 0.7:
            signal = ConfluenceSignal.BUY if overall_bias == Bias.BULLISH else ConfluenceSignal.SELL
            
        return ConfluenceInsight(
            timestamp=df.index[-1] if isinstance(df.index[-1], datetime) else datetime.now(),
            symbol=symbol,
            bias=overall_bias,
            confidence_score=confidence,
            indicator_results=results,
            signal=signal,
            agreement_level=confidence
        )
