# PixityAI Batch Backtest Implementation Spec

## Problem Statement

PixityAI backtests produce far fewer trades than expected because **features are computed differently during training vs backtest**:

- **Training** (`scripts/pixityAI_workflow.py`): Uses `batch_generate_events()` — indicators computed once on the entire DataFrame vectorized, swing points pre-computed with forward-fill across the full history.
- **Backtest** (bar-by-bar via `core/strategies/pixityAI_event_generator.py`): Rebuilds a DataFrame from a 100-bar deque on every bar, recalculates all indicators, searches for swing points dynamically by scanning backward.

This causes different feature values (VWAP, EMA slope, ATR, ADX, swing levels) at the same bar → the meta-model sees different inputs → rejects most events. Result: training detects ~104 events but backtest produces only 4 trades.

## Solution

Use the **same vectorized `batch_generate_events()` function** for both training and backtesting. Pre-compute all events+features on the full DataFrame, filter through the meta-model, then feed approved signals into TradingRunner for execution with TP/SL/time-stop exits.

### Why This Is Also Viable for Live Trading

All PixityAI features are **causal** (look backward only):
- **VWAP**: Resets each session — only needs current session's bars
- **EMA20/50**: Converges within ~60 bars of warmup
- **ATR14, ADX14**: Converge within ~40 bars
- **vol_z**: Rolling 100-bar z-score

Computing these on a 200-bar rolling window gives **mathematically identical values** as computing on the full 2-year dataset at the same position. So in live mode, maintain a buffer of 200 bars and call the same `batch_generate_events()` on each new bar — guaranteed feature consistency.

---

## Architecture Overview

```
TRAINING (workflow script)                    BACKTEST (new batch path)
========================                      ========================
Load 1m data from DuckDB                     Load 1m data from DuckDB
Resample to target TF                        Resample to target TF
batch_generate_events(df) → events            batch_generate_events(df) → events
Triple-barrier labeling                       Meta-model filter (same model)
Train RandomForest model                      Risk engine enrichment (TP/SL/qty)
                                              ↓
                                              PrecomputedSignalStrategy(signals)
                                              ↓
                                              TradingRunner iterates bars:
                                                - check_exit_conditions (TP/SL/time-stop)
                                                - replay pre-computed signal at matching timestamp
                                                - execute via ExecutionHandler
                                              ↓
                                              Save trades + metrics to DuckDB

LIVE (future, same principle)
=============================
Buffer last 200 bars
On new bar: batch_generate_events(buffer)
Check if latest bar has event
Meta-model filter → execute if approved
```

---

## Implementation Steps

### Step 1: Extract Batch Functions into Shared Module

**Create**: `core/strategies/pixityAI_batch_events.py`

Move these 4 functions from `scripts/pixityAI_workflow.py` (lines 70-231):

1. `compute_session_vwap(df: pd.DataFrame) -> pd.Series`
   - Computes intraday VWAP per session, with TWAP fallback for zero-volume data
   - Source: `scripts/pixityAI_workflow.py` lines 70-86

2. `find_swing_highs(highs: pd.Series, period: int = 5) -> pd.Series`
   - Detects swing highs via rolling window max, forward-fills
   - Source: `scripts/pixityAI_workflow.py` lines 89-96

3. `find_swing_lows(lows: pd.Series, period: int = 5) -> pd.Series`
   - Same for lows
   - Source: `scripts/pixityAI_workflow.py` lines 99-106

4. `batch_generate_events(df, swing_period=5, reversion_k=2.0, time_stop_bars=12, bar_minutes=1) -> list`
   - Vectorized event generation — computes EMA20/50, ATR14, ADX14, session VWAP, vol_z, swing points
   - Scans for TREND (LONG/SHORT) and REVERSION (LONG/SHORT) conditions
   - Returns list of `SignalEvent` objects with 7 ML features in metadata
   - Source: `scripts/pixityAI_workflow.py` lines 109-231

**Important**: These functions depend on:
- `core.events.SignalEvent`, `core.events.SignalType`
- `core.analytics.indicators.ema.EMA`
- `core.analytics.indicators.atr.ATR`
- `core.analytics.indicators.adx.ADX`
- `numpy`, `pandas`

