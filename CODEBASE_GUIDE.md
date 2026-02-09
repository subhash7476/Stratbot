# Codebase Guide

This documentation provides a comprehensive overview of the trading system's architecture, directory structure, and core components.

## 1. Directory Structure

The codebase is organized into several key directories, each with a specific responsibility:

- `flask_app/`: Contains the web-based user interface and API endpoints. It follows the Flask application factory pattern and is organized into blueprints.
- `app_facade/`: An abstraction layer (bridge) between the web application and the core trading logic. It ensures the UI doesn't directly depend on core implementation details.
- `core/`: The heart of the system, containing all business logic.
    - `auth/`: Authentication and user management.
    - `data/`: Database interactions (DuckDB), market data providers, and persistence logic.
    - `execution/`: Order handling, risk management, trade recording, and broker interaction.
    - `strategies/`: Trading strategy definitions and implementations.
    - `analytics/`: Indicator calculations, market regime detection, and reporting.
    - `alerts/`: Notification systems (e.g., Telegram).
    - `brokers/`: Adapters for different brokerage APIs (e.g., Upstox, Paper trading).
    - `api/`: Lower-level API clients.
    - `zmq/`: ZeroMQ publisher/subscriber components for real-time event distribution and telemetry.
- `scripts/`: Entry points for running the application, backtesting, database initialization, and maintenance tasks.
- `config/`: Configuration settings and environment-specific variables.
- `ops/`: Operational logging and system health monitoring.
- `tests/`: Comprehensive test suite for various system components.

## 2. Core Components

### Flask Application (`flask_app/`)
The Flask application serves as the user interface for monitoring and managing the trading bot.
- **Factory Pattern**: `flask_app/__init__.py` uses `create_app()` to initialize the application, register blueprints, and set up global context processors.
- **Blueprints**: Modules like `auth`, `dashboard`, `database`, `backtest`, `scanner`, and `ops` are registered as blueprints to handle specific routes.

### Database Interactions (DuckDB)
The system uses DuckDB for fast, local, and relational data storage.
- **Client**: `core/data/duckdb_client.py` provides thread-safe connection management and context managers for database operations.
- **Schema**: `core/data/schema.py` defines the database tables for users, trades, signals, market data, and backtest results.
- **Persistence**: Modules like `core/data/analytics_persistence.py` and `core/execution/recorder.py` handle the actual saving of data.

### Authentication Logic (`core/auth/`)
Secure access to the system is managed through the `auth` module.
- **Service**: `core/auth/auth_service.py` handles user authentication and session management.
- **Models**: `core/auth/models.py` defines User and Role data structures.
- **Security**: `core/auth/password.py` manages password hashing and verification.

### Trading Strategies (`core/strategies/`)
Strategies are the "brains" of the system, deciding when to enter or exit trades.
- **Base Interface**: `core/strategies/base.py` defines the `BaseStrategy` contract that all strategies must implement.
- **Process**: Strategies receive a `StrategyContext` (current position, analytics, regime) and process each `OHLCVBar` to optionally emit a `SignalEvent`.
- **Registry**: `core/strategies/registry.py` maintains a list of available strategies for dynamic loading.

### Execution Handler (`core/execution/handler.py`)
The execution handler is responsible for turning strategy "intent" (signals) into action (orders/trades).
- **Validation**: Performs risk checks, position limit checks, and daily trade limit enforcement.
- **Safety**: Includes "kill switches" that can stop trading based on drawdown, loss thresholds, or manual intervention.
- **Modes**: Supports `DRY_RUN`, `PAPER`, and `LIVE` execution modes.

### Trading Runner (`core/runner.py`)
The `TradingRunner` is the system orchestrator. It coordinates the data flow by:
1. Pulling market data and analytics.
2. Invoking strategies in a deterministic order.
3. Handing resulting signals to the execution handler.
4. Updating position tracking.

