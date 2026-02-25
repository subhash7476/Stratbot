# PixityAI Trading Platform - Developer Guide

**Project:** PixityAI Algorithmic Trading Bot  
**Exchange:** NSE/NFO (Indian Equity & Derivatives)  
**Broker:** Upstox V2  
**Last Updated:** 2026-02-24

---

## 1. Project Overview

PixityAI is a production-grade, deterministic algorithmic trading platform built with Python, DuckDB, and Upstox V2. The system treats live trading as "backtesting with real money," ensuring that every decision is auditable, explainable, and reproducible.

### Core Design Principles

1. **Determinism**: Single-threaded execution ensuring backtest/live parity
2. **Database-First**: DuckDB as single source of truth for all state
3. **Separation of Concerns**: Strategies emit intent; Execution owns risk + broker
4. **Audit Trail**: Every trade traceable to signal → indicator → price

---

## 2. Architecture

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
    ┌────┴────┐
    ▼         ▼
update_analytics.py   TradingRunner (core/runner.py)
(offline, pre-compute)     │
    │                   reads bars (causal)
    ▼                         ▼
signals.db             BaseStrategy.process_bar()
(confluence, regime)         │
    └──────────►   SignalEvent (intent only)
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
```

### Directory Structure

```
D:\BOT\root\
├── core/                          # All business logic
│   ├── analytics/                 # Indicators, confluence, regime engine
│   │   ├── indicators/           # EMA, RSI, MACD, ATR, ADX, VWAP, UTBot, linreg
│   │   ├── confluence_engine.py  # Multi-indicator aggregation
│   │   ├── regime_engine.py     # Market regime detection
│   │   ├── day_features.py      # 53 engineered features
│   │   └── dispersion.py        # Cross-sectional dispersion
│   │
│   ├── strategies/               # Strategy implementations
│   │   ├── expansion_v3/       # Daily compression scanner + triggers
│   │   ├── regime/              # Regime-adaptive logic
│   │   ├── base.py             # Abstract BaseStrategy
│   │   ├── registry.py         # Strategy loader/registry
│   │   └── premium_tp_sl.py    # Premium TP/SL management
│   │
│   ├── execution/               # Order handling + risk
│   │   ├── handler.py          # ExecutionHandler
│   │   ├── position_tracker.py # Position state
│   │   ├── risk_manager.py     # Pre-trade risk validation
│   │   └── order_models.py     # Order dataclasses
│   │
│   ├── database/               # DB abstraction layer
│   │   ├── manager.py          # DatabaseManager
│   │   ├── schema.py           # SQL table definitions
│   │   ├── writers.py          # Domain write interfaces
│   │   ├── queries.py          # Common query patterns
│   │   └── providers/          # MarketDataProvider, AnalyticsProvider
│   │
│   ├── brokers/                 # Broker adapters
│   │   ├── broker_base.py      # Abstract BrokerAdapter
│   │   ├── upstox_adapter.py   # Live Upstox V2
│   │   └── paper_broker.py     # Simulated fills
│   │
│   ├── state/                  # Intraday state engine (daytype)
│   ├── runner.py               # TradingRunner (main orchestrator)
│   ├── clock.py                # Time abstraction (real vs replay)
│   └── events.py               # Frozen event contracts
│
├── scripts/                    # CLI entry points
│   ├── run_trading.py          # Live trading
│   ├── run_flask.py            # Flask web UI
│   ├── backtest.py             # Backtesting
│   ├── market_ingestor.py      # Live tick ingestor
│   ├── update_analytics.py     # Offline analytics
│   ├── run_v3_backtest.py     # Compression + breakout (v3)
│   ├── run_v9_backtest.py     # PM impulse (v9) - CURRENT BEST
│   └── unified_live_runner.py # Main live runner
│
├── flask_app/                  # Web monitoring UI
│   └── blueprints/             # Dashboard, auth, scanner, ops
│
├── models/                     # ML model artifacts
│   └── daytype/               # Day-type classifiers
│       └── logistic_13pm_prod/ # PRODUCTION model (80% accuracy)
│
└── data/                       # All data storage
    ├── market_data/            # DuckDB candles
    ├── trading/                # SQLite trading.db
    ├── signals/                # SQLite signals.db
    └── features/               # Day features CSVs