No logic changes — pure extraction. Remove `print()` statements (replace with `logging.debug()` or remove entirely since they're only useful in CLI).

Then update `scripts/pixityAI_workflow.py` to import from the shared module:
```python
from core.strategies.pixityAI_batch_events import (
    compute_session_vwap,
    find_swing_highs,
    find_swing_lows,
    batch_generate_events,
)
```

Delete the 4 inline function definitions. Keep `load_candles_from_duckdb()` in the script (it's CLI-specific).

### Step 2: Create PrecomputedSignalStrategy

**Create**: `core/strategies/precomputed_signals.py`

```python
from typing import Optional, Dict, List
from datetime import datetime
from core.strategies.base import BaseStrategy, StrategyContext
from core.events import OHLCVBar, SignalEvent

class PrecomputedSignalStrategy(BaseStrategy):
    """Replays pre-computed signals at matching bar timestamps.

    Used for backtest with batch-generated events to guarantee
    feature consistency with training pipeline.
    """
    def __init__(self, strategy_id: str, signals: List[SignalEvent]):
        super().__init__(strategy_id, {})
        # Index by timestamp for O(1) lookup
        self._signals: Dict[datetime, SignalEvent] = {}
        for s in signals:
            self._signals[s.timestamp] = s
        self._total = len(signals)

    def process_bar(self, bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        return self._signals.get(bar.timestamp)
```

This is a thin adapter — TradingRunner calls `strategy.process_bar(bar, context)` on each bar. When the bar's timestamp matches a pre-computed signal, the signal is returned. TradingRunner then:
1. Executes via ExecutionHandler at `bar.close`
2. Registers exit params (sl, tp, h_bars) from `signal.metadata` into `_open_exit_params`
3. On subsequent bars, `_check_exit_conditions()` monitors TP/SL/time-stop

**Key compatibility facts** (already verified):
- ExecutionHandler respects `metadata.quantity` when > 0 (handler.py line 290-294)
- TradingRunner reads `sl`, `tp`, `h_bars` from signal.metadata (runner.py line 254-261)
- Exit logic for LONG/SHORT TP/SL/TIME_STOP is already implemented (runner.py line 139-196)

### Step 3: Add Batch Backtest Path to BacktestRunner

**Modify**: `core/backtest/runner.py`

Refactor `run()` to dispatch based on strategy type:

```python
def run(self, strategy_id, symbol, start_time, end_time, initial_capital=100000.0,
        strategy_params=None, timeframe='1m', run_id=None):
    run_id = run_id or str(uuid.uuid4())
    strategy_params = strategy_params or {}

    # Record in index DB (existing code, unchanged)
    with self.db.backtest_index_writer() as conn:
        conn.execute("""...""", [...])

    try:
        if strategy_id == "pixityAI_meta":
            self._run_pixityAI_batch(run_id, symbol, start_time, end_time,
                                      initial_capital, strategy_params, timeframe)
        else:
            self._run_standard(run_id, strategy_id, symbol, start_time, end_time,
                                initial_capital, strategy_params, timeframe)

        # Update index to COMPLETED (existing code)
        metrics = self._calculate_metrics(self._last_execution, run_id)
        with self.db.backtest_index_writer() as conn:
            conn.execute("""UPDATE ...""", [...])
        return run_id

    except Exception as e:
        # Error handling (existing code, unchanged)
        ...
```

Move existing run logic into `_run_standard()` (no changes to it).

Add new `_run_pixityAI_batch()`:

```python
def _run_pixityAI_batch(self, run_id, symbol, start_time, end_time,
                         initial_capital, strategy_params, timeframe):
    """Batch-vectorized backtest for PixityAI — guarantees feature consistency with training."""
    import joblib
    from pathlib import Path
    from dataclasses import replace
    from core.strategies.pixityAI_batch_events import batch_generate_events
    from core.strategies.precomputed_signals import PrecomputedSignalStrategy
    from core.execution.pixityAI_risk_engine import PixityAIRiskEngine
    from core.database.queries import MarketDataQuery
    from core.analytics.resampler import resample_ohlcv

    # 1. Load PixityAI config
    config_path = Path("core/models/pixityAI_config.json")
    with open(config_path) as f:
        config = json.load(f)

    # 2. Load market data as DataFrame
    query = MarketDataQuery(self.db)
    df = query.get_ohlcv(instrument_key=symbol, start_time=start_time,
                          end_time=end_time, timeframe='1m')

    target_tf = strategy_params.get('timeframe', timeframe) or '1m'
    if target_tf != '1m':
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = resample_ohlcv(df, target_tf)

    logger.info(f"PixityAI batch: {len(df)} bars ({target_tf}) for {symbol}")

    # 3. Batch generate events (same as training pipeline)
    bar_minutes = config.get('bar_minutes', 60)
    events = batch_generate_events(
        df,
        swing_period=config.get('swing_period', 5),
        reversion_k=config.get('reversion_k', 2.0),
        time_stop_bars=config.get('time_stop_bars', 12),
        bar_minutes=bar_minutes,
    )
    logger.info(f"PixityAI batch: {len(events)} candidate events")

    # 4. Meta-model filter
    model = joblib.load(config['model_path'])
    feature_keys = ["vwap_dist", "ema_slope", "atr_pct", "adx", "hour", "minute", "vol_z"]
    risk_engine = PixityAIRiskEngine(
        risk_per_trade=config.get('risk_per_trade', 500.0),
        max_daily_trades=config.get('max_daily_trades', 10),
    )

    approved = []
    for event in events:
        features = [event.metadata.get(k, 0.0) for k in feature_keys]
        try:
            probs = model.predict_proba([features])[0]
            pos_prob = probs[-1]
        except Exception:
            continue

        threshold = (config.get('long_threshold', 0.45)
                     if event.signal_type == SignalType.BUY
                     else config.get('short_threshold', 0.45))

        if pos_prob < threshold:
            continue

        # Enrich with risk engine sizing
        enriched_event = replace(event, confidence=float(pos_prob), strategy_id="pixityAI_meta")
        pos_info = risk_engine.calculate_position(enriched_event, initial_capital)
        enriched_meta = {
            **enriched_event.metadata,
            'quantity': pos_info['quantity'],
            'sl': pos_info['sl'],
            'tp': pos_info['tp'],
        }
        approved.append(replace(enriched_event, metadata=enriched_meta))

    logger.info(f"PixityAI batch: {len(approved)} signals after meta-filter")

    # 5. Setup execution components (same as standard path)
    clock = ReplayClock(start_time)
    market_data = DuckDBMarketDataProvider(
        symbols=[symbol], db_manager=self.db,
        timeframe=target_tf, start_time=start_time, end_time=end_time,
    )
    broker = PaperBroker(clock)
    exec_config = ExecutionConfig(mode=ExecutionMode.PAPER, max_drawdown_limit=0.99)
    execution = ExecutionHandler(
        db_manager=self.db, clock=clock, broker=broker,
        config=exec_config, initial_capital=initial_capital,
    )
    position_tracker = PositionTracker()

    # 6. Create PrecomputedSignalStrategy and run
    strategy = PrecomputedSignalStrategy("pixityAI_meta", approved)
    runner = TradingRunner(
        config=RunnerConfig(
            symbols=[symbol],
            strategy_ids=["pixityAI_meta"],
            disable_state_update=True,
        ),
        db_manager=self.db,
        market_data_provider=market_data,
        analytics_provider=DuckDBAnalyticsProvider(db_manager=self.db),
        strategies=[strategy],
        execution_handler=execution,
        position_tracker=position_tracker,
        clock=clock,
    )
    runner.run()

    # 7. Save results (reuse existing methods)
    self._last_execution = execution
    self._save_run_results(run_id, symbol, execution)
```

**Important notes**:
- Skip `AnalyticsPopulator.update_all()` — batch generator computes its own indicators
- The `DuckDBMarketDataProvider` will re-read 1m data and resample (it's fast, keeps code simple)
- Store execution reference in `self._last_execution` so `_calculate_metrics()` can access it
- The `DuckDBAnalyticsProvider` is passed but PrecomputedSignalStrategy doesn't use it

### Step 4: Handle the Idempotency Guard

The ExecutionHandler has an idempotency guard that generates a signal_id from `{symbol}_{strategy_id}_{timestamp}` and checks `trading.db` for duplicates. Previous backtest runs may have stored signal IDs that block re-execution.

**Solution**: The execution handler already uses `run_id` scoped databases for backtests. Verify that the idempotency check uses the backtest's isolated DB, not the shared `trading.db`. If it uses `trading.db`, either:
- Disable the idempotency guard for backtest mode (add a flag)
- Or ensure the signal_id includes run_id to make it unique

Check `core/execution/handler.py` line 159: `self._is_signal_already_executed(signal_id)` — trace this to see which DB it queries. If it's `trading.db`, the simplest fix is to add `run_id` to the signal_id hash.

---

## Files Summary

| File | Action | Lines Changed |
|------|--------|---------------|
| `core/strategies/pixityAI_batch_events.py` | **NEW** | ~170 lines (extracted from workflow) |
| `core/strategies/precomputed_signals.py` | **NEW** | ~25 lines |
| `core/backtest/runner.py` | **MODIFY** | ~80 lines added, ~20 refactored |
| `scripts/pixityAI_workflow.py` | **MODIFY** | ~5 lines changed (imports), ~160 lines deleted |

---

## Existing Code References

These files/functions are USED but NOT MODIFIED:

| Component | File | Purpose |
|-----------|------|---------|
| `SignalEvent`, `SignalType` | `core/events.py:53-60, 13-17` | Signal data structure |
| `EMA`, `ATR`, `ADX` | `core/analytics/indicators/` | Indicator computation |
| `resample_ohlcv()` | `core/analytics/resampler.py` | 1m → target TF resampling (9:15-aligned) |
| `MarketDataQuery.get_ohlcv()` | `core/database/queries.py` | Read OHLCV from DuckDB |
| `DuckDBMarketDataProvider` | `core/database/providers/market_data.py` | Bar iteration for TradingRunner |
| `DuckDBAnalyticsProvider` | `core/database/providers/analytics.py` | Analytics (unused by PixityAI but required by runner) |
| `PixityAIRiskEngine` | `core/execution/pixityAI_risk_engine.py` | ATR-based TP/SL/quantity calculation |
| `ExecutionHandler` | `core/execution/handler.py` | Trade execution, respects metadata.quantity (line 290-294) |
| `TradingRunner._check_exit_conditions()` | `core/runner.py:139-196` | TP/SL/time-stop exit monitoring |
| `TradingRunner._open_exit_params` | `core/runner.py:72, 254-269` | Exit param registration on new position |
| `ReplayClock` | `core/clock.py` | Simulated clock for backtesting |
| `PaperBroker` | `core/brokers/paper_broker.py` | Paper trading broker |
| `PositionTracker` | `core/execution/position_tracker.py` | Position state management |
| `pixityAI_config.json` | `core/models/pixityAI_config.json` | Active config (model_path, thresholds, etc.) |

---

## Config File (current state)

`core/models/pixityAI_config.json`:
```json
{
    "strategy_id": "pixityAI_meta",
    "lookback": 100,
    "swing_period": 5,
    "reversion_k": 2.0,
    "sl_mult": 1.0,
    "tp_mult": 2.0,
    "time_stop_bars": 12,
    "bar_minutes": 60,
    "long_threshold": 0.45,
    "short_threshold": 0.45,
    "cooldown_bars": 3,
    "risk_per_trade": 500.0,
    "max_daily_trades": 10,
    "model_path": "core/models/pixityAI_ine002a01018_1h.joblib"
}
```

---

## Verification Checklist

After implementation, run these checks:

1. **Workflow script still works**:
   ```
   python scripts/pixityAI_workflow.py --symbol "NSE_EQ|INE002A01018" --timeframe 1h --start 2024-01-01
   ```
   Should produce same output as before (104 events, model trained, saved).

2. **Backtest via Flask UI**:
   - Strategy: `pixityAI_meta`, Symbol: RELIANCE [FO], Timeframe: 1 Hour
   - Date range: 2025-01-05 to 2026-01-31
   - Expected: 20-30+ trades (vs the 4 trades the broken bar-by-bar path produced)
   - Win rate and PnL should display correctly in the UI

3. **Trade count consistency**:
   - The number of trades should be close to what `batch_generate_events()` produces after meta-filter
   - Exact match won't happen because execution effects (cooldown between exits and new entries, capital constraints) reduce actual trades slightly

4. **Other strategies unaffected**:
   - Run a backtest with `ehma_pivot` or `premium_tp_sl` — should use existing `_run_standard()` path unchanged

5. **Hold times reasonable**:
   - All trades should exit within 12 bars (time_stop_bars=12) = max 12 hours of market time
   - Calendar hours may be larger due to weekends/overnight, but bar count should be ≤ 12
