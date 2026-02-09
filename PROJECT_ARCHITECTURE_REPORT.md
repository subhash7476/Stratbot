# Trading Platform Architecture Report

## 1. Executive Summary

The Trading Platform is a production-grade, deterministic algorithmic trading system built with Python, DuckDB, and Upstox V2. The system treats live trading as "backtesting with real money," ensuring that every decision is auditable, explainable, and reproducible.

**System Type**: Event-driven, intraday trading platform with live market connectivity and historical backtesting capabilities.

**Core Design Philosophy**: 
- **Discipline**: Strict separation of concerns with clear architectural boundaries
- **Determinism**: Single-threaded execution ensuring backtest/live parity
- **Decoupling**: Database-first architecture with minimal inter-component dependencies

**Constraints**:
- Single-threaded execution to ensure deterministic behavior
- Database-first approach with DuckDB as the single source of truth
- Strict kill-switch mechanisms for operational safety

## 2. High-Level Architecture

```
┌───────────────────┐      ┌─────────────────────────┐      ┌─────────────────────┐
│  Market Data Node │      │   DuckDB (Market Data)  │      │ Strategy Runner Node│
│  (Sole Writer)    │─────▶│   (Multi-Reader)        │◀─────│ (Read-Only Market)  │
└─────────┬─────────┘      └─────────────────────────┘      └──────────┬──────────┘
          │                            ▲                               │
          │          ZMQ PUB           │                               │ ZMQ PUB (Future)
          └────────────────────────────┼───────────────────────────────┘
                                       │
                                       ▼
                           ┌─────────────────────────┐
                           │   Market Scanner Node   │
                           │   (Read-Only Market)    │
                           └─────────────────────────┘
                                       │
                                       ▼
                           ┌─────────────────────────┐
                           │   SQLite (State/Config) │
                           │   (Multi-Writer/WAL)    │
                           └─────────────────────────┘
```

**Major Components**:
- **Market Data Node**: Standalone process owning WebSocket, Tick Aggregation, and DuckDB writing. Publishes live candles via ZMQ.
- **Strategy Runner Node**: Independent process for strategy logic and authoritative execution. Subscribes to ZMQ market data.
- **Market Scanner Node**: Best-effort process for broad market scanning.
- **Flask UI**: Web-based dashboard, reads state from SQLite.

**Process Model & Ownership**:
- **Market Data Node**: Sole authority for DuckDB writes.
- **Strategy Runner**: Authoritative for Trading State (SQLite).
- **Scanner**: Pure consumer, no state mutation allowed except for runner_state.
- **Database Rules**: All non-Ingestor processes must use `DatabaseManager(read_only=True)` for DuckDB access.

**Communication Model**:
- Database-based communication between components
- Event-driven processing using standardized data contracts
- No direct messaging system; database serves as the message bus

**State Management**:
- **Stateful**: Database layer, position tracker, execution handler
- **Stateless**: Strategies, analytics engines, UI components

## 3. End-to-End Data Flow

### Live Trading Data Path
```
Market Data Feed → WebSocketIngestor → TickAggregator → OHLCVBar → TradingRunner → Strategy → Signal → ExecutionHandler → Broker → Trade
```

1. **Market Data Feed**: Real-time tick data from Upstox WebSocket
2. **WebSocketIngestor**: Receives and validates incoming market data
3. **TickAggregator**: Converts ticks to 1-minute OHLCV bars deterministically
4. **TradingRunner**: Coordinates data flow between providers, strategies, and execution
5. **Strategy**: Processes bars and generates signals based on analytics
6. **Signal**: Standardized event representing trading intent
7. **ExecutionHandler**: Validates signals against risk parameters and executes trades
8. **Broker**: Places orders via Upstox API
9. **Trade**: Confirmed execution recorded in database

### Storage / Historical Data Path
```
Historical Data → DuckDB Tables → Analytics Calculation → Confluence Insights → Database Storage
```

