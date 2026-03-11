from typing import Optional, Dict
from collections import deque
import pandas as pd
from datetime import timedelta

from core.strategies.base import BaseStrategy, StrategyContext
from core.events import OHLCVBar, SignalEvent, SignalType
from core.analytics.indicators.ema import EMA
from core.analytics.indicators.atr import ATR
from core.analytics.indicators.adx import ADX
from core.analytics.pixityAI_feature_factory import PixityAIFeatureFactory


class PixityAIEventGenerator(BaseStrategy):
    """Legacy PixityAI event generator used by existing tests."""

    def __init__(self, strategy_id: str = "pixityAI_generator", config: Optional[Dict] = None):
        super().__init__(strategy_id, config)
        self.lookback = self.config.get("lookback", 100)
        self.swing_period = self.config.get("swing_period", 5)
        self.reversion_k = self.config.get("reversion_k", 2.0)
        self.time_stop_bars = self.config.get("time_stop_bars", 12)
        self.bar_minutes = self.config.get("bar_minutes", 1)
        self.entry_basis = self.config.get("entry_basis", "next_open")
        self.bars = {}
        self.ema20 = EMA(20)
        self.ema50 = EMA(50)
        self.atr14 = ATR(14)
        self.adx14 = ADX(14)

    def process_bar(self, bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        symbol = bar.symbol
        if symbol not in self.bars:
            self.bars[symbol] = deque(maxlen=self.lookback)
        self.bars[symbol].append(bar)
        if len(self.bars[symbol]) < 50:
            return None

        df = pd.DataFrame([vars(b) for b in self.bars[symbol]])
        df["ema20"] = self.ema20.calculate(df)
        df["ema50"] = self.ema50.calculate(df)
        df["atr"] = self.atr14.calculate(df)
        df["adx"] = self.adx14.calculate(df)

        hlc3 = (df["high"] + df["low"] + df["close"]) / 3
        session_date = df["timestamp"].dt.date
        if df["volume"].sum() > 0:
            pv = hlc3 * df["volume"]
            cum_pv = pv.groupby(session_date).cumsum()
            cum_vol = df["volume"].groupby(session_date).cumsum()
            df["vwap"] = cum_pv / cum_vol
        else:
            df["vwap"] = hlc3.groupby(session_date).expanding().mean().droplevel(0)

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        vol_std = df["volume"].std()
        vol_z = (bar.volume - df["volume"].mean()) / vol_std if vol_std and vol_std > 0 else 0.0
        features = PixityAIFeatureFactory.get_features(
            bar=bar,
            indicators={"vwap": curr["vwap"], "ema20": curr["ema20"], "atr": curr["atr"], "adx": curr["adx"]},
            prev_indicators={"ema20": prev["ema20"]},
            vol_z=vol_z,
        )

        if curr["close"] > curr["vwap"] and curr["ema20"] > curr["ema50"]:
            swing_high = self._get_last_swing_high(df, self.swing_period)
            if swing_high and prev["close"] <= swing_high < curr["close"]:
                return self._create_signal(bar, SignalType.BUY, "TREND", features, float(curr["atr"]))

        if curr["close"] < curr["vwap"] and curr["ema20"] < curr["ema50"]:
            swing_low = self._get_last_swing_low(df, self.swing_period)
            if swing_low and prev["close"] >= swing_low > curr["close"]:
                return self._create_signal(bar, SignalType.SELL, "TREND", features, float(curr["atr"]))
        return None

    def _get_last_swing_high(self, df: pd.DataFrame, period: int) -> Optional[float]:
        for i in range(len(df) - 2, period, -1):
            window = df["high"].iloc[i - period: i + period + 1]
            if df["high"].iloc[i] == window.max():
                return float(df["high"].iloc[i])
        return None

    def _get_last_swing_low(self, df: pd.DataFrame, period: int) -> Optional[float]:
        for i in range(len(df) - 2, period, -1):
            window = df["low"].iloc[i - period: i + period + 1]
            if df["low"].iloc[i] == window.min():
                return float(df["low"].iloc[i])
        return None

    def _create_signal(
        self,
        bar: OHLCVBar,
        signal_type: SignalType,
        event_type: str,
        features: Dict,
        atr: float,
    ) -> SignalEvent:
        metadata = dict(features)
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
            metadata=metadata,
        )

