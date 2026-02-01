# Backtest Walkthrough — Complete Step-by-Step Guide

**Example:** MARICO (NSE_EQ|INE196A01026), 5 trading days (Jan 1–7, 2025), EHMA Pivot strategy

---

## The Data

| Day | Date       | Bars | Open   | High   | Low    | Close  |
|-----|------------|------|--------|--------|--------|--------|
| 1   | 2025-01-01 | 375  | 636.00 | 648.20 | 634.05 | 641.00 |
| 2   | 2025-01-02 | 375  | 635.30 | 656.00 | 635.30 | 647.05 |
| 3   | 2025-01-03 | 375  | 648.25 | 663.65 | 647.65 | 651.50 |
| 4   | 2025-01-06 | 375  | 634.15 | 675.05 | 633.15 | 646.65 |
| 5   | 2025-01-07 | 375  | 639.95 | 654.75 | 638.65 | 648.25 |

**Total: 1,875 one-minute bars** (375 bars/day × 5 days, 9:15 AM–3:29 PM IST)

---

## Phase 1: User Submits Backtest (Flask UI)

The user opens `/backtest/` in the browser and fills the form:

```
Strategy:  ehma_pivot
Symbol:    MARICO (instrument_key: NSE_EQ|INE196A01026)
Days:      5
Params:    {"ema_period": 20}
```

Clicks **Run Backtest**.

### What happens in the backend

**File:** `flask_app/blueprints/backtest.py` → `run_backtest()`

```
1. Generate run_id = UUID (e.g., "a3f7b2c1-...")
2. Store in-memory: _running_backtests[run_id] = {status: 'RUNNING', progress: 'Starting...'}
3. Spawn background thread → _execute_backtest(run_id, 'ehma_pivot', 'MARICO', 5, {"ema_period": 20})
4. Return JSON: {run_id: "a3f7b2c1-...", status: "RUNNING"}
```

The browser starts polling `GET /backtest/api/status/a3f7b2c1-...` every 2 seconds.

---

## Phase 2: Pipeline Assembly (inside background thread)

`_execute_backtest()` builds the entire trading system from scratch:

### 2a. Resolve Symbol
```
"MARICO" → DB lookup → instrument_key = "NSE_EQ|INE196A01026"
```

### 2b. Calculate Date Range
```
end_time   = now()  (2025-01-07 15:30:00)
start_time = now() - 5 days = 2025-01-02 09:15:00
(actual: the code uses the days parameter to compute the window)
```

### 2c. Create ReplayClock
```python
clock = ReplayClock(initial_time=start_time)
# clock.now() → 2025-01-01 09:15:00
# Clock does NOT tick by itself — only the data provider advances it
```

### 2d. Create HistoricalMarketDataProvider
```python
provider = HistoricalMarketDataProvider(
    symbols=["NSE_EQ|INE196A01026"],
    start_time=start_time,
    end_time=end_time,
    clock=clock,
    db_path="data/trading_bot.duckdb"
)
```

**Immediately on creation**, it runs `_load_all_data()`:
```sql
SELECT timestamp, open, high, low, close, volume
FROM ohlcv_1m
WHERE instrument_key = 'NSE_EQ|INE196A01026'
  AND timestamp >= '2025-01-01 09:15:00'
  AND timestamp <= '2025-01-07 15:30:00'
ORDER BY timestamp ASC
```

Result: **1,875 OHLCVBar objects** loaded into memory. Index pointer starts at 0.

### 2e. Create Analytics Provider
```python
analytics = HistoricalAnalyticsProvider(symbol, start_time, end_time, db_path)
```
For `ehma_pivot`, analytics are **not used** (strategy ignores `context.analytics_snapshot`). But the provider still preloads from `confluence_insights` and `regime_insights` tables. Since these are empty → snapshots will be `None`.

### 2f. Create Execution Handler
```python
exec_config = ExecutionConfig(
    mode=ExecutionMode.PAPER,          # Simulated fills
    default_quantity=100,
    max_trades_per_day=999999,         # Effectively unlimited for backtest
    max_drawdown_limit=999.0,          # Disabled for backtest
)
execution = ExecutionHandler(clock=clock, broker=paper_broker, config=exec_config)
execution._kill_switch_disabled = True  # Skip STOP file + drawdown checks
```

The PaperBroker always returns `FILLED` for every order — simulates instant execution.

### 2g. Create Strategy
```python
from core.strategies.registry import STRATEGY_REGISTRY
strategy_class = STRATEGY_REGISTRY['ehma_pivot']  # → EHMAPivotStrategy
strategy = EHMAPivotStrategy(strategy_id='ehma_pivot', config={"ema_period": 20})
```

Strategy internal state:
```
ema_period = 20
_previous_ema = None      (needs first bar to initialize)
_previous_price_above_ema = None
```

