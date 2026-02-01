# 🚀 Trading Platform: Project Master Document

This document provides a comprehensive, single-source-of-truth overview of the production-grade, deterministic algorithmic trading platform. It consolidates all architecture decisions, phase completions, and operational protocols implemented to date.

---

## 🎯 1. Project Vision & Goals
To build a world-class trading system defined by **discipline, determinism, and total decoupling**. The system treats live trading as "backtesting with real money," ensuring that every decision is auditable, explainable, and reproducible.

### Core Principles
1.  **Strategies Stay Dumb**: Strategies only emit intent (`SignalEvent`). They don't know about brokers, position sizing, or risk limits.
2.  **Analytics Produce Facts**: All indicators are pre-computed offline. Runtime is read-only.
3.  **Execution Owns Reality**: Risk, sizing, and broker interaction live exclusively in the execution layer.
4.  **Runner is Neutral**: A single-threaded orchestrator that treats live and backtest data identically.
5.  **Audit-First**: Every trade must be explainable by the exact analytical facts that justified it.

---

## 📊 2. System Architecture

### High-Level Flow
```
CLI Scripts → DuckDB → Core Logic → Facade → Flask UI
    ↓           ↓          ↓          ↓        ↓
Computation   Storage   Decision    Bridge   Display
```

### Layer Responsibilities
-   **Layer 1: CLI & Batch (Computation)**: The only place where heavy computation (Analytics) occurs.
-   **Layer 2: Data (Persistence)**: DuckDB implementation using a strict "Downward-Only" flow.
-   **Layer 3: Analytics (Stateless Computation)**: Stateless engines producing immutable snapshots.
-   **Layer 4: Auth (Headless)**: PBKDF2 hashing, testable without a web layer.
-   **Layer 5: Strategies (Decision Only)**: Pure functions transforming facts into intent.
-   **Layer 6: Execution (Action)**: Authoritative layer for risk, capital allocation, and broker API.
-   **Layer 7: Events (Contracts)**: Standardized frozen dataclasses for system-wide communication.
-   **Layer 8: Facade (Bridge)**: Read-only bridges for the Flask UI.
-   **Layer 9: UI (Flask)**: A thin client for displaying pre-computed state.

---

## ✅ 3. Phase Completion Summary

### Phase 1: Database Bedrock
Established DuckDB as the single source of truth with idempotent schemas and atomic transactions.
-   **Key Achievements**: Idempotent schema creation, explicit transactions, zero Flask dependencies.
-   **Key Files**: `core/data/schema.py`, `scripts/init_db.py`.

### Phase 2: Trading Strategies
Implemented the `BaseStrategy` interface and three initial strategies.
-   **EHMA Pivot**: Pure price-only EMA crossover logic.
-   **Confluence Consumer**: Analytics-driven strategy using `confluence_insights`.
-   **Regime Adaptive**: Conditional logic adjusting entry/exit based on market regime.
-   **Key Files**: `core/strategies/base.py`, `core/strategies/registry.py`.

### Phase 3: Runner & Integration
Created the `TradingRunner` orchestrator. Guaranteed single-threaded, deterministic execution.
-   **Key Achievements**: Separation of coordination from logic, deterministic iteration order.
-   **Key Files**: `core/runner.py`, `scripts/run_trading.py`.

### Phase 4: Backtesting & Replay
Developed a high-fidelity replay engine using `ReplayClock` and historical providers.
-   **Key Achievements**: `Clock` sovereignty (data providers advance time), live/backtest parity.
-   **Key Files**: `core/clock.py`, `core/data/historical_market_provider.py`.

### Phase 5: Performance, Scale & Safety
Optimized I/O with analytical caching, chunked data pre-fetching, and implemented system-wide **Kill Switches**.
-   **Key Achievements**: `CachedAnalyticsProvider`, `WriteBuffer` for batch persistence.
-   **Key Files**: `core/data/cached_analytics_provider.py`, `core/execution/write_buffer.py`.

### Phase 6: Connectivity & Reality
Bridged to live markets with `TickAggregator` and `WebSocketMarketProvider`. Added the `trades` audit table.
-   **Key Achievements**: Deterministic tick-to-bar aggregation, broker abstraction (`BrokerAdapter`).
-   **Key Files**: `core/data/tick_aggregator.py`, `core/brokers/paper_broker.py`.

### Phase 7: Post-Trade Intelligence
Introduced the "Truth Layer" to explain every trade and the `FactFrequencyAnalyzer` for rarity scoring.
-   **Key Achievements**: `TradeTruth` model, `DrawdownAnalyzer`, `CapitalAllocator`.
-   **Key Files**: `core/post_trade/trade_context_builder.py`, `core/execution/capital_allocator.py`.

