from typing import Optional, Dict, Any
import os
import json
import logging
import joblib
from pathlib import Path
from dataclasses import replace
from datetime import datetime
from core.strategies.base import BaseStrategy, StrategyContext
from core.events import OHLCVBar, SignalEvent, SignalType
from core.strategies.pixityAI_event_generator import PixityAIEventGenerator
from core.execution.pixityAI_risk_engine import PixityAIRiskEngine

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("core/models/pixityAI_config.json")

class PixityAIMetaStrategy(BaseStrategy):
    """
    PixityAI Meta-Filtered Strategy.
    Uses PixityAIEventGenerator to find candidates,
    then filters them through a Meta-Model.
    Enriches signals with ATR-based sizing via PixityAIRiskEngine.
    """

    def __init__(self, strategy_id: str = "pixityAI_meta", config: Optional[Dict] = None):
        # Load base config from JSON, then merge runtime config on top
        base_config = self._load_json_config()
        merged = {**base_config, **(config or {})}
        super().__init__(strategy_id, merged)
        self.generator = PixityAIEventGenerator(f"{strategy_id}_gen", self.config)
        self.model_path = self.config.get("model_path", "core/models/pixityAI_meta_filter.joblib")

        # Separate thresholds
        self.long_threshold = self.config.get("long_threshold", 0.6)
        self.short_threshold = self.config.get("short_threshold", 0.6)

        # Cooldown mechanism
        self.cooldown_bars = self.config.get("cooldown_bars", 5)
        self.last_exit_bar = {} # symbol -> bar_index/count
        self.bars_processed = {} # symbol -> count

        # Risk engine for ATR-based sizing
        self.risk_engine = PixityAIRiskEngine(
            risk_per_trade=self.config.get("risk_per_trade", 500.0),
            max_daily_trades=self.config.get("max_daily_trades", 10),
        )

        self.model = self._load_model()
        logger.info(f"PixityAI initialized: model={self.model_path}, "
                     f"thresholds=({self.long_threshold}/{self.short_threshold}), "
                     f"bar_minutes={self.config.get('bar_minutes', 1)}, "
                     f"model_loaded={self.model is not None}")

    @staticmethod
    def _load_json_config() -> dict:
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _load_model(self):
        if os.path.exists(self.model_path):
            try:
                return joblib.load(self.model_path)
            except Exception:
                return None
        return None

    def process_bar(self, bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        symbol = bar.symbol
        self.bars_processed[symbol] = self.bars_processed.get(symbol, 0) + 1

        # 1. Check Cooldown
        if symbol in self.last_exit_bar:
            if self.bars_processed[symbol] - self.last_exit_bar[symbol] < self.cooldown_bars:
                # Still in cooldown, but we must still update generator's bars
                self.generator.process_bar(bar, context)
                return None

        # 2. Get candidate signal from generator
        candidate = self.generator.process_bar(bar, context)

        if not candidate:
            return None

        # 3. Apply Meta-Model Filter
        confidence = candidate.confidence
        if self.model:
            features = self._prepare_features(candidate.metadata)
            try:
                probs = self.model.predict_proba([features])[0]
                pos_prob = probs[-1]

                threshold = self.long_threshold if candidate.signal_type == SignalType.BUY else self.short_threshold

                if pos_prob < threshold:
                    return None

                confidence = float(pos_prob)
            except Exception:
                return None

        # 4. Enrich with risk engine sizing
        pos_info = self.risk_engine.calculate_position(candidate, 100000)
        enriched_metadata = {
            **candidate.metadata,
            "quantity": pos_info["quantity"],
            "sl": pos_info["sl"],
            "tp": pos_info["tp"],
        }

        return replace(
            candidate,
            strategy_id=self.strategy_id,
            confidence=confidence,
            metadata=enriched_metadata,
        )

    def record_exit(self, symbol: str):
        """Called by TradingRunner when a position is closed."""
        self.last_exit_bar[symbol] = self.bars_processed.get(symbol, 0)

    def _prepare_features(self, metadata: Dict) -> list:
        feature_keys = ["vwap_dist", "ema_slope", "atr_pct", "adx", "hour", "minute", "vol_z"]
        return [metadata.get(k, 0.0) for k in feature_keys]
