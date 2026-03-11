# Codebase Overview

**Project:** PixityAI Trading Bot
**Exchange:** NSE/NFO (Indian Equity & Derivatives)
**Broker:** Upstox V2
**Last Updated:** 2026-02-22

---

## Table of Contents

1. [Directory Structure](#1-directory-structure)
2. [System Architecture](#2-system-architecture)
3. [Core Modules](#3-core-modules)
4. [Execution Engine](#4-execution-engine)
5. [Database Layer](#5-database-layer)
6. [Flask Web UI](#6-flask-web-ui)
7. [Key Classes Quick Reference](#7-key-classes-quick-reference)
8. [Configuration](#8-configuration)
9. [Deployment Checklist](#9-deployment-checklist)

---

## 1. Directory Structure

```
D:\BOT\root\
│
├── core/                          # All business logic
│   ├── analytics/                 # Indicators, confluence, regime engine
│   │   ├── indicators/            # EMA, RSI, MACD, ATR, ADX, VWAP, UTBot, linreg
│   │   ├── confluence_engine.py   # Multi-indicator aggregation → ConfluenceInsight
│   │   ├── regime_engine.py       # Market regime detection → RegimeSnapshot
│   │   ├── day_features.py        # 53 engineered features from 1m Nifty data
│   │   ├── dispersion.py          # Cross-sectional dispersion (CSAD/CSSD) engine
│   │   └── models.py              # Analytics dataclasses
│   │
│   ├── strategies/                # Strategy implementations
│   │   ├── expansion_v3/          # Daily compression scanner + triggers
│   │   │   ├── daily_compression_scanner.py
│   │   │   ├── daily_breakout_trigger.py
│   │   │   └── hourly_breakout_trigger.py
│   │   ├── regime/                # Regime-adaptive logic
│   │   │   ├── observer.py
│   │   │   ├── classifier.py
│   │   │   ├── circuit_breaker.py
│   │   │   ├── executor.py
│   │   │   └── sizing.py
│   │   ├── base.py                # Abstract BaseStrategy
│   │   ├── registry.py            # Strategy loader/registry
│   │   ├── premium_signal.py      # Stub: high-conviction signals
│   │   └── premium_tp_sl.py       # Premium TP/SL management
│   │
│   ├── execution/                 # Order handling + risk
│   │   ├── handler.py             # ExecutionHandler — signal → trade
│   │   ├── position_tracker.py    # Position state (source of truth)
│   │   ├── position_models.py     # Position dataclasses
│   │   ├── risk_manager.py        # Pre-trade risk validation
│   │   ├── order_models.py        # Order dataclasses
│   │   └── persistence/           # Trade/order persistence
│   │
│   ├── database/                  # DB abstraction layer
│   │   ├── manager.py             # DatabaseManager (connection routing)
│   │   ├── schema.py              # SQL table definitions
│   │   ├── writers.py             # Domain write interfaces
│   │   ├── queries.py             # Common query patterns
│   │   ├── providers/             # MarketDataProvider, AnalyticsProvider
│   │   └── ingestors/             # Data ingestion pipeline
│   │
│   ├── brokers/                   # Broker adapters
│   │   ├── broker_base.py         # Abstract BrokerAdapter
│   │   ├── upstox_adapter.py      # Live Upstox V2 (OAuth2 + WebSocket)
│   │   └── paper_broker.py        # Simulated fills (paper trading)
│   │
│   ├── state/                     # Intraday state engine
│   ├── instruments/               # Instrument parsing & models
│   ├── auth/                      # Authentication & credentials
│   ├── alerts/                    # Telegram notification system
│   ├── messaging/                 # ZMQ event distribution
│   ├── risk/                      # Greeks + portfolio risk models
│   ├── models/                    # ML model management
│   ├── clock.py                   # Time abstraction (real vs replay)
│   ├── runner.py                  # TradingRunner (main orchestrator)
│   ├── events.py                  # Frozen event contracts
│   └── logging.py                 # Logging configuration
│
├── scripts/                       # CLI entry points
│   ├── run_trading.py             # Live trading
│   ├── run_flask.py               # Flask web UI
│   ├── backtest.py                # General backtesting entry point
│   ├── init_db.py                 # Database bootstrap
│   ├── update_analytics.py        # Offline analytics compute
│   ├── market_ingestor.py         # Live tick ingestor
│   ├── fetch_upstox_historical.py # Historical data backfill
│   ├── generate_day_features.py   # 53-feature generator (1m → daily)
│   ├── cluster_day_types.py       # K-means day-type clustering
│   ├── train_daytype_classifier.py# Model training (LightGBM, Logistic)
│   ├── live_daytype_engine.py     # Replay/live checkpoint engine
│   ├── run_pm_expectancy.py       # PM session distribution analysis
│   ├── run_v3_backtest.py         # v3: compression + 1H breakout
│   ├── run_v4_backtest.py         # v4: compression + daily breakout
│   ├── run_v5_backtest.py         # v5: 20d momentum, trail only
│   ├── run_v6_backtest.py         # v6: 20d momentum, 2x ATR trail, no time stop
│   ├── run_v7_backtest.py         # v7: mean reversion (RSI + 5d return)
│   ├── run_v8_backtest.py         # v8: 15m intraday OR compression
│   ├── run_v9_backtest.py         # v9: PM impulse (13:00, regime-driven)
│   ├── run_combo_backtest.py      # Combined v6 + v7 (50/50 split)
│   ├── diagnose_am_checkpoint_edge.py  # AM edge evaluation
│   ├── diagnose_am_session.py     # AM session diagnostics
│   ├── diagnose_remaining_excursion.py # Excursion analysis
│   ├── research_dispersion.py     # Dispersion alpha research
│   ├── pm_timing_stats.py         # PM timing analysis
│   └── build_intraday_features.py # Intraday feature builder
│
├── flask_app/                     # Web monitoring UI
│   ├── __init__.py                # App factory + TelemetryBridge (ZMQ→SSE)
│   └── blueprints/
│       ├── auth.py                # Login, logout, role-based access
│       ├── dashboard.py           # Real-time positions & metrics
│       ├── database.py            # DB query/management UI
│       ├── backtest.py            # Backtest results viewer
│       ├── scanner.py             # Daily compression scanner UI
│       └── ops/routes.py          # Ops: kill switch, health, SSE telemetry
│
├── models/                        # ML model artifacts
│   └── daytype/
│       ├── lgbm_10am/             # LightGBM, 10am checkpoint
│       ├── lgbm_11am/             # LightGBM, 11am checkpoint
│       ├── lgbm_13pm/             # LightGBM, 13pm checkpoint
│       ├── logistic_10am/
│       ├── logistic_11am/
│       ├── logistic_13pm/
│       └── logistic_13pm_prod/    # PRODUCTION model (no Block A features)
│
├── data/                          # All data storage (see DATA_PIPELINE.md)
├── config/
│   └── credentials.json           # Upstox API credentials + access token
├── tests/                         # Test suite
└── docs/                          # Documentation (this folder)
```

---

## 2. System Architecture

### High-Level Flow

```
Market Data (Upstox WebSocket)
         │
         ▼
   market_ingestor.py
         │
         ▼
  DuckDB (partitioned daily)
  data/market_data/nse/candles/1m/{date}.duckdb
         │
    ┌────┴────────────────────────┐
    │                             │
    ▼                             ▼
update_analytics.py         TradingRunner (core/runner.py)
(offline, pre-compute)           │
    │                        reads bars (causal)
    ▼                             │
SQLite signals.db                 ▼
(confluence, regime)         BaseStrategy.process_bar()
    │                             │
    └──────────────►  StrategyContext  ◄──┘
                             │
                             ▼
                        SignalEvent (intent only)
                             │
                             ▼
                     ExecutionHandler
                     (risk check → size → broker)
                             │
                             ▼
                    UpstoxAdapter / PaperBroker
                             │
                             ▼
                     SQLite trading.db
                     (orders, trades, positions)
                             │
                             ▼
                    ZMQ Publisher → Flask UI / Telegram
```

### Design Principles

| Principle | Implementation |
|-----------|---------------|
| **Determinism** | Single-threaded `TradingRunner`, `Clock` abstraction |
| **Causal guarantee** | All analytics computed on data ≤ signal time |
| **Separation of concerns** | Strategies emit intent; Execution owns risk + broker |
| **Live = Backtest** | Same code path; only `Clock` and `BrokerAdapter` differ |
| **Audit trail** | Every trade traceable to signal → indicator → price |
| **Read-only runtime** | Analytics pre-computed offline; runtime never writes market data |

---

## 3. Core Modules

### 3.1 Events (`core/events.py`)

All inter-module communication uses frozen dataclasses:

```python
OHLCVBar(symbol, timestamp, open, high, low, close, volume)

SignalEvent(strategy_id, symbol, timestamp, signal_type, confidence, metadata)
# signal_type: BUY | SELL | EXIT | NEUTRAL

TradeEvent(trade_id, signal_id_reference, timestamp, symbol, status,
           direction, quantity, price, fees, rejection_reason)

OrderEvent(order_id, signal_id_reference, timestamp, symbol,
           order_type, side, quantity, price, status)
```

### 3.2 Clock (`core/clock.py`)

```python
RealTimeClock(timezone='Asia/Kolkata')   # Live trading
ReplayClock(start_time)                  # Backtesting: deterministic time
```

### 3.3 Strategy Base (`core/strategies/base.py`)

```python
class BaseStrategy(ABC):
    def process_bar(bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        # Strategies ONLY emit intent. No execution. No state persistence.
```

### 3.4 Confluence Engine (`core/analytics/confluence_engine.py`)

Aggregates 8 indicators into a single `ConfluenceInsight`:

| Indicator | Period | Bull Signal | Bear Signal |
|-----------|--------|-------------|-------------|
| EMA | 20, 50 | EMA20 > EMA50 | EMA20 < EMA50 |
| RSI | 14 | RSI > 60 | RSI < 40 |
| MACD | 12/26/9 | MACD > Signal | MACD < Signal |
| ADX | 14 | ADX > 25 | ADX > 25 (bear context) |
| UT Bot | - | Close > UT level | Close < UT level |
| VWAP | Session | Close > VWAP | Close < VWAP |

Output: `confidence` (0.0–1.0) = fraction of indicators agreeing.

### 3.5 Regime Engine (`core/analytics/regime_engine.py`)

| Regime | Condition |
|--------|-----------|
| `BULL_TREND` | Close > EMA20 > EMA50, ADX > 25 |
| `BEAR_TREND` | Close < EMA20 < EMA50, ADX > 25 |
| `RANGING` | Price between MA bands, ADX < 20 |
| `VOLATILE_RANGE` | ATR% > 3.0% |

Volatility levels: `LOW` (<0.5%), `MEDIUM` (0.5–1.5%), `HIGH` (1.5–3%), `EXTREME` (>3%)

### 3.6 Day Features (`core/analytics/day_features.py`)

Computes 53 features per trading day from Nifty 50 1m data. Used for unsupervised day-type clustering. See `DAYTYPE_CLASSIFIER.md` for full details.

---

## 4. Execution Engine

### 4.1 ExecutionHandler (`core/execution/handler.py`)

```python
ExecutionConfig:
    mode: DRY_RUN | PAPER | LIVE
    default_quantity: 100
    max_position_size: 1000
    slippage_value: 0.01
    max_trades_per_day: 100
    max_drawdown_limit: 0.05        # 5%
    max_portfolio_delta: 1000.0
    max_portfolio_vega: 500.0
```

Signal → Trade pipeline:
1. Idempotency check (no duplicate fills)
2. Risk manager clearance
3. Position sizing (account + volatility adjusted)
4. Slippage application
5. Broker placement
6. Trade record to SQLite

### 4.2 Risk Manager (`core/execution/risk_manager.py`)

Validation order (first failure = REJECT):
1. Global kill switch active?
2. Daily trade count ≥ max?
3. Order qty > max_order_qty?
4. Symbol in deny list?
5. Symbol NOT in allow list (if specified)?

### 4.3 Kill Switches

| Trigger | Action |
|---------|--------|
| `max_trades_per_day` reached | Stop new signals |
| Equity drawdown ≥ 5% | Hard stop |
| `STOP` file in root dir | Immediate halt |
| Broker disconnect | Pause + alert |

### 4.4 Position Tracker (`core/execution/position_tracker.py`)

Single source of truth for all open positions. Driven by `FillEvent`s. Handles netting, average price, realized PnL calculation.

---

## 5. Database Layer

### 5.1 Architecture

| Database | Type | Location | Owner | Readers |
|----------|------|----------|-------|---------|
| Market data | DuckDB | `data/market_data/nse/candles/1m/{date}.duckdb` | Ingestor | Strategies, Backtest |
| Live buffer | DuckDB | `data/live_buffer/` | Ingestor | Runtime |
| Trading | SQLite | `data/trading/trading.db` | Execution | Flask UI |
| Signals | SQLite | `data/signals/signals.db` | Analytics | Strategies |
| Config | SQLite | `data/config.db` | Admin | System |

### 5.2 Key Tables

**DuckDB (market data):**
```sql
candles(symbol, timeframe, timestamp, open, high, low, close, volume, is_synthetic)
ticks(symbol, timestamp, price, volume, bid, ask)
```

**SQLite trading.db:**
```sql
orders(order_id, signal_id, timestamp, symbol, order_type, side, quantity, price, status, broker_order_id)
trades(trade_id, signal_id, strategy_id, symbol, timestamp, side, entry_price, exit_price, quantity, pnl, fees)
positions(symbol, quantity, avg_entry_price, realized_pnl, updated_at)
```

**SQLite signals.db:**
```sql
confluence_insights(timestamp, symbol, bias, confidence, agreement_level, indicator_states, insight_signal)
regime_insights(insight_id, symbol, timestamp, regime, momentum_bias, trend_strength, volatility_level, persistence_score, ma_fast, ma_medium, ma_slow)
signals(signal_id, strategy_id, symbol, signal_type, confidence, bar_ts, status)
```

### 5.3 DuckDB Partitioning

Files are partitioned by date: `{date}.duckdb` (e.g., `2025-02-22.duckdb`).
Schema per file: same `candles` table, filtered to that date.

---

## 6. Flask Web UI

**Start:** `python scripts/run_flask.py`
**Default URL:** `http://127.0.0.1:5000`

### Routes

| Blueprint | Route | Purpose |
|-----------|-------|---------|
| auth | `GET /login`, `POST /auth/login`, `GET /logout` | Authentication |
| dashboard | `GET /dashboard/` | Live positions + metrics |
| dashboard | `GET /dashboard/api/positions` | Positions JSON |
| dashboard | `GET /dashboard/api/trades` | Recent trades JSON |
| scanner | `GET /scanner/` | Compression scanner UI |
| scanner | `GET /scanner/api/candidates` | Latest candidates |
| database | `GET /database/` | DB query interface |
| backtest | `GET /backtest/` | Backtest results |
| ops | `POST /ops/api/kill_switch` | Emergency stop |
| ops | `GET /ops/telemetry` | SSE real-time stream |

### TelemetryBridge

ZMQ subscriber → SSE publisher. Runs in background thread. Bridges `TradingRunner` telemetry to the browser in real time.

---

## 7. Key Classes Quick Reference

| Class | File | Purpose |
|-------|------|---------|
| `TradingRunner` | `core/runner.py` | Main orchestrator loop |
| `BaseStrategy` | `core/strategies/base.py` | Strategy interface |
| `ExecutionHandler` | `core/execution/handler.py` | Signal → trade |
| `PositionTracker` | `core/execution/position_tracker.py` | Position state |
| `RiskManager` | `core/execution/risk_manager.py` | Pre-trade validation |
| `ConfluenceEngine` | `core/analytics/confluence_engine.py` | Multi-indicator signal |
| `RegimeDetector` | `core/analytics/regime_engine.py` | Market regime |
| `DailyCompressionScanner` | `core/strategies/expansion_v3/daily_compression_scanner.py` | EOD compression scan |
| `DatabaseManager` | `core/database/manager.py` | DB connection routing |
| `UpstoxAdapter` | `core/brokers/upstox_adapter.py` | Live broker API |
| `PaperBroker` | `core/brokers/paper_broker.py` | Simulated fills |
| `RealTimeClock` | `core/clock.py` | Live time |
| `ReplayClock` | `core/clock.py` | Backtest time |

---

## 8. Configuration

### `config/credentials.json`

```json
{
  "broker": "UPSTOX",
  "user_id": "4PC6HK",
  "api_key": "...",
  "api_secret": "...",
  "redirect_uri": "http://127.0.0.1:5000/ops/callback/upstox",
  "access_token": "...",   // JWT, refreshed daily
  "token_saved_at": ...,
  "last_refresh_date": "YYYY-MM-DD",
  "exchanges": ["NFO", "NSE", "MCX", "BSE", "BFO", "BCD", "CDS"],
  "order_types": ["MARKET", "LIMIT", "SL", "SL-M"]
}
```

### Environment Variables

```bash
FLASK_HOST=127.0.0.1
FLASK_PORT=5000
EXECUTION_MODE=DRY_RUN        # or PAPER, LIVE
MAX_TRADES_PER_DAY=100
MAX_DRAWDOWN_PCT=5.0
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
UNIFIED_MODE=1                # Single-process DuckDB access
```

---

## 9. Deployment Checklist

### One-Time Setup
```bash
pip install -r requirements.txt
python scripts/init_db.py
python scripts/manage_users.py create      # Admin user
python scripts/upstox_auth.py              # OAuth token
python scripts/fetch_upstox_historical.py --days 365
python scripts/update_analytics.py
```

### Daily Operations
```bash
# Pre-market
python scripts/market_ingestor.py          # Start tick ingestor
python scripts/update_analytics.py         # Refresh indicators

# Trading session
python scripts/run_flask.py                # Start UI
python scripts/run_trading.py              # Start trading

# Post-market
python scripts/daily_review.py             # P&L summary
```

### Backtesting
```bash
python scripts/run_v9_backtest.py          # Latest strategy
python scripts/run_combo_backtest.py       # v6 + v7 blended
```

---

*For strategy research history, see `STRATEGY_RESEARCH_LOG.md`.*
*For data pipeline details, see `DATA_PIPELINE.md`.*
*For day-type classifier, see `DAYTYPE_CLASSIFIER.md`.*
*For edge analysis, see `EDGE_ANALYSIS.md`.*
