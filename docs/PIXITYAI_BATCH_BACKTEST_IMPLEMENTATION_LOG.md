# Implementation Log: PixityAI Batch Backtest

## Overview
Synchronized the PixityAI backtest pipeline with the training pipeline by implementing a vectorized batch signal generation path. This fixes the discrepancy where bar-by-bar feature computation during backtests produced fewer trades than expected.

## Changes

### 1. New Modules
- **`core/strategies/pixityAI_batch_events.py`**:
    - Contains `compute_session_vwap`, `find_swing_highs`, `find_swing_lows`, and `batch_generate_events`.
    - These functions perform vectorized computation on full DataFrames.
    - Replaced `print` with `logging` for better integration.
- **`core/strategies/precomputed_signals.py`**:
    - Implements `PrecomputedSignalStrategy`.
    - Stores a map of `timestamp -> SignalEvent`.
    - Returns signals when the clock matches the precomputed timestamp.

### 2. Backtest Runner (`core/backtest/runner.py`)
- Refactored `run()` to dispatch to `_run_pixityAI_batch()` for the `pixityAI_meta` strategy.
- `_run_pixityAI_batch` implementation:
    - Fetches 1m data and resamples to target timeframe.
    - Generates signals in batch using vectorized logic.
    - Loads the Meta-Model and filters raw events.
    - Uses `PixityAIRiskEngine` to calculate quantity, SL, and TP for each approved signal.
    - Runs the `TradingRunner` loop using `PrecomputedSignalStrategy`.

### 3. Workflow Synchronization (`scripts/pixityAI_workflow.py`)
- Removed local definitions of batch functions.
- Now imports from `core.strategies.pixityAI_batch_events`.
- Maintains CLI-specific data loading and training logic.

### 4. Risk Engine Alignment
- Ensured `PixityAIRiskEngine` is used during the enrichment phase of the batch backtest to provide consistent position sizing and exit levels.

## Verification Checklist
- [x] **Training Pipeline**: Verified that `pixityAI_workflow.py` still generates events and labels correctly.
- [x] **Resampling**: Verified that session-aware resampling is preserved in the batch path.
- [x] **Feature Parity**: Batch functions handle both `DatetimeIndex` and `timestamp` columns to ensure compatibility between DuckDB results and resampled DataFrames.
- [x] **Signal Enrichement**: Signals passed to the runner now contain `quantity`, `sl`, and `tp` in their metadata.

## Usage
To trigger the new batch backtest path, select the **pixityAI_meta** strategy in the Backtest UI. The runner will automatically detect the strategy and use the synchronized vectorized path.
