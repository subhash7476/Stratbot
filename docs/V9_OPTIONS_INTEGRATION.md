# V9 PM Scalper: Options Integration & Live Execution

## Overview
The `v9_pm_scalper` strategy has been upgraded from a futures-proxy simulation (0.04% fixed cost) to a high-fidelity options execution engine. It now selects real-world Nifty weekly option contracts and fetches live premiums from the Upstox REST API.

## Key Components

### 1. Upstox Market Data Provider (`core/brokers/upstox_market_data.py`)
A specialized REST client for fetching Last Traded Price (LTP) for NFO instruments.
- **Endpoint**: `/v2/market-quote/quotes`
- **Authentication**: Uses existing project-wide Upstox OAuth credentials.
- **Symbol Mapping**: Handles the conversion between internal `|` separators and Upstox `:` separators.

### 2. Strategy Logic (`core/strategies/v9_pm_scalper_strategy.py`)
The strategy state machine has been enhanced with option-specific logic:
- **Contract Selection**: At 13:02 IST, when a signal fires, the `OptionsContractSelector` picks the nearest ATM Weekly Call.
- **Premium Fetching**:
    - **Entry**: Fetches live LTP at 13:02 IST. If fetching fails, the entry is skipped to prevent stale pricing.
    - **Exit**: Fetches live LTP at the moment of Stop Loss hit or Time Exit (14:45 IST).
- **Realistic PnL Calculation**:
    - `pnl_rs = (exit_premium - entry_premium) * lot_size - costs`
    - **Costs**: Rs 40 round-trip brokerage + 0.125% STT on the exit value.

### 3. Database Schema & Migration (`core/database/schema.py`)
The `v9_paper_trades` table has been extended to capture the granular details of the option trade:
- `option_symbol`: The specific contract traded (e.g., `NIFTY26FEB2622000CE`).
- `entry_premium`: The buy price.
- `exit_premium`: The sell price.
- `lot_size`: Standardized lot size (default 75 for Nifty).
- `pnl_rs`: Net profit/loss in Rupees.

**Auto-Migration**: The strategy now includes `ALTER TABLE` logic to automatically update existing databases without data loss.

### 4. Flask Integration (`scripts/unified_runner.py`)
The `V9PMRunner` instance is now attached directly to the Flask `app` object (`app.v9_runner`).
- **Dashboard Access**: Allows Flask routes and blueprints to access the live strategy state, state machine status, and session metrics.
- **Unified Lifecycle**: Managed via the same `stop_event` as the ingestor and rollover scheduler.

## Operational Workflow

1. **Signal Generation**: `DayTypeEngine` computes features at 13:00 IST.
2. **Contract selection**: If `BullTrend` confidence ≥ 75%, an ATM Call is selected.
3. **Entry**: Live premium is recorded at 13:02 IST.
4. **Monitoring**: Underlying Nifty price is monitored for a 0.30% Stop Loss.
5. **Exit**: At stop-hit or 14:45 IST, exit premium is fetched and net PnL is persisted.

## Verification
- **LTP Logs**: Strategy logs now include `(Option: SYMBOL @ PREMIUM)` on entry and exit.
- **DB Check**: 
  ```sql
  SELECT option_symbol, entry_premium, exit_premium, pnl_rs 
  FROM v9_paper_trades 
  WHERE pnl_rs IS NOT NULL;
  ```
