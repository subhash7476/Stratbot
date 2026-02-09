import unittest
from unittest.mock import MagicMock, patch
import numpy as np
from datetime import datetime, timedelta
from core.events import OHLCVBar, SignalEvent, SignalType
from core.strategies.pixityAI_event_generator import PixityAIEventGenerator
from core.strategies.pixityAIMetaStrategy import PixityAIMetaStrategy
from core.execution.pixityAI_risk_engine import PixityAIRiskEngine
from core.strategies.base import StrategyContext


def make_context(symbol="TEST"):
    return StrategyContext(
        symbol=symbol, current_position=0,
        analytics_snapshot=None, market_regime=None, strategy_params={}
    )


def make_bar(symbol, timestamp, open_, high, low, close, volume=1000):
    return OHLCVBar(
        symbol=symbol, timestamp=timestamp,
        open=open_, high=high, low=low, close=close, volume=volume
    )


def build_trending_bars(symbol="TEST", count=60, start_price=100.0, trend=0.1):
    """Build a series of upward-trending bars."""
    bars = []
    start = datetime(2024, 1, 2, 9, 15)
    for i in range(count):
        price = start_price + i * trend
        bars.append(make_bar(
            symbol, start + timedelta(minutes=i),
            open_=price - 0.05, high=price + 0.5, low=price - 0.5,
            close=price, volume=1000 + i * 10
        ))
    return bars


class TestPixityAIEventGenerator(unittest.TestCase):
    def setUp(self):
        self.generator = PixityAIEventGenerator(config={"lookback": 100, "swing_period": 3})
        self.symbol = "TEST"
        self.context = make_context(self.symbol)

    def test_needs_minimum_bars(self):
        """Generator returns None until 50 bars are accumulated."""
        bar = make_bar(self.symbol, datetime(2024, 1, 2, 9, 15), 100, 101, 99, 100)
        result = self.generator.process_bar(bar, self.context)
        self.assertIsNone(result)

    def test_runs_without_error_on_trending_data(self):
        """Generator processes trending bars without exceptions."""
        bars = build_trending_bars(count=60)
        for bar in bars:
            self.generator.process_bar(bar, self.context)

    def test_signal_has_required_metadata(self):
        """If a signal is generated, it must contain all 7 features + event metadata."""
        bars = build_trending_bars(count=80)
        signal = None
        for bar in bars:
            result = self.generator.process_bar(bar, self.context)
            if result is not None:
                signal = result
                break

        if signal is not None:
            required_features = ["vwap_dist", "ema_slope", "atr_pct", "adx", "hour", "minute", "vol_z"]
            for key in required_features:
                self.assertIn(key, signal.metadata, f"Missing feature: {key}")
            self.assertIn("event_type", signal.metadata)
            self.assertIn("entry_price_at_event", signal.metadata)
            self.assertIn("atr_at_event", signal.metadata)

    def test_signal_type_is_buy_or_sell(self):
        """Generator should only produce BUY or SELL signals."""
        bars = build_trending_bars(count=80)
        for bar in bars:
            result = self.generator.process_bar(bar, self.context)
            if result is not None:
                self.assertIn(result.signal_type, [SignalType.BUY, SignalType.SELL])


