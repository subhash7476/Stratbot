# Implementation Prompt: PixityAI Batch Backtest

> Copy this entire prompt into a new Claude Code session or give it to a developer.

---

## Task

Fix the PixityAI backtest pipeline so that it uses the **same vectorized feature computation** as the training pipeline. Currently, backtests produce only 4 trades instead of the expected 20-30, because features are computed differently during backtest (bar-by-bar on a 100-bar sliding window) vs training (vectorized on the full DataFrame).

Read `docs/PIXITYAI_BATCH_BACKTEST_IMPLEMENTATION.md` for the full architecture spec, code references, and verification checklist.

## What to Do (4 steps)

### 1. Create `core/strategies/pixityAI_batch_events.py` (NEW file)

Extract these 4 functions from `scripts/pixityAI_workflow.py` (lines 70-231) into this new shared module:
- `compute_session_vwap()`
- `find_swing_highs()`
- `find_swing_lows()`
- `batch_generate_events()`

Copy them exactly as-is. Replace `print()` calls with `logger.debug()`. These functions depend on `core.events`, `core.analytics.indicators.ema`, `core.analytics.indicators.atr`, `core.analytics.indicators.adx`, `numpy`, `pandas`.

### 2. Create `core/strategies/precomputed_signals.py` (NEW file)

A thin BaseStrategy subclass that stores a list of pre-computed SignalEvents indexed by timestamp. Its `process_bar()` returns the matching signal when `bar.timestamp` matches, otherwise None. ~25 lines total.

### 3. Modify `core/backtest/runner.py`

- Move the existing `run()` body into a new `_run_standard()` method
- Add a `_run_pixityAI_batch()` method that:
  1. Loads 1m data via `MarketDataQuery.get_ohlcv()`, resamples via `resample_ohlcv()`
  2. Calls `batch_generate_events()` on the full DataFrame
  3. Filters events through the meta-model (load from `pixityAI_config.json` model_path)
  4. Enriches approved signals via `PixityAIRiskEngine` (adds quantity, sl, tp to metadata)
  5. Creates a `PrecomputedSignalStrategy` with the approved signals
  6. Runs through `TradingRunner` as before (reuses existing TP/SL/time-stop exit logic)
  7. Saves results via existing `_save_run_results()` and `_calculate_metrics()`
- Dispatch in `run()`: if `strategy_id == "pixityAI_meta"` → batch path, else → standard path
- Skip `AnalyticsPopulator.update_all()` for the batch path (batch generator computes its own indicators)

### 4. Update `scripts/pixityAI_workflow.py`

Replace the 4 inline function definitions (lines 70-231) with:
```python
from core.strategies.pixityAI_batch_events import (
    compute_session_vwap, find_swing_highs, find_swing_lows, batch_generate_events,
)
```
Keep `load_candles_from_duckdb()` in the script (it's CLI-specific).

## Key Things to Watch For

- **Idempotency guard**: `ExecutionHandler` hashes `{symbol}_{strategy_id}_{timestamp}` and blocks duplicate signals. If the backtest DB retains signals from previous runs, new runs will be blocked. Check if this uses the shared `trading.db` or a run-scoped DB. If shared, disable the guard for backtests or include `run_id` in the hash.

- **ExecutionHandler already respects metadata.quantity** (handler.py line 290-294) — no change needed there.

- **TradingRunner already has TP/SL/time-stop exit logic** (runner.py lines 139-196) — no change needed there.

- **DuckDBAnalyticsProvider** is required by TradingRunner constructor but PrecomputedSignalStrategy doesn't use it. Just pass a default instance.

## Verification

After implementation:
1. Run `python scripts/pixityAI_workflow.py --symbol "NSE_EQ|INE002A01018" --timeframe 1h --start 2024-01-01` — should still produce ~104 events and train a model
2. Start Flask (`python scripts/run_flask.py`) and run a backtest: pixityAI_meta, RELIANCE [FO], 1 Hour, 2025-01-05 to 2026-01-31
3. Expect 20-30 trades with proper metrics (win rate, PnL, hold times ≤ 12 market hours)
4. Run a backtest with a different strategy (e.g. `ehma_pivot`) to verify it still uses the standard path

## Reference Files

| File | Role |
|------|------|
| `scripts/pixityAI_workflow.py` | Source of batch functions to extract |
| `core/strategies/pixityAI_event_generator.py` | Bar-by-bar generator (NOT used in new path) |
| `core/strategies/pixityAIMetaStrategy.py` | Meta-filter logic (reference for filter implementation) |
| `core/backtest/runner.py` | Main file to modify |
| `core/runner.py` | TradingRunner with exit logic (no changes needed) |
| `core/execution/handler.py` | Execution + idempotency guard |
| `core/execution/pixityAI_risk_engine.py` | TP/SL/qty calculation |
| `core/models/pixityAI_config.json` | Active config |
| `core/analytics/resampler.py` | 1m→TF resampling (9:15-aligned) |
| `docs/PIXITYAI_BATCH_BACKTEST_IMPLEMENTATION.md` | Full architecture spec |
