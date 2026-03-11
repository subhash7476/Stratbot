# Data Pipeline

**Project:** PixityAI Trading Bot
**Last Updated:** 2026-02-22

---

## Table of Contents

1. [Overview](#1-overview)
2. [Data Sources](#2-data-sources)
3. [Storage Architecture](#3-storage-architecture)
4. [DuckDB Market Data](#4-duckdb-market-data)
5. [SQLite Operational Databases](#5-sqlite-operational-databases)
6. [Data Flow: Live Trading](#6-data-flow-live-trading)
7. [Data Flow: Backtesting](#7-data-flow-backtesting)
8. [DatabaseManager](#8-databasemanager)
9. [Schema Reference](#9-schema-reference)
10. [Data Commands](#10-data-commands)

---

## 1. Overview

The pipeline has two regimes:

**Live mode:** Upstox WebSocket → market_ingestor.py → DuckDB live buffer → TradingRunner (read-only)

**Backtest mode:** Historical DuckDB files → ReplayClock → TradingRunner (read-only)

In both modes, the TradingRunner and strategies are **read-only consumers** of market data. Only the ingestor writes to market data files.

---

## 2. Data Sources

### 2.1 Upstox V2 API

| Feed | Type | Used For |
|------|------|----------|
| WebSocket market feed | Protobuf binary | Live 1m candle construction |
| REST API (historical) | JSON | Backfill up to 2 years per symbol |
| REST API (order management) | JSON | Order placement, status |
| REST API (portfolio) | JSON | Position reconciliation |

**Authentication:** OAuth2 flow. Access token stored in `config/credentials.json`. Refreshed daily via `scripts/upstox_auth.py`.

**Exchanges configured:**
- `NSE` — Equity
- `NFO` — F&O (futures and options)
- `MCX` — Commodities
- `BSE` — BSE equity
- `BFO` — BSE F&O
- `CDS` — Currency derivatives

### 2.2 Symbol Mapping

NSE symbols must be mapped to Upstox instrument tokens. The mapping is maintained in `core/database/utils/` and used by all data fetch scripts.

F&O universe: ~100–150 active scrips (filtered from NSE F&O permitted list by liquidity).

---

## 3. Storage Architecture

```
data/
├── market_data/                    # DuckDB — OHLCV candles
│   ├── nse/
│   │   ├── candles/
│   │   │   ├── 1m/                 # 1-minute bars
│   │   │   │   ├── 2023-01-02.duckdb
│   │   │   │   ├── 2023-01-03.duckdb
│   │   │   │   └── ... (~750 files as of 2026-02)
│   │   │   ├── 5m/                 # 5-minute bars
│   │   │   ├── 15min/              # 15-minute bars
│   │   │   └── 1d/                 # Daily bars
│   │   └── ticks/
│   │       └── {date}.duckdb
│   ├── mcx/
│   └── bse/
│
├── live_buffer/                    # Today's live data (DuckDB)
│   ├── ticks.duckdb
│   └── candles.duckdb
│
├── trading/
│   └── trading.db                  # SQLite — orders, trades, positions
│
├── signals/
│   └── signals.db                  # SQLite — confluence insights, regime, signals
│
├── config.db                       # SQLite — users, roles, settings
│
├── features/
│   └── day_type/
│       ├── nifty_day_features_2023.csv
│       ├── nifty_day_features_2024.csv
│       ├── nifty_day_features_2025.csv
│       ├── nifty_day_features_2026.csv
│       └── pm_expectancy_raw.csv   # v9 model predictions + PM metrics
│
├── backtest/
│   ├── runs/                       # Per-run artifacts
│   └── backtest_index.db           # SQLite index of all runs
│
└── persistence/                    # Serialized in-memory state artifacts
```

**Total data volume (approximate):**
- 1m candles: ~3 years × 375 bars/day × 150 symbols ≈ 60M rows across ~750 DuckDB files
- Daily candles: ~3 years × 150 symbols ≈ 100K rows
- Nifty day features: ~750 rows × 53 columns

---

## 4. DuckDB Market Data

### 4.1 Partitioning Strategy

Each trading date gets its own DuckDB file: `{date}.duckdb`

**Why per-date partitioning?**
- Each day is immutable once market closes; never re-written
- Fast date-range queries by file selection
- Easy backfill (add missing dates without touching existing files)
- Small file sizes enable memory-mapped reads
- Parallel ingest per date

### 4.2 Candle File Schema

```sql
-- Each date file: data/market_data/nse/candles/1m/2025-01-15.duckdb

CREATE TABLE candles (
    symbol      VARCHAR     NOT NULL,
    timeframe   VARCHAR     NOT NULL,   -- "1m", "5m", "15m", "1h", "1d"
    timestamp   TIMESTAMP   NOT NULL,
    open        DOUBLE      NOT NULL,
    high        DOUBLE      NOT NULL,
    low         DOUBLE      NOT NULL,
    close       DOUBLE      NOT NULL,
    volume      BIGINT      NOT NULL,
    is_synthetic BOOLEAN    DEFAULT FALSE,
    PRIMARY KEY (symbol, timeframe, timestamp)
);
```

`is_synthetic`: TRUE if the bar was constructed by aggregating finer bars rather than received directly from the exchange.

### 4.3 Tick File Schema

```sql
-- data/market_data/nse/ticks/2025-01-15.duckdb

CREATE TABLE ticks (
    symbol      VARCHAR     NOT NULL,
    timestamp   TIMESTAMP   NOT NULL,
    price       DOUBLE      NOT NULL,
    volume      BIGINT      NOT NULL,
    bid         DOUBLE,
    ask         DOUBLE,
    PRIMARY KEY (symbol, timestamp)
);
```

### 4.4 Backtest Query Pattern

```python
# Load 1m candles for a symbol over a date range
from core.database.manager import DatabaseManager
from datetime import date

dm = DatabaseManager(Path("data"))

# Each date is a separate file; iterate and union
for d in trading_days:
    with dm.historical_reader("nse", "candles", "1m", d) as conn:
        df = conn.execute(
            "SELECT * FROM candles WHERE symbol = ? ORDER BY timestamp",
            ["RELIANCE"]
        ).df()
```

### 4.5 Causal Guarantee

All backtest scripts load data by iterating dates forward. No future dates are ever loaded for a decision made on date T. This is enforced at the script level, not the database level.

---

## 5. SQLite Operational Databases

### 5.1 trading.db

Runtime execution state. Written by `ExecutionHandler`, read by Flask UI.

```sql
-- Orders
CREATE TABLE orders (
    order_id        TEXT PRIMARY KEY,
    signal_id       TEXT,
    timestamp       DATETIME    NOT NULL,
    symbol          TEXT        NOT NULL,
    order_type      TEXT        NOT NULL,   -- MARKET, LIMIT, SL, SL-M
    side            TEXT        NOT NULL,   -- BUY, SELL
    quantity        REAL        NOT NULL,
    price           REAL,
    status          TEXT        NOT NULL,   -- CREATED, SUBMITTED, OPEN, FILLED, REJECTED
    broker_order_id TEXT,
    metadata        TEXT                    -- JSON blob
);

-- Executed trades
CREATE TABLE trades (
    trade_id        TEXT PRIMARY KEY,
    signal_id       TEXT UNIQUE,
    strategy_id     TEXT,
    symbol          TEXT,
    timestamp       DATETIME,
    side            TEXT,                   -- BUY, SELL
    entry_price     DOUBLE,
    exit_price      DOUBLE,
    quantity        INTEGER,
    pnl             DOUBLE,
    fees            DOUBLE,
    metadata        TEXT                    -- JSON blob
);

-- Current positions
CREATE TABLE positions (
    symbol          TEXT PRIMARY KEY,
    quantity        REAL        NOT NULL DEFAULT 0.0,
    avg_entry_price REAL                 DEFAULT 0.0,
    realized_pnl    REAL                 DEFAULT 0.0,
    updated_at      DATETIME             DEFAULT CURRENT_TIMESTAMP
);
```

### 5.2 signals.db

Pre-computed analytics. Written by `update_analytics.py`, read by strategies at runtime.

```sql
-- Multi-indicator confluence signals
CREATE TABLE confluence_insights (
    timestamp       DATETIME,
    symbol          TEXT,
    bias            TEXT,       -- BULLISH, BEARISH, NEUTRAL
    confidence      DOUBLE,     -- 0.0-1.0
    agreement_level DOUBLE,     -- fraction of indicators agreeing
    indicator_states TEXT,      -- JSON: {"EMA": "BULL", "RSI": "BEAR", ...}
    insight_signal  TEXT,       -- BUY, SELL, NEUTRAL
    PRIMARY KEY (timestamp, symbol)
);

-- Market regime snapshots
CREATE TABLE regime_insights (
    insight_id          TEXT,
    symbol              TEXT,
    timestamp           DATETIME,
    regime              TEXT,   -- BULL_TREND, BEAR_TREND, RANGING, VOLATILE_RANGE
    momentum_bias       TEXT,   -- BULLISH, BEARISH, NEUTRAL
    trend_strength      DOUBLE, -- 0.0-1.0
    volatility_level    TEXT,   -- LOW, MEDIUM, HIGH, EXTREME
    persistence_score   DOUBLE,
    ma_fast             DOUBLE, -- EMA 20
    ma_medium           DOUBLE, -- EMA 50
    ma_slow             DOUBLE, -- EMA 200
    PRIMARY KEY (symbol, timestamp)
);

-- Raw strategy signals
CREATE TABLE signals (
    signal_id   TEXT PRIMARY KEY,
    strategy_id TEXT,
    symbol      TEXT,
    signal_type TEXT,           -- BUY, SELL, EXIT, NEUTRAL
    confidence  DOUBLE,
    bar_ts      DATETIME,
    status      TEXT,           -- PENDING, EXECUTED, REJECTED
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 5.3 config.db

System configuration, users, roles.

```sql
CREATE TABLE users (
    username        TEXT PRIMARY KEY,
    password_hash   TEXT,
    roles           TEXT,       -- Comma-separated
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE roles (
    role_name   TEXT PRIMARY KEY,
    permissions TEXT            -- Comma-separated
);
```

---

## 6. Data Flow: Live Trading

```
Upstox WebSocket (binary protobuf)
         │
         │ decode tick
         ▼
market_ingestor.py
         │
         ├──► Aggregate 1m candle from ticks
         │         (every 60s at :00)
         │
         ├──► Write tick to:
         │    data/live_buffer/ticks.duckdb
         │
         └──► Write 1m candle to:
              data/live_buffer/candles.duckdb
              data/market_data/nse/candles/1m/{today}.duckdb

              (EOD: today's file becomes historical)

                     │
                     │ (read-only after write)
                     ▼
              LiveDuckDBMarketDataProvider
                     │
                     ▼
              TradingRunner.run()
                     │
              ┌──────┴────────────────┐
              │                       │
              ▼                       ▼
     BaseStrategy.process_bar()   CachedAnalyticsProvider
              │                  (signals.db - pre-computed)
              │
              ▼
         SignalEvent
              │
              ▼
         ExecutionHandler
              │
              ├──► RiskManager.evaluate()
              │
              ├──► UpstoxAdapter.place_order()
              │
              └──► SQLite trading.db (orders, trades, positions)
```

---

## 7. Data Flow: Backtesting

```
data/market_data/nse/candles/1m/{date}.duckdb
         │
         │ preload entire date range
         ▼
DuckDBMarketDataProvider
         │ (in-memory pandas DataFrames, keyed by symbol)
         │
         ▼
ReplayClock (deterministic time)
         │
         ▼
TradingRunner.run()  ──► identical code path as live
         │
    [per-bar loop]
         │
         ▼
Strategy signals + PaperBroker fills
         │
         ▼
Backtest summary to:
data/backtest/runs/{run_id}/
    summary.json    — metrics, PnL, drawdown
    trades.csv      — all trades with entry/exit
    signals.csv     — all emitted signals
    positions.csv   — position updates
```

---

## 8. DatabaseManager

**File:** `core/database/manager.py`

Central connection broker. Enforces ownership rules: only the designated writer can write to each database.

```python
class DatabaseManager:
    def __init__(self, data_root: Path, read_only: bool = False)

    # Historical market data (DuckDB)
    @contextmanager
    def historical_reader(exchange, data_type, timeframe, dt) -> duckdb.Connection

    @contextmanager
    def market_data_writer() -> duckdb.Connection

    # Live buffer (DuckDB)
    @contextmanager
    def live_buffer_reader() -> Dict[str, duckdb.Connection]

    @contextmanager
    def live_buffer_writer() -> Dict[str, duckdb.Connection]

    # Operational databases (SQLite)
    @contextmanager
    def trading_reader() -> sqlite3.Connection

    @contextmanager
    def trading_writer() -> sqlite3.Connection

    @contextmanager
    def signals_reader() -> sqlite3.Connection

    @contextmanager
    def signals_writer() -> sqlite3.Connection

    @contextmanager
    def config_reader() -> sqlite3.Connection

    @contextmanager
    def config_writer() -> sqlite3.Connection
```

**Ownership rules:**

| Database | Writer | Readers |
|----------|--------|---------|
| Historical DuckDB | `market_ingestor.py` only | Strategies, Backtest, Flask |
| Live buffer DuckDB | `market_ingestor.py` only | TradingRunner |
| trading.db | `ExecutionHandler` only | Flask dashboard |
| signals.db | `update_analytics.py` only | Strategies |
| config.db | Admin scripts only | System |

**UNIFIED_MODE:** If `UNIFIED_MODE=1` env var set, allows read-write connections in same process (for Flask + trading in single process). Use with caution.

---

## 9. Schema Reference

### Backtest CSV Outputs

**trades.csv** (per backtest run):
```
trade_id, strategy_id, symbol, direction, entry_date, entry_price,
exit_date, exit_price, quantity, gross_pnl, fees, net_pnl,
exit_reason, r_multiple, bars_held
```

**signals.csv**:
```
signal_id, strategy_id, symbol, signal_type, confidence, bar_ts,
metadata_json
```

### Day Features CSV

**nifty_day_features_{YYYY}.csv** (53 columns):
```
date,
gap_pct, gap_dir, gap_size_pct60, prev_day_return, prev_day_range,
prev_day_clv, prev_day_slope, prev_day_vol_pct,
open_5m_ret, open_15m_ret, open_30m_ret, open_30m_range,
open_30m_range_ratio, open_30m_high_break_min, open_30m_low_break_min,
open_30m_twap_dist, open_30m_vol_ratio,
full_day_return, day_range_pct, clv, linreg_slope, linreg_r2,
hh_count_15m, ll_count_15m, max_twap_excursion,
realized_vol, intraday_atr_5m, range_pct_vs20d,
range_pct_before_11am, range_pct_after_130pm, largest_5m_candle,
log_vol_expansion, vol_clustering, center_of_mass_return_time,
pct_min_above_twap, pct_min_below_twap, twap_cross_count,
longest_above_twap, longest_below_twap, close_dist_twap, twap_dist_std,
flip_count_15m, inside_bar_pct_15m, median_body_pct_15m,
avg_adverse_excursion, max_adverse_excursion, overlap_ratio_15m,
dominant_direction_strength,
total_vol_pct20, first_hour_vol_pct, vol_skew_ampm,
vol_acceleration, vol_wtd_momentum
```
(Volume block G — last 5 features — is always 0 for Nifty index data)

---

## 10. Data Commands

### Initialize databases

```bash
python scripts/init_db.py
```

### Backfill historical data

```bash
python scripts/fetch_upstox_historical.py --symbols "CDSL,INFY,RELIANCE" --days 365
```

### Generate day features (Nifty 50, 1m → daily features)

```bash
python scripts/generate_day_features.py --start 2023-01-02 --end 2026-02-13
# Output: data/features/day_type/nifty_day_features_{YYYY}.csv
```

### Pre-compute offline analytics

```bash
python scripts/update_analytics.py --days 30
# Writes to signals.db
```

### Ingest live data

```bash
python scripts/market_ingestor.py
# Runs continuously during market hours
```

### Query candles (example)

```python
import duckdb
from pathlib import Path

conn = duckdb.connect("data/market_data/nse/candles/1m/2025-06-03.duckdb", read_only=True)
df = conn.execute(
    "SELECT * FROM candles WHERE symbol = 'NIFTY 50' ORDER BY timestamp"
).df()
conn.close()
```
