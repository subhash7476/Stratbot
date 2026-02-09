# PixityAI Strategy Implementation Details

The PixityAI strategy has been successfully integrated into the codebase with the following components:

## Core Files Created
1.  **`core/strategies/pixityAI_event_generator.py`**: 
    - Implements logic for **Trend** (VWAP + EMA + Swing Breakout) and **Reversion** (ADX + VWAP deviation) triggers.
    - Captures a feature snapshot (6 context features) for every candidate trade.
2.  **`core/analytics/pixityAI_labeler.py`**:
    - Implements **Triple-Barrier Labeling**.
    - Outputs labels: `+1` (Target Hit), `-1` (Stop Hit), `0` (Time Stop).
3.  **`core/strategies/pixityAI_meta_strategy.py`**:
    - The production strategy class that wraps the event generator.
    - Loads the trained `pixityAI_meta_filter.joblib` to filter signals.
4.  **`core/execution/pixityAI_risk_engine.py`**:
    - Handles position sizing based on fixed risk per trade.
    - Calculates bracket order prices (SL/TP) using ATR.
    - Includes 0.025% STT calculation for SELL fills.
5.  **`scripts/pixityAI_trainer.py`**:
    - A utility to train the Meta-Model filter using `scikit-learn`.
6.  **`scripts/pixityAI_backtest_runner.py`**:
    - A dedicated backtest script simulating realistic fills, costs, and conservative intrabar logic.
7.  **`core/models/pixityAI_config.json`**:
    - Centralized configuration for strategy parameters, risk limits, and model paths.

## Key Logic Features
- **Conservative Fill Assumption**: If both Stop Loss and Take Profit are hit within the same candle during backtesting, the logic records a loss to prevent over-optimistic results.
- **Dynamic Sizing**: Uses `Risk_Amount / ATR_Distance` to ensure every trade has the same dollar-risk regardless of symbol volatility.
- **Meta-Filtering**: Moves beyond raw signals by requiring a high probability of success from the ML model before execution.

## Usage
To start using the strategy:
1.  Collect historical events using the generator.
2.  Label them using the labeler against your historical bar data.
3.  Run the trainer script to generate `pixityAI_meta_filter.joblib`.
4.  Configure `pixityAI_meta` in your strategy registry and run.
