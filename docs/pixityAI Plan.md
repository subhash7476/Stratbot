# PixityAI Strategy Plan

## Overview
The PixityAI strategy is an advanced intraday trading framework for NSE equities. It utilizes a two-stage signal process: a base event generator for identifying candidates and a machine learning meta-model for final trade filtering.

## 1. Research Phase
- **Universe Selection:** Top 100-200 liquid NSE equities based on 20-day median turnover.
- **Data Gathering:** Fetch 1-minute or 5-minute historical bars.
- **Event Generation:** Run the `pixityAI_event_generator.py` on historical data to identify Trend and Reversion candidates.
- **Labeling:** Use `pixityAI_labeler.py` to apply Triple-Barrier labeling (Profit, Loss, and Time barriers) to the generated events.

## 2. Model Development
- **Feature Engineering:** Extract context features (VWAP distance, EMA slopes, ATR%, ADX, time of day) at the moment of each event.
- **Meta-Model Training:** Train a `RandomForestClassifier` using `scripts/pixityAI_trainer.py`.
- **Validation:** Use purged cross-validation to ensure no look-ahead bias or time-leakage.

## 3. Production Workflow
- **Live Scanning:** The `PixityAIMetaStrategy` runs in real-time.
- **Candidate Detection:** identifies potential Trend/Reversion trades.
- **ML Filtering:** Only trades with a probability > 60% (configurable) are executed.
- **Execution:** Automated bracket orders with strict ATR-based SL/TP and 0.025% STT calculation on sells.

## 4. Risk Management
- **Fixed Risk per Trade:** Position sizing based on a fixed loss amount (e.g., 500 INR).
- **Intraday Flattening:** All positions closed by 15:10 IST.
- **Conservative Backtesting:** Assuming SL hits before TP if both occur in the same bar.