### Phase 8: Live Deployment & Alerting
Built a fire-and-forget `TelegramNotifier` and integrated operational alerts into the execution path.
-   **Key Achievements**: Non-blocking alerts, automated kill-switch triggers.
-   **Key Files**: `core/alerts/alerter.py`, `scripts/live_runner.py`.

### Phase 9: Review & Discipline
Codified operator SOPs and created the Post-Session Review (PSR) pipeline.
-   **Key Achievements**: `SessionLogger` (Markdown logs), `daily_review.py`, `weekly_review.py`.
-   **Key Files**: `ops/session_log.py`, `scripts/daily_review.py`, `docs/live_playbook.md`.

### Phase 10: Upstox Integration
Implemented actual REST/WebSocket connectivity for Upstox V2, including OAuth2 flow and binary market feed plumbing.
-   **Key Achievements**: Real-time order placement, position reconciliation, `upstox_auth.py`.
-   **Key Files**: `scripts/upstox_auth.py`, `core/brokers/upstox_adapter.py`.

---

## 🔒 4. Architectural & Security Guarantees

### Computation Boundaries
-   **No Web Triggers**: Analytics can **never** be triggered by an HTTP request.
-   **Read-Only UI**: The Flask app uses `read_only=True` connections to DuckDB.
-   **Stateless Strategies**: Strategies do not persist state between bars; the `PositionTracker` is the only source of truth for quantity.

### Data Integrity
-   **Immutable Snapshots**: Analytics and trade records are frozen dataclasses.
-   **Zero Logic Leaks**: Strategies are forbidden from importing `core.analytics` engines.
-   **One-Way Data Flow**: CLI Scripts → DB → Core → Facade → UI.

### Operational Safety
-   **Kill Switches**:
-   `Max Daily Trades`: Stops over-trading.
-   `Max Drawdown`: Stops bleeding capital.
-   `Manual STOP`: Immediate halt via file flag.
-   **Dead Letter Queue (DLQ)**: Captures failed events for post-hoc analysis.

---

## 🚀 5. Detailed Usage & Deployment

### 1. Initialize System
```bash
# Create database and seed roles
python scripts/init_db.py

# Create admin user (requires interactive input)
python scripts/manage_users.py create
```

### 2. Update Analytics (Offline Computation)
```bash
# Update all active symbols
python scripts/update_analytics.py

# Specific symbol check
python scripts/update_analytics.py --symbol INFY
```

### 3. Run Replay & Backfills
```bash
# Standard Backtest
python scripts/backtest.py --symbol CDSL --days 15 --strategy ehma_pivot

# Systematic Strategy Backfill (Artifacts in backfills/)
python scripts/backfill_strategies.py --strategies ehma_pivot --symbols CDSL --days 30
```

### 4. Live/Paper Trading
```bash
# Paper trading using live WebSocket data (or mock fallback)
python scripts/live_runner.py --mode paper --symbols CDSL --strategies ehma_pivot
```

### 5. Web Dashboard (Deployment)
```bash
# Development server
python scripts/run_flask.py

# Production deployment
gunicorn -w 4 "flask_app:create_app()"
```

---

## 🧪 6. Testing & Verification

### Headless Tests (Core Logic)
- **Auth**: `python tests/auth/test_service.py`
- **Analytics**: Verified via `scripts/update_analytics.py` snapshots.
- **Strategies**: Verified for determinism via `backfill_strategies.py`.

### System Audits
- **Position Reconciliation**: `python scripts/reconcile_positions.py --mode live`
- **Daily Review**: `python scripts/daily_review.py --date YYYY-MM-DD`
- **Weekly Review**: `python scripts/weekly_review.py`

---

## 📁 7. File Inventory (Key Modules)

| Directory | Purpose |
| :--- | :--- |
| `core/analytics/` | Indicators (RSI, MACD) and Confluence engines. |
| `core/auth/` | Headless PBKDF2 authentication. |
| `core/brokers/` | Upstox and Paper broker adapters. |
| `core/data/` | DuckDB providers and Tick Aggregators. |
| `core/execution/` | Risk, Sizing, and Order handling. |
| `core/strategies/` | Intent-only trading logic. |
| `app_facade/` | Read-only bridges for UI. |
| `flask_app/` | Dashboard and API. |
| `ops/` | Session logging and change management. |
| `docs/` | Playbooks and Incident Response guides. |

---

## 📜 8. Operational Playbooks (Summary)
-   **Pre-Market**: Reconcile positions and verify internet latency.
-   **In-Market**: Follow the "Code Freeze" rule. Monitor Telegram for alerts.
-   **Post-Market**: Perform structured Daily Review and close the Session Log.

**Status: SYSTEM IS LIVE-CAPITAL READY** 🔒