```

---

## 3. Database Layer

### Architecture

| Database | Type | Location | Owner |
|----------|------|----------|-------|
| Market data | DuckDB | `data/market_data/nse/candles/1m/{date}.duckdb` | Ingestor |
| Live buffer | DuckDB | `data/live_buffer/` | Ingestor |
| Trading | SQLite | `data/trading/trading.db` | Execution |
| Signals | SQLite | `data/signals/signals.db` | Analytics |
| Config | SQLite | `data/config.db` | Admin |

### Key Tables

**DuckDB (market data):**
```sql
candles(symbol, timeframe, timestamp, open, high, low, close, volume, is_synthetic)
ticks(symbol, timestamp, price, volume, bid, ask)
```

**SQLite trading.db:**
```sql
orders(order_id, signal_id, timestamp, symbol, order_type, side, quantity, price, status)
trades(trade_id, signal_id, strategy_id, symbol, timestamp, side, entry_price, exit_price, quantity, pnl, fees)
positions(symbol, quantity, avg_entry_price, realized_pnl, updated_at)
```

**SQLite signals.db:**
```sql
confluence_insights(timestamp, symbol, bias, confidence, agreement_level, indicator_states, insight_signal)
regime_insights(insight_id, symbol, timestamp, regime, momentum_bias, trend_strength, volatility_level)
signals(signal_id, strategy_id, symbol, signal_type, confidence, bar_ts, status)
```

---

## 4. Core Components

### Events (`core/events.py`)

All inter-module communication uses frozen dataclasses:

```python
OHLCVBar(symbol, timestamp, open, high, low, close, volume)
SignalEvent(strategy_id, symbol, timestamp, signal_type, confidence, metadata)
TradeEvent(trade_id, signal_id_reference, timestamp, symbol, status, direction, quantity, price, fees)
```

### Clock (`core/clock.py`)

```python
RealTimeClock(timezone='Asia/Kolkata')   # Live trading
ReplayClock(start_time)                  # Backtesting
```

### Strategy Base (`core/strategies/base.py`)

```python
class BaseStrategy(ABC):
    def process_bar(bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        # Strategies ONLY emit intent. No execution. No state persistence.
```

### ExecutionHandler (`core/execution/handler.py`)

Signal → Trade pipeline:
1. Idempotency check (no duplicate fills)
2. Risk manager clearance
3. Position sizing
4. Slippage application
5. Broker placement
6. Trade record to SQLite

### Confluence Engine (`core/analytics/confluence_engine.py`)

Aggregates 8 indicators (EMA, RSI, MACD, ADX, UT Bot, VWAP) into a single `ConfluenceInsight` with `confidence` (0.0–1.0).

### Regime Engine (`core/analytics/regime_engine.py`)

| Regime | Condition |
|--------|-----------|
| `BULL_TREND` | Close > EMA20 > EMA50, ADX > 22 |
| `BEAR_TREND` | Close < EMA20 < EMA50, ADX > 22 |
| `RANGING` | ADX < 20, Low Volatility |
| `VOLATILE_RANGE` | ADX < 20, High Volatility |

---

## 5. Configuration

### `config/credentials.json`

```json
{
  "broker": "UPSTOX",
  "user_id": "4PC6HK",
  "api_key": "...",
  "api_secret": "...",
  "redirect_uri": "http://127.0.0.1:5000/ops/callback/upstox",
  "access_token": "...",
  "exchanges": ["NFO", "NSE", "MCX", "BSE", "BFO", "CDS"]
}
```

### Environment Variables

```bash
FLASK_HOST=127.0.0.1
FLASK_PORT=5000
EXECUTION_MODE=DRY_RUN        # or PAPER, LIVE
MAX_TRADES_PER_DAY=100
MAX_DRAWDOWN_PCT=5.0
UNIFIED_MODE=1                # Single-process DuckDB access
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

---

## 6. How to Run

### Initialize System

```bash
python scripts/init_db.py
python scripts/manage_users.py create
python scripts/upstox_auth.py
python scripts/fetch_upstox_historical.py --days 365
python scripts/update_analytics.py
```

### Run Backtest

```bash
python scripts/run_v9_backtest.py    # Best performing strategy
python scripts/run_v3_backtest.py    # Compression + breakout
python scripts/run_combo_backtest.py # v6 + v7 blended
```

### Run Live/Paper Trading

```bash
python scripts/unified_live_runner.py ^
  --mode paper ^
  --symbols NSE_EQ|INE155A01022 ^
  --strategies pixityAI_meta ^
  --max-capital 100000 ^
  --max-position-size 1000
```

### Run Web Dashboard

```bash
python scripts/run_flask.py
# URL: http://127.0.0.1:5000
```

---

## 7. Strategy Research Summary

### What's Been Tried

| Version | Strategy | Result |
|---------|----------|--------|
| v3/v4 | Compression → Breakout | Fails ~50% directional (no edge) |
| v5/v6 | 20-Day Momentum | Just Nifty beta with fees |
| v7 | Mean Reversion (RSI + 5d) | Zero trades in bear markets |
| v8 | 15m Intraday | Few triggers, bad exit timing |
| v9 | PM Impulse (13:00) | **Best: +0.036% E/trade, 80% model accuracy** |

### Day-Type Classifier

The `logistic_13pm_prod` model achieves **80% holdout accuracy** classifying market regimes at 13:00:
- **BullTrend**: 81% chance of new PM high
- **BearTrend**: 81% chance of new PM low
- **Choppy**: Range-bound

**Features:** 35 features (Blocks B-F, excluding gap Block A).  
**Model:** Logistic regression (better than LightGBM for this task).

### Key Insights from Edge Analysis

1. **Fee Floor**: NSE equity round-trip = 0.07-0.08%, Futures = 0.04%
2. **All OHLCV patterns saturated** by institutional algos
3. **Real structural edges** in Indian markets:
   - Options theta decay (IV > realized vol by 3-5 pts)
   - Open Interest max-pain (market maker hedging)
   - Index reconstitution (forced passive buying)
   - Post-earnings drift (PEAD)

---

## 8. Current System State

### What's Working

- ✅ Single-threaded deterministic runner
- ✅ DuckDB market data with date partitioning
- ✅ Paper broker with realistic fills
- ✅ Pre-computed analytics (confluence, regime)
- ✅ Day-type classification (80% accuracy at 13pm)
- ✅ ZMQ telemetry → Flask dashboard
- ✅ Kill switches (max trades, drawdown, STOP file)
- ✅ Data staleness detection (5-min threshold)
- ✅ NSE holiday calendar (16 holidays 2026)
- ✅ Heartbeat file for external monitoring

### Known Limitations

| Issue | Risk | Fix Before |
|-------|------|------------|
| `_consecutive_losses` unused | Low | Live broker |
| ReconciliationEngine not called | None in paper | Live broker |
| Single-process fragility | Medium | Multi-process |
| No EOD auto-closure | Low | Live broker |
| Portfolio-level circuit breaker | Medium | Production |

### Database Sanitization

Old backtest results (pre-2026-02-15) have incorrect `fees=0.0` and `max_drawdown_pct=0.0`. Re-run Phase 5 scanner with corrected logic.

---

## 9. Key Classes Quick Reference

| Class | File | Purpose |
|-------|------|---------|
| `TradingRunner` | `core/runner.py` | Main orchestrator loop |
| `BaseStrategy` | `core/strategies/base.py` | Strategy interface |
| `ExecutionHandler` | `core/execution/handler.py` | Signal → trade |
| `PositionTracker` | `core/execution/position_tracker.py` | Position state |
| `RiskManager` | `core/execution/risk_manager.py` | Pre-trade validation |
| `ConfluenceEngine` | `core/analytics/confluence_engine.py` | Multi-indicator signal |
| `RegimeDetector` | `core/analytics/regime_engine.py` | Market regime |
| `DailyCompressionScanner` | `core/strategies/expansion_v3/` | EOD scan |
| `DatabaseManager` | `core/database/manager.py` | DB routing |
| `UpstoxAdapter` | `core/brokers/upstox_adapter.py` | Live broker |
| `PaperBroker` | `core/brokers/paper_broker.py` | Simulated fills |

---

## 10. Next Steps & Recommendations

### Immediate

1. **Validate the 4 core hypotheses** in `EDGE_ANALYSIS.md` before building new strategies:
   - Does compression have directional edge?
   - Is v5/v6 momentum just Nifty beta?
   - Does day-type edge increase with higher confidence?
   - Does max-pain proximity predict Nifty direction?

2. **Improve v9 profitability**:
   - Switch from futures to ATM options (limited risk)
   - Use only HIGH confidence (≥0.75) predictions
   - Extend holding time to 15:15

3. **Build OI-Anchored strategy** (if max-pain hypothesis validates):
   - Fetch NSE option chain at 9:30 AM
   - Compute max-pain strike
   - Fade direction toward max-pain with mechanical stops

### Before Live Trading

- Fix ReconciliationEngine to call on startup
- Add portfolio-level circuit breaker (stop if portfolio down 2%)
- Add EOD position auto-closure
- Test with paper trading for 5+ consecutive days

---

## 11. Useful Commands

```bash
# Check DB schema
python check_db_schema.py

# Monitor live trading
python scripts/listen_telemetry.py

# Validate filters
python scripts/validate_filters.py

# Run day-type engine
python scripts/live_daytype_engine.py --date 2026-02-20

# PM expectancy analysis
python scripts/run_pm_expectancy.py

# Generate day features
python scripts/generate_day_features.py --start 2023-01-02 --end 2026-02-13
```

---

## 12. Documentation Links

- `docs/CODEBASE_OVERVIEW.md` - Comprehensive codebase documentation
- `docs/PROJECT_MASTER.md` - Phase completion summary
- `docs/PROJECT_ARCHITECTURE_REPORT.md` - Architecture deep dive
- `docs/DATA_PIPELINE.md` - Data flow and storage
- `docs/DAYTYPE_CLASSIFIER.md` - Day-type ML system
- `docs/EDGE_ANALYSIS.md` - Strategy research findings
- `docs/LIVE_READINESS_AUDIT_REPORT.md` - Current system status

---

*This document should provide everything needed to continue work on the PixityAI trading platform. For questions, refer to the detailed docs in `docs/`.*
