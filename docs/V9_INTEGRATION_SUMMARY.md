# V9 PM Scalper Strategy Integration Summary

The V9 PM Scalper strategy has been fully integrated into the core trading system and Flask application, moving from a standalone script (`run_v9_paper.py`) to a modular, database-driven architecture consistent with the "Stock Daytrade" strategy.

## Changes Implemented

### 1. Database Schema
- Added `v9_paper_signals` and `v9_paper_trades` tables to `core/database/schema.py`.
- This enables persistent storage in SQLite (`trading.db`), allowing for detailed auditing and better integration with the Flask UI.

### 2. Strategy & Runner
- **`core/strategies/v9_pm_scalper_strategy.py`**: Encapsulated the V9 logic into a class-based `V9PMScalperStrategy`. It manages signal checkpoints (13:00 IST), entries (13:02 IST), and exits (Stop Loss or 14:45 IST).
- **`scripts/v9_pm_runner.py`**: A new background runner that polls the live DuckDB candle buffer and feeds bars to the strategy in real-time.

### 3. System Integration
- **`scripts/unified_runner.py`**: Modified to launch the `V9PMTradingThread` on startup. The strategy now runs automatically alongside the market ingestor and Flask server.
- **`flask_app/blueprints/scanner.py`**: Updated the "Nifty PM" blueprint to read trade history and session state directly from SQLite instead of the CSV log.

### 4. Data Migration
- Created `scripts/migrate_v9_to_db.py` to import historical trades from `logs/v9_paper_trades.csv` into the new database tables. This script was successfully executed.

## Benefits
- **Real-time Monitoring**: The Flask UI now reflects the live state of the strategy as bars are ingested.
- **Improved Robustness**: By using SQLite and the centralized `DatabaseManager`, the strategy is less prone to file-locking issues and data corruption.
- **Unified Pipeline**: V9 now follows the same architectural principles as the rest of the platform (Strategies stay dumb, Execution owns reality).

## Usage
- The strategy starts automatically when running `python scripts/unified_runner.py`.
- Monitoring is available on the **Nifty PM** page of the Dashboard.
- Historical logs are preserved in the `v9_paper_trades` table of `data/trading.db`.