class TestPixityAIMetaStrategy(unittest.TestCase):
    def setUp(self):
        self.config = {
            "lookback": 100,
            "swing_period": 3,
            "long_threshold": 0.6,
            "short_threshold": 0.6,
            "cooldown_bars": 5,
            "risk_per_trade": 500.0,
            "max_daily_trades": 10,
            "model_path": "nonexistent_model.joblib",
        }
        self.strategy = PixityAIMetaStrategy(config=self.config)
        self.symbol = "TEST"
        self.context = make_context(self.symbol)

    def test_no_model_graceful_degradation(self):
        """Without a model file, strategy should still run (no ML filtering)."""
        self.assertIsNone(self.strategy.model)
        bars = build_trending_bars(count=60)
        for bar in bars:
            self.strategy.process_bar(bar, self.context)

    def test_cooldown_blocks_signal(self):
        """After record_exit, signals should be blocked for cooldown_bars."""
        self.strategy.bars_processed[self.symbol] = 10
        self.strategy.record_exit(self.symbol)

        # bars_processed = 10, last_exit_bar = 10, cooldown = 5
        # bars 11-14 should be blocked, bar 15 should pass cooldown
        self.strategy.bars_processed[self.symbol] = 12
        in_cooldown = (
            self.symbol in self.strategy.last_exit_bar
            and self.strategy.bars_processed[self.symbol] - self.strategy.last_exit_bar[self.symbol] < self.strategy.cooldown_bars
        )
        self.assertTrue(in_cooldown, "Should be in cooldown at bar 12")

        self.strategy.bars_processed[self.symbol] = 16
        past_cooldown = (
            self.strategy.bars_processed[self.symbol] - self.strategy.last_exit_bar[self.symbol] >= self.strategy.cooldown_bars
        )
        self.assertTrue(past_cooldown, "Should be past cooldown at bar 16")

    def _make_candidate(self):
        return SignalEvent(
            strategy_id="test_gen",
            symbol=self.symbol,
            timestamp=datetime(2024, 1, 2, 10, 0),
            signal_type=SignalType.BUY,
            confidence=0.5,
            metadata={
                "vwap_dist": 0.01, "ema_slope": 0.002, "atr_pct": 0.015,
                "adx": 30.0, "hour": 10, "minute": 0, "vol_z": 1.2,
                "event_type": "TREND", "entry_price_at_event": 105.0,
                "atr_at_event": 1.5, "side": "BUY",
                "entry_price_basis": "next_open", "h_bars": 12,
                "event_end_time": "2024-01-02T11:00:00",
            }
        )

    def test_meta_filter_rejects_low_prob(self):
        """Mock model returning low probability should result in None."""
        mock_model = MagicMock()
        mock_model.predict_proba.return_value = np.array([[0.8, 0.2]])
        self.strategy.model = mock_model

        with patch.object(self.strategy.generator, 'process_bar', return_value=self._make_candidate()):
            bar = make_bar(self.symbol, datetime(2024, 1, 2, 10, 0), 105, 106, 104, 105)
            result = self.strategy.process_bar(bar, self.context)

        self.assertIsNone(result, "Low probability should be rejected")

    def test_meta_filter_passes_high_prob(self):
        """Mock model returning high probability should produce a signal."""
        mock_model = MagicMock()
        mock_model.predict_proba.return_value = np.array([[0.2, 0.8]])
        self.strategy.model = mock_model

        with patch.object(self.strategy.generator, 'process_bar', return_value=self._make_candidate()):
            bar = make_bar(self.symbol, datetime(2024, 1, 2, 10, 0), 105, 106, 104, 105)
            result = self.strategy.process_bar(bar, self.context)

        self.assertIsNotNone(result, "High probability should produce a signal")
        self.assertAlmostEqual(result.confidence, 0.8, places=2)
        self.assertEqual(result.strategy_id, "pixityAI_meta")

    def test_signal_enriched_with_sizing(self):
        """Signal metadata should contain quantity, sl, tp from risk engine."""
        with patch.object(self.strategy.generator, 'process_bar', return_value=self._make_candidate()):
            bar = make_bar(self.symbol, datetime(2024, 1, 2, 10, 0), 105, 106, 104, 105)
            result = self.strategy.process_bar(bar, self.context)

        self.assertIsNotNone(result)
        self.assertIn("quantity", result.metadata)
        self.assertIn("sl", result.metadata)
        self.assertIn("tp", result.metadata)
        self.assertGreater(result.metadata["quantity"], 0)


class TestPixityAIRiskEngine(unittest.TestCase):
    def setUp(self):
        self.engine = PixityAIRiskEngine(risk_per_trade=500.0, max_daily_trades=10)

    def _make_signal(self, signal_type, entry_price, atr):
        return SignalEvent(
            strategy_id="test", symbol="TEST",
            timestamp=datetime(2024, 1, 2, 10, 0),
            signal_type=signal_type, confidence=0.7,
            metadata={"entry_price_at_event": entry_price, "atr_at_event": atr}
        )

    def test_atr_based_sizing(self):
        """Quantity should equal risk_per_trade / atr_distance."""
        result = self.engine.calculate_position(self._make_signal(SignalType.BUY, 100.0, 2.0), 100000)
        self.assertEqual(result["quantity"], 250)  # 500 / 2.0

    def test_buy_sl_tp_direction(self):
        """BUY: SL below entry, TP above entry."""
        result = self.engine.calculate_position(self._make_signal(SignalType.BUY, 100.0, 2.0), 100000)
        self.assertLess(result["sl"], 100.0)
        self.assertGreater(result["tp"], 100.0)

    def test_sell_sl_tp_direction(self):
        """SELL: SL above entry, TP below entry."""
        result = self.engine.calculate_position(self._make_signal(SignalType.SELL, 100.0, 2.0), 100000)
        self.assertGreater(result["sl"], 100.0)
        self.assertLess(result["tp"], 100.0)

    def test_stt_only_on_sell(self):
        """STT should be charged only on SELL side."""
        stt_buy = self.engine.calculate_costs(SignalType.BUY, 100.0, 100)
        stt_sell = self.engine.calculate_costs(SignalType.SELL, 100.0, 100)
        self.assertEqual(stt_buy, 0.0)
        self.assertGreater(stt_sell, 0.0)
        self.assertAlmostEqual(stt_sell, 0.00025 * 100 * 100, places=4)

    def test_zero_atr_returns_zero_quantity(self):
        """Zero ATR should return zero quantity to avoid division errors."""
        result = self.engine.calculate_position(self._make_signal(SignalType.BUY, 100.0, 0.0), 100000)
        self.assertEqual(result["quantity"], 0)

    def test_leverage_limit(self):
        """Position notional should not exceed 5x equity."""
        # risk_per_trade=500, atr=0.01 → quantity=50000 → notional=500000
        # equity=10000 → max_notional=50000 → max_qty=5000
        result = self.engine.calculate_position(self._make_signal(SignalType.BUY, 10.0, 0.01), 10000)
        max_notional = 10000 * 5.0
        actual_notional = result["quantity"] * 10.0
        self.assertLessEqual(actual_notional, max_notional)


if __name__ == "__main__":
    unittest.main()