1. **Historical Data**: Stored in DuckDB with exchange/timeframe partitioning
2. **Analytics Calculation**: Pre-computed indicators and confluence analysis
3. **Confluence Insights**: Combined analytical view of market conditions
4. **Database Storage**: Persisted to SQLite for real-time access

### Data Ownership at Each Stage
- **Market Data**: Owned by data ingestion modules, read-only for consumers
- **Analytics**: Owned by analytics modules, read-only for strategies
- **Signals**: Owned by strategies, consumed by execution
- **Trades**: Owned by execution layer, final source of truth
- **Positions**: Owned by position tracker, updated by execution handler

## 4. Folder & Module Map

### Root Directory
- `README.md`: High-level system overview
- `CODEBASE_GUIDE.md`: Comprehensive codebase documentation
- `PROJECT_MASTER.md`: System architecture and phase completion summary
- `pyproject.toml`: Dependency management
- `scripts/`: Entry points for all system operations

### `app_facade/` - UI Bridge Layer
- **Purpose**: Read-only bridges between Flask UI and core logic
- **What belongs here**: Facade classes that expose core functionality to UI
- **What must never be added**: Direct database queries, business logic, or broker interactions

### `core/` - Business Logic Core
- **Purpose**: All business logic and system orchestration
- **What belongs here**: Strategies, execution, analytics, data providers, brokers
- **What must never be added**: UI code, direct HTTP handling, or external service calls without abstraction

#### `core/analytics/` - Analytical Engines
- **Purpose**: Technical indicators and market analysis
- **What belongs here**: RSI, MACD, VWAP, confluence engines, regime detection
- **What must never be added**: Strategy logic or execution code

#### `core/auth/` - Authentication
- **Purpose**: User authentication and credential management
- **What belongs here**: Password hashing, user validation, credential storage
- **What must never be added**: UI code or business logic

#### `core/brokers/` - Broker Integration
- **Purpose**: Abstraction layer for different broker APIs
- **What belongs here**: Upstox adapter, paper trading simulator, order management
- **What must never be added**: Strategy logic or UI code

#### `core/data/` - Data Providers
- **Purpose**: Market data access and persistence
- **What belongs here**: Market data providers, schema definitions, tick aggregators
- **What must never be added**: Strategy logic or execution code

#### `core/execution/` - Trade Execution
- **Purpose**: Risk management, order placement, position tracking
- **What belongs here**: Execution handler, risk manager, position tracker
- **What must never be added**: Strategy logic or UI code

#### `core/strategies/` - Trading Strategies
- **Purpose**: Strategy implementations that generate trading signals
- **What belongs here**: Strategy classes, signal generation logic
- **What must never be added**: Broker interactions, risk management, or UI code

### `flask_app/` - Web Interface
- **Purpose**: User interface and API endpoints
- **What belongs here**: Flask routes, templates, static assets
- **What must never be added**: Business logic or direct database queries

### `scripts/` - Entry Points
- **Purpose**: Main entry points for system operations
- **What belongs here**: Main application runners, utility scripts
- **What must never be added**: Business logic or UI code

### `config/` - Configuration
- **Purpose**: System configuration and settings
- **What belongs here**: Settings files, market universe definitions
- **What must never be added**: Business logic or executable code

### `ops/` - Operations
- **Purpose**: System monitoring and operational logging
- **What belongs here**: Session logging, health checks, operational utilities
- **What must never be added**: Business logic or UI code

## 5. Key Entry Points & Processes

### Main Scripts
- `scripts/run_flask.py`: Starts the web dashboard
- `scripts/run_trading.py`: Starts the live trading engine
- `scripts/live_runner.py`: Advanced live trading with kill switches
- `scripts/backtest.py`: Runs historical backtests
- `scripts/init_db.py`: Initializes database schema
- `scripts/update_analytics.py`: Updates analytical calculations

### Process Dependencies
1. **Database Initialization**: Must run before any other process
2. **Analytics Update**: Should run before live trading to populate confluence insights
3. **Web Dashboard**: Can run independently but requires initialized database
4. **Live Trading**: Depends on market data feeds and broker connectivity

