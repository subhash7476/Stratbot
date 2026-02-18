# Implementation Plan — Self-Service Training & Backtest UI

## Problem
User must depend on Claude to test SL/TP combinations and train models. The plumbing is broken:
- Risk engine has **hardcoded** SL=2×ATR, TP=4×ATR (ignores config)
- UI collects `tp_pct`/`sl_pct` as percentages but they're **silently ignored**
- Config files declare `sl_mult`/`tp_mult` but **nobody reads them**
- Training script has **no CLI args** and different SL/TP than execution
- No web endpoint for model training

## Phase 1: Fix PixityAIRiskEngine (core plumbing)

**File: `core/execution/pixityAI_risk_engine.py`**

- Add `sl_mult` and `tp_mult` to constructor (defaults: 1.0, 2.0)
- Replace hardcoded `sl_distance = 2.0 * atr` → `self.sl_mult * atr`
- Replace hardcoded `tp_distance = 4.0 * atr` → `self.tp_mult * atr`

## Phase 2: Wire SL/TP through BacktestRunner

**File: `core/backtest/runner.py`**

- In `_run_pixityAI_batch()`, extract `sl_mult`/`tp_mult` from `strategy_params` (UI override) falling back to `pixity_config` (JSON file) falling back to defaults
- Pass them to `PixityAIRiskEngine(sl_mult=..., tp_mult=...)`

## Phase 3: Fix UI inputs + API params

**File: `flask_app/templates/backtest/index.html`**
- Change "TP %" / "SL %" inputs → "TP (ATR Mult)" / "SL (ATR Mult)" with sensible defaults (1.0, 2.0)
- Change JS to send `sl_mult`/`tp_mult` instead of `sl_pct`/`tp_pct`
- Add Model Selection dropdown to Single Run form

**File: `flask_app/blueprints/backtest.py`**
- Change `run_backtest()` to extract `sl_mult`/`tp_mult` instead of `tp_pct`/`sl_pct`
- Add `model_path` and `skip_meta_model` to strategy_params

## Phase 4: Refactor training script to be importable

**File: `scripts/train_global_model.py`**
- Add parameters to `train_global_model()`: symbols, train_start, train_end, sl_mult, tp_mult, timeframe, model_save_path, progress_callback
- Add parameters to `simulate_trade_outcome()`: sl_mult, tp_mult (replace hardcoded 1.0/2.0)
- Add `argparse` CLI support in `__main__` block
- Return result dict with model_path, stats, cv_scores

## Phase 5: Add Model Trainer tab + API endpoints

**File: `flask_app/blueprints/backtest.py`** — 3 new endpoints:
- `POST /backtest/api/trainer/start` — launch training in background thread
- `GET /backtest/api/trainer/progress/<id>` — poll progress
- `GET /backtest/api/trainer/models` — list .joblib files in core/models/

**File: `flask_app/templates/backtest/index.html`** — new tab:
- Training config form (dates, SL/TP, timeframe, symbol scope)
- Progress bar with polling
- Trained models table with "Use in Backtest" action
- Auto-populates model dropdown in Single Run tab

## Phase 6: Fix validate_single.py

**File: `scripts/validate_single.py`**
- Wire existing `--sl`/`--tp` args into `strategy_params` as `sl_mult`/`tp_mult`

## Verification
1. Run backtest from UI with SL=1.5, TP=3.0 → verify trade SL/TP in results match
2. Train model from UI → verify .joblib file created
3. Run backtest with trained model selected → verify model is loaded
4. Run `validate_single.py --sl 1.5 --tp 3.0` → verify CLI args respected
