from typing import Dict, Any, Optional
import pandas as pd
from core.events import OHLCVBar

class PixityAIFeatureFactory:
    """
    Utility to generate features for the PixityAI Meta-Model.
    Ensures consistency between training and live execution.
    """

    @staticmethod
    def get_features(bar: OHLCVBar, indicators: Dict[str, Any], prev_indicators: Optional[Dict[str, Any]] = None, vol_z: float = 0.0) -> Dict[str, float]:
        """
        Calculates the 7 core features used by the Meta-Model.
        """
        vwap = indicators.get('vwap')
        ema20 = indicators.get('ema20')
        atr = indicators.get('atr')
        adx = indicators.get('adx')

        prev_ema20 = prev_indicators.get('ema20') if prev_indicators else ema20

        features = {
            "vwap_dist": (bar.close - vwap) / vwap if vwap else 0.0,
            "ema_slope": (ema20 - prev_ema20) / prev_ema20 if prev_ema20 else 0.0,
            "atr_pct": atr / bar.close if atr else 0.0,
            "adx": adx if adx else 0.0,
            "hour": float(bar.timestamp.hour),
            "minute": float(bar.timestamp.minute),
            "vol_z": vol_z
        }
        return features