### Startup Sequence
1. Initialize database schema and seed data
2. Update analytics for current market conditions
3. Start WebSocket connection for live data
4. Initialize strategies and execution handler
5. Begin trading loop with proper error handling

## 6. Strategy Lifecycle

### Strategy Definition
- All strategies inherit from `BaseStrategy` abstract class
- Must implement `process_bar()` method returning optional `SignalEvent`
- Identified by unique strategy ID registered in `registry.py`

### Signal Generation
1. **Data Input**: Strategy receives `OHLCVBar` and `StrategyContext`
2. **Analysis**: Strategy analyzes current market conditions using analytics snapshot
3. **Decision**: Strategy determines if conditions meet entry/exit criteria
4. **Signal Creation**: Strategy creates `SignalEvent` with type, confidence, and metadata

### Risk Checks
1. **Position Limits**: Validate against maximum position size
2. **Daily Trade Limits**: Prevent over-trading
3. **Drawdown Limits**: Stop trading if losses exceed threshold
4. **Kill Switches**: Manual and automatic safety mechanisms

### Order Placement
1. **Signal Validation**: Execution handler validates signal against risk parameters
2. **Order Creation**: Convert signal to broker-specific order format
3. **Broker Communication**: Place order via broker adapter
4. **Status Tracking**: Monitor order status and update position

### Exit Logic
- **Stop Loss**: Automatic exit when price reaches predetermined level
- **Take Profit**: Automatic exit when profit target is achieved
- **Time-Based**: Exit at market close or after holding period
- **Signal-Based**: Exit when opposing signal is generated

## 7. Database Design & Philosophy

### Why Database Exists
- **Single Source of Truth**: Eliminate data inconsistency between components
- **Audit Trail**: Maintain complete history of all trading activity
- **Reproducibility**: Enable deterministic backtesting and replay
- **Persistence**: Survive system restarts and crashes

### Data Persistence vs Computation
- **Persisted**: Trades, signals, positions, user data, configuration
- **Computed**: Technical indicators, confluence insights, performance metrics
- **Hybrid**: Market data (historical persisted, live computed from ticks)

### Conceptual Schema
- **Market Data**: Ticks and candles stored in DuckDB for performance
- **Trading State**: Orders, trades, positions in SQLite for ACID compliance
- **Signals**: Confluence insights, strategy signals in SQLite
- **Configuration**: Users, roles, watchlists in SQLite

### Source-of-Truth Rules
- **Market Data**: Upstox API is the ultimate source
- **Trading State**: Execution handler maintains truth about positions
- **Signals**: Strategy implementations determine signal validity
- **Configuration**: Database tables are the single source of truth

## 8. Messaging & Concurrency Model

### How Live Data is Distributed
- **Database Polling**: Components periodically query database for updates
- **Event-Based**: Standardized data contracts (`OHLCVBar`, `SignalEvent`, `TradeEvent`)
- **Single-Threaded**: TradingRunner processes one bar at a time per symbol

### Why This Model Was Chosen
- **Determinism**: Ensures identical results between backtest and live trading
- **Simplicity**: Avoids complex messaging infrastructure
- **Consistency**: Database transactions guarantee data integrity
- **Debugging**: Sequential execution simplifies troubleshooting

### Failure Handling and Restart Behavior
- **Graceful Degradation**: System continues with reduced functionality
- **Automatic Recovery**: Reconnects to data feeds and resumes processing
- **State Restoration**: Reloads positions and outstanding orders from database
- **Journaling**: Sub-minute tick journaling enables precise recovery

## 9. Operational Rules & Invariants

### Non-Negotiable Rules
- **Who Can Write What**: Only specific modules can write to certain tables
  - Data ingestion modules: Market data tables only
  - Execution handler: Trading state tables only
  - Analytics modules: Analytics tables only
  - UI: Configuration tables only (with proper validation)