### 2h. Create Recorder & Writer
```python
recorder = BackfillRecorder(strategy_id='ehma_pivot', symbol='NSE_EQ|INE196A01026')
writer = BackfillWriter(base_dir="backfills")
```

### 2i. Create BackfillSpec (metadata)
```python
spec = BackfillSpec(
    strategy_id='ehma_pivot',
    symbol='NSE_EQ|INE196A01026',
    start_date=start_time,
    end_date=end_time,
    execution_mode='PAPER',
    strategy_params={"ema_period": 20}
)
```

### 2j. Assemble TradingRunner
```python
runner = TradingRunner(
    config=RunnerConfig(symbols=["NSE_EQ|INE196A01026"], strategy_ids=["ehma_pivot"]),
    market_data_provider=provider,
    analytics_provider=analytics,
    strategies=[strategy],
    execution_handler=execution,
    position_tracker=position_tracker,
    clock=clock
)
```

**Everything is wired. Ready to run.**

---

## Phase 3: The Main Loop (`runner.run()`)

The runner enters its main `while _is_running:` loop. Here's what happens bar by bar:

### Iteration Flow (repeated 1,875 times)

```
┌─────────────────────────────────────────────────────────┐
│  runner.run() main loop                             │
│                                                     │
│  1. provider.get_next_bar("NSE_EQ|INE196A01026")    │
│     → Returns OHLCVBar #N                           │
│     → clock.advance_to(bar.timestamp)               │
│                                                     │
│  2. analytics.get_latest_snapshot(symbol)            │
│     → None (tables empty for ehma_pivot)            │
│                                                     │
│  3. position_tracker.get_position_quantity(symbol)   │
│     → 0 (no position yet)                           │
│                                                     │
│  4. Build StrategyContext:                           │
│     {symbol, current_position, analytics_snapshot,   │
│      market_regime, strategy_params}                 │
│                                                     │
│  5. strategy.process_bar(bar, context)              │
│     → signal OR None                                │
│                                                     │
│  6. If signal:                                      │
│     a. recorder.record_signal(signal)               │
│     b. execution.process_signal(signal, bar.close)  │
│        → trade OR None                              │
│     c. position_tracker.apply_trade(trade)          │
│     d. recorder.record_trade(trade)                 │
│                                                     │
│  7. bars_processed += 1                             │
│  8. Loop → next bar                                 │
└─────────────────────────────────────────────────────────┘
```

### Concrete Example: First 25 Bars (Warmup)

The EHMA Pivot strategy uses a 20-period EMA. Here's what happens:

**Bar 1** (9:15, close=640.95):
```
EMA calculation: _previous_ema is None → set _previous_ema = 640.95
Return: None (warming up, can't determine crossover yet)
```

**Bar 2** (9:16, close=640.25):
```
k = 2 / (20+1) = 0.0952
EMA = (640.25 × 0.0952) + (640.95 × 0.9048) = 640.88
price_above_ema? 640.25 > 640.88 → False
_previous_price_above_ema was None → set to False
Return: None (need previous state to detect crossover)
```

**Bar 3–20** (9:17–9:34):
```
EMA continues adjusting. No crossover signals because:
- Price stays close to EMA during first 20 bars
- EMA needs ~20 bars to stabilize
- Each bar: compare price vs EMA, check for crossover
```

### Concrete Example: A BUY Signal

Let's say at **Bar 47** (9:41 AM, Jan 1):
```
Previous state: price was BELOW EMA
Current bar: close = 645.50, EMA = 644.80
price_above_ema = True (645.50 > 644.80)
Crossover detected! Price crossed from below to above EMA.
current_position = 0 (no existing position)

→ Generate SignalEvent:
  {
    strategy_id: "ehma_pivot",
    symbol: "NSE_EQ|INE196A01026",
    timestamp: 2025-01-01 09:41:00,
    signal_type: BUY,
    confidence: 0.6,
    metadata: {reason: "EMA crossover UP", ema: 644.80, close: 645.50}
  }
```

### Signal → Trade Execution

The signal is passed to `ExecutionHandler.process_signal()`:

```
1. Kill switch check → disabled (_kill_switch_disabled = True) → PASS
2. Daily trade limit → 999999 → PASS
3. Drawdown check → disabled → PASS
4. Validate signal format → OK
5. Calculate position size:
   quantity = default_qty × (0.5 + confidence × 0.5)
            = 100 × (0.5 + 0.6 × 0.5)
            = 100 × 0.8 = 80 shares
6. Apply slippage:
   BUY slippage = price × (1 + 0.01) = 645.50 × 1.01 = 651.955
7. Create OrderEvent: BUY 80 shares @ 651.955
8. PaperBroker.place_order() → FILLED (always fills in paper mode)
9. Create TradeEvent:
   {
     trade_id: "T-001",
     symbol: "NSE_EQ|INE196A01026",
     direction: "BUY",
     quantity: 80,
     price: 651.955,
     fees: 0.0,
     status: FILLED
   }
10. Position tracker: position = +80 shares
```