### ZMQ Event Distribution (`core/zmq/`)
The ZeroMQ layer provides real-time, low-latency event distribution across system components.
- **Patterns**: Uses PUB/SUB for market data distribution and telemetry streaming.
- **Components**: `ZmqPublisher` for publishing events, `ZmqSubscriber` for consuming events.
- **Telemetry**: `TelemetryPublisher` provides unified publishing interface for metrics, positions, health, and logs.
- **Decoupling**: Enables process isolation with dual-rail model (fast ZMQ path + fallback DuckDB polling).
- **Flask Bridge**: Provides SSE endpoint to expose real-time telemetry to the browser without polling.

## 3. Module Interactions

The system is designed with clear separation of concerns:

1. **User Request**: A user interacts with the `flask_app` UI.
2. **Facade Call**: The Flask route calls a method in the `app_facade` (e.g., `BacktestFacade.run_backtest`).
3. **Core Orchestration**: The Facade instantiates the necessary `core` components (e.g., `TradingRunner`, `MarketDataProvider`, `ExecutionHandler`) and starts the process.
4. **Execution Loop**: The `TradingRunner` loops through market data, passing it to `BaseStrategy` implementations.
5. **Signal to Trade**: If a strategy emits a `SignalEvent`, the `ExecutionHandler` validates it and uses a `BrokerAdapter` (e.g., `UpstoxAdapter`) to place an order.
6. **Persistence**: All signals, trades, and analytics are persisted back to DuckDB via the `core/data` layer.

## 4. File-by-File Guide

### Root Directory
- `CODEBASE_GUIDE.md`: This file.

### `app_facade/`
- `analytics_facade.py`: Bridge for analytics data.
- `auth_facade.py`: Bridge for authentication services.
- `backtest_facade.py`: Bridge for running and retrieving backtests.
- `data_facade.py`: Bridge for database and market data status.
- `ops_facade.py`: Bridge for operational tasks and system health.

### `core/`
- `clock.py`: Provides `Clock` interfaces for both real-time and simulated time (backtesting).
- `events.py`: Defines core data structures like `OHLCVBar`, `SignalEvent`, and `TradeEvent`.
- `runner.py`: The main system orchestrator.
- `database.py`: Legacy compatibility wrapper for DuckDB connections.
- `zmq/`: ZeroMQ components for real-time event distribution.
    - `zmq_publisher.py`: Publisher wrapper for ZMQ PUB sockets.
    - `zmq_subscriber.py`: Subscriber wrapper for ZMQ SUB sockets with topic filtering.
    - `telemetry_publisher.py`: Unified interface for publishing system telemetry.

### `core/analytics/`
- `indicators/`: Implementation of various technical indicators (VWAP, RSI, EMA, MACD, etc.).
- `regime_engine.py`: Logic for detecting market states (Trend, Volatile, Ranging).
- `reporting.py`: Generates analytics reports and performance metrics.

### `core/auth/`
- `auth_service.py`: Core logic for authenticating users.
- `credentials.py`: Secure storage/retrieval for API credentials.
- `models.py`: Data models for users and roles.

### `core/data/`
- `duckdb_client.py`: Thread-safe DuckDB connection management.
- `market_data_provider.py`: Interface for retrieving market bars/ticks.
- `schema.py`: SQL definitions for all database tables.

### `core/execution/`
- `handler.py`: Translates signals to trades with risk and safety checks.
- `risk_manager.py`: Logic for validating trades against risk parameters.
- `position_tracker.py`: Real-time tracking of open positions.

### `core/strategies/`
- `base.py`: Abstract base class for all strategies.
- `registry.py`: Central registration of all implemented strategies.
- `premium_signal.py`: Implementation of specific signal-based strategy.

### `flask_app/`
- `__init__.py`: Flask application factory.
- `blueprints/`: Directory containing route handlers for different sections of the app.

### `scripts/`
- `run_flask.py`: Entry point to start the web application.
- `run_trading.py`: Entry point to start the live trading bot.
- `backtest.py`: Script for running strategy backtests.
- `init_db.py`: Initializes the DuckDB database and schema.