- **Strategy Isolation**: Strategies must not access broker APIs or trading state
- **Database Transactions**: All writes must be wrapped in transactions
- **Kill Switches**: Multiple layers of safety mechanisms must be respected

### Common Mistakes to Avoid
- **Direct Database Writes**: Never write to database outside designated methods
- **Cross-Module Dependencies**: Avoid tight coupling between components
- **Shared State Mutations**: Use immutable data structures where possible
- **Blocking Operations**: Keep UI responsive with asynchronous operations

### Ordering and Startup Requirements
1. **Database First**: Schema must be initialized before other components
2. **Analytics Second**: Analytics must be current before live trading
3. **Connectivity Third**: Broker connection verified before trading begins
4. **Monitoring Fourth**: Operational logging active before trading starts

## 10. How to Safely Extend the System

### Adding a New Strategy
1. Create new strategy class inheriting from `BaseStrategy`
2. Implement `process_bar()` method with signal generation logic
3. Register strategy in `core/strategies/registry.py`
4. Add strategy ID to UI dropdown or configuration
5. Test with backtesting before live deployment

### Adding a New Scanner
1. Create scanner class that implements scanning interface
2. Define scan criteria and output format
3. Integrate with existing analytics pipeline
4. Store results in appropriate database tables
5. Expose results through facade layer to UI

### Adding a New Data Source
1. Create new broker adapter implementing `BrokerAdapter` interface
2. Handle authentication and API communication
3. Map external data formats to internal contracts
4. Implement rate limiting and error handling
5. Add configuration options for new data source

### Adding or Modifying UI Components
1. Create new blueprint in `flask_app/blueprints/`
2. Implement routes that call appropriate facade methods
3. Create templates following existing patterns
4. Add navigation links to main layout
5. Ensure proper authentication and authorization


See: docs/ZMQ_LAYER_IMPLEMENTATION_SUMMARY.md for full ZeroMQ design and guarantees.

## 11. Known Limitations & Tech Debt

### Current Constraints
- **Single-Threaded**: Limited to processing one symbol at a time per runner
- **Memory Usage**: Large datasets may require pagination or streaming
- **Broker Dependency**: Tightly coupled to Upstox API (though abstraction exists)
- **Market Hours**: Designed primarily for Indian market hours

### Planned Refactors
- **Multi-Processing**: Parallel processing for multiple symbols
- **Caching Layer**: Improved caching for frequently accessed data
- **Configuration Management**: More flexible configuration system
- **Testing Coverage**: Expanded unit and integration tests

### Temporary or Risky Areas
- **Rate Limiting**: Manual rate limiting in broker adapters could be improved
- **Error Handling**: Some error cases may not be properly handled
- **Database Locking**: Concurrent access patterns need refinement
- **Memory Leaks**: Long-running processes may accumulate memory

## 12. Appendix

### Glossary
- **OHLCVBar**: Open, High, Low, Close, Volume data for a time period
- **SignalEvent**: Trading intent generated by a strategy
- **TradeEvent**: Confirmed execution of a trade
- **Confluence Insight**: Combined analytical view of market conditions
- **Kill Switch**: Safety mechanism to stop trading under adverse conditions
- **Deterministic**: Produces identical results given identical inputs

### Naming Conventions
- **Classes**: PascalCase (e.g., `TradingRunner`, `BaseStrategy`)
- **Functions**: snake_case (e.g., `process_bar`, `get_position`)
- **Constants**: UPPER_CASE (e.g., `MAX_DRAWDOWN_LIMIT`)
- **Variables**: snake_case (e.g., `current_price`, `signal_type`)

### Message/Event Formats
- **OHLCVBar**: `{symbol, timestamp, open, high, low, close, volume}`
- **SignalEvent**: `{strategy_id, symbol, timestamp, signal_type, confidence, metadata}`
- **TradeEvent**: `{trade_id, signal_id_reference, timestamp, symbol, status, direction, quantity, price, fees}`
- **OrderEvent**: `{order_id, signal_id_reference, timestamp, symbol, order_type, side, quantity, price, status}`