### Concrete Example: An EXIT Signal

Later, at **Bar 312** (let's say 3:05 PM, Jan 2):
```
Previous state: price was ABOVE EMA
Current bar: close = 646.20, EMA = 647.10
price_above_ema = False (646.20 < 647.10)
Crossover detected! Price crossed from above to below EMA.
current_position = 80 (we have a long position)

→ Generate SignalEvent:
  {
    signal_type: EXIT,
    confidence: 0.7,
    metadata: {reason: "EMA crossover DOWN", ema: 647.10, close: 646.20}
  }
```

Execution:
```
quantity = 80 × (0.5 + 0.7 × 0.5) = 80 × 0.85 = 68 shares
SELL slippage = 646.20 × (1 - 0.01) = 639.738
TradeEvent: SELL 68 shares @ 639.738
Position: 80 - 68 = 12 shares remaining
```

### Loop Termination

After all 1,875 bars are consumed:
```
provider.get_next_bar() → None (index 1875 >= len 1875)
provider.is_data_available() → False
Runner detects: no bars processed this iteration + not streaming → BREAK
```

`runner.run()` returns:
```python
{
    "bars_processed": 1875,
    "signals_generated": 14,      # hypothetical
    "trades_executed": 10,         # hypothetical
    "strategies_disabled": 0
}
```

---

## Phase 4: Post-Backtest Processing

### 4a. Collect Results

```python
trades = execution.get_trade_history()  # List of all TradeEvents
signals = recorder.get_signal_data()    # List of all SignalEvents as dicts
trade_data = recorder.get_trade_data()  # Trades formatted for CSV
```

### 4b. Calculate Metrics

```python
summary = {
    "bars_processed": 1875,
    "signals_generated": 14,
    "trades_executed": 10,
    "total_fees": 0.0,
    "final_equity": 100243.50,     # Starting 100k + P&L
    "max_drawdown": 0.023,          # 2.3%
    "win_rate": 0.60,               # 60% profitable trades
    "sharpe_ratio": 1.45
}
```

### 4c. Write Artifacts to Disk

`BackfillWriter.write_artifacts()` creates:

```
backfills/
└── ehma_pivot/
    └── NSE_EQ_INE196A01026/           (pipe sanitized to underscore)
        └── 20250101_20250107_a3f7b2/
            ├── spec.json               # Run configuration
            ├── signals.csv             # All 14 signals
            ├── trades.csv              # All 10 trades
            ├── summary.json            # Metrics snapshot
            └── equity.csv              # Equity curve points
```

**spec.json:**
```json
{
  "strategy_id": "ehma_pivot",
  "symbol": "NSE_EQ|INE196A01026",
  "start_date": "2025-01-01T09:15:00",
  "end_date": "2025-01-07T15:30:00",
  "execution_mode": "PAPER",
  "strategy_params": {"ema_period": 20}
}
```

**equity.csv** (simplified):
```csv
timestamp,equity
2025-01-01T09:41:00,47843.60     # After BUY: 100k - (80 × 651.955)
2025-01-02T15:05:00,91345.38     # After partial SELL: + (68 × 639.738)
...
```

### 4d. Persist to Database

`persist_backtest_run()` inserts into three DuckDB tables:

**backtest_runs** (1 row):
```sql
INSERT INTO backtest_runs (
  run_id, strategy_id, symbol, start_date, end_date,
  strategy_params, status, bars_processed, signals_generated,
  trades_executed, total_fees, final_equity, max_drawdown,
  win_rate, sharpe_ratio, completed_at
) VALUES (
  'a3f7b2c1-...', 'ehma_pivot', 'NSE_EQ|INE196A01026',
  '2025-01-01', '2025-01-07',
  '{"ema_period": 20}', 'COMPLETED', 1875, 14,
  10, 0.0, 100243.50, 0.023,
  0.60, 1.45, NOW()
);
```

**backtest_trades** (10 rows):
```sql
INSERT INTO backtest_trades (trade_id, run_id, timestamp, direction, quantity, price, fees, status)
VALUES ('T-001', 'a3f7b2c1-...', '2025-01-01 09:41:00', 'BUY', 80, 651.955, 0.0, 'FILLED');
-- ... 9 more trades
```

**backtest_equity** (10 rows — one per trade):
```sql
INSERT INTO backtest_equity (run_id, timestamp, equity)
VALUES ('a3f7b2c1-...', '2025-01-01 09:41:00', 47843.60);
-- ... equity after each trade
```

### 4e. Update In-Memory Status

```python
_running_backtests[run_id] = {
    'status': 'COMPLETED',
    'progress': 'Done',
    'db_run_id': 'a3f7b2c1-...'
}
```

---

## Phase 5: Results Display

### 5a. Polling Completes

The browser's `pollStatus()` receives:
```json
{"status": "COMPLETED", "db_run_id": "a3f7b2c1-..."}
```

JS stops the polling interval and redirects to `/backtest/a3f7b2c1-...`

### 5b. Detail Page Load

Flask calls `BacktestFacade`:

```python
facade.get_run_detail("a3f7b2c1-...")   # → run metadata + metrics
facade.get_trades("a3f7b2c1-...")       # → 10 trade rows
facade.get_equity_curve("a3f7b2c1-...")  # → equity time series
```

### 5c. Rendered Page Shows

- **Summary cards:** 1,875 bars processed, 14 signals, 10 trades, 60% win rate, $243.50 profit
- **Equity curve chart** (Chart.js line chart)
- **Trades table:** each trade with timestamp, direction, quantity, price, fees

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                     FLASK (Browser)                          │
│  POST /backtest/run  →  poll status  →  GET /backtest/<id>   │
└────────────┬─────────────────────────────────────┬───────────┘
             │ (background thread)             │ (read)
             ▼                                 ▼
┌────────────────────────┐        ┌──────────────────────┐
│  _execute_backtest()   │        │  BacktestFacade       │
│                        │        │  - list_runs()        │
│  1. Assemble pipeline  │        │  - get_run_detail()   │
│  2. runner.run()       │        │  - get_trades()       │
│  3. Write artifacts    │        │  - get_equity_curve() │
└────────┬───────────────┘        └──────────┬───────────┘
         │                                   │
         ▼                                   │
┌──────────────────────────────┐             │
│  TradingRunner.run()         │             │
│                              │             │
│  ┌─────────────────────┐     │             │
│  │ MarketDataProvider   │     │             │
│  │ get_next_bar()       │◄──── DB (ohlcv_1m)
│  │ → advances clock     │     │             │
│  └─────────┬───────────┘     │             │
│            ▼                 │             │
│  ┌─────────────────────┐     │             │
│  │ Strategy             │     │             │
│  │ process_bar()        │     │             │
│  │ → signal or None     │     │             │
│  └─────────┬───────────┘     │             │
│            ▼                 │             │
│  ┌─────────────────────┐     │             │
│  │ ExecutionHandler     │     │             │
│  │ process_signal()     │     │             │
│  │ → trade or None      │     │             │
│  └─────────┬───────────┘     │             │
│            ▼                 │             │
│  ┌─────────────────────┐     │             │
│  │ BackfillRecorder     │     │             │
│  │ record_signal/trade  │     │             │
│  └─────────────────────┘     │             │
└──────────────┬───────────────┘             │
               ▼                             │
┌──────────────────────────────┐             │
│  BackfillWriter              │             │
│  write_artifacts()           │             │
│  ├── spec.json               │             │
│  ├── signals.csv             │             │
│  ├── trades.csv              │             │
│  ├── summary.json            │             │
│  ├── equity.csv              │             │
│  └── persist_backtest_run() ─┼─────► DuckDB │
│      INSERT backtest_runs    │      ◄──────┘
│      INSERT backtest_trades  │
│      INSERT backtest_equity  │
└──────────────────────────────┘
```

---

## Key Design Decisions

| Decision | Why |
|----------|-----|
| **Single-threaded runner** | Deterministic results — same input always produces same output |
| **Clock owned by data provider** | Time only advances when a bar arrives — no wall-clock dependency |
| **Strategies are pure functions** | `bar + context → signal` — no side effects, easy to test |
| **Paper broker always fills** | Simplifies backtesting — no partial fills or order book simulation |
| **Dual persistence (files + DB)** | Files for offline analysis, DB for dashboard queries |
| **Kill switches disabled in backtest** | STOP file, drawdown limits, daily trade caps would interfere with historical simulation |
| **Analytics as optional pre-step** | Self-contained strategies (ehma_pivot) skip it; dependent strategies (confluence_consumer) auto-compute before running |

---

## Strategies and Analytics Dependency

| Strategy | Needs Analytics? | What it Uses |
|----------|-----------------|--------------|
| ehma_pivot | No | Pure price EMA crossover |
| confluence_consumer | Yes | confluence_insights (RSI+MACD bias/confidence) |
| regime_adaptive | Yes | regime_insights (trend/volatility regime) |
| daily_regime_v2 | Yes | Both tables |

For analytics-dependent strategies, the backtest pipeline adds an extra step before Phase 3:
```
Phase 2.5: populate_analytics(symbol, start, end)
  → Run ConfluenceEngine over all bars → save to confluence_insights
  → Run RegimeAnalyticsEngine over all bars → save to regime_insights
  → Now HistoricalAnalyticsProvider can preload these snapshots
```
