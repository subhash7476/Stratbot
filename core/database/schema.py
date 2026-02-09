"""
Database Schema Definitions for Refactored Architecture
Matches monolithic schemas for smooth migration.
"""

# ─────────────────────────────────────────────────────────────
# MARKET DATA (DuckDB)
# ─────────────────────────────────────────────────────────────

MARKET_TICKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    symbol VARCHAR NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    price DOUBLE NOT NULL,
    volume BIGINT NOT NULL,
    bid DOUBLE,
    ask DOUBLE,
    PRIMARY KEY (symbol, timestamp)
);
"""

MARKET_CANDLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol VARCHAR NOT NULL,
    timeframe VARCHAR NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    volume BIGINT NOT NULL,
    is_synthetic BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (symbol, timeframe, timestamp)
);
"""

# ─────────────────────────────────────────────────────────────
# TRADING (SQLite)
# ─────────────────────────────────────────────────────────────

TRADING_ORDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    signal_id TEXT,
    timestamp DATETIME NOT NULL,
    symbol TEXT NOT NULL,
    order_type TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL,
    status TEXT NOT NULL,
    broker_order_id TEXT,
    metadata TEXT
);
"""

TRADING_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    signal_id TEXT UNIQUE,
    strategy_id TEXT,
    symbol TEXT,
    timestamp DATETIME,
    side TEXT,
    entry_price DOUBLE,
    exit_price DOUBLE,
    quantity INTEGER,
    pnl DOUBLE,
    fees DOUBLE,
    metadata TEXT
);
"""

TRADING_POSITIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    quantity REAL NOT NULL DEFAULT 0.0,
    avg_entry_price REAL DEFAULT 0.0,
    realized_pnl REAL DEFAULT 0.0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# ─────────────────────────────────────────────────────────────
# SIGNALS & SCANNERS (SQLite)
# ─────────────────────────────────────────────────────────────

SIGNALS_INSIGHTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS confluence_insights (
    timestamp DATETIME,
    symbol TEXT,
    bias TEXT,
    confidence DOUBLE,
    agreement_level DOUBLE,
    indicator_states TEXT, -- JSON string
    insight_signal TEXT,
    PRIMARY KEY (timestamp, symbol)
);
"""

SIGNALS_REGIME_SCHEMA = """
CREATE TABLE IF NOT EXISTS regime_insights (
    insight_id TEXT,
    symbol TEXT,
    timestamp DATETIME,
    regime TEXT,
    momentum_bias TEXT,
    trend_strength DOUBLE,
    volatility_level TEXT,
    persistence_score DOUBLE,
    ma_fast DOUBLE,
    ma_medium DOUBLE,
    ma_slow DOUBLE,
    PRIMARY KEY (symbol, timestamp)
);
"""

SIGNALS_STRATEGY_SIGNALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    strategy_id TEXT,
    symbol TEXT,
    signal_type TEXT,
    confidence DOUBLE,
    bar_ts DATETIME,
    status TEXT, -- 'PENDING', 'EXECUTED', 'REJECTED'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# ─────────────────────────────────────────────────────────────
# USER & CONFIG (SQLite)
# ─────────────────────────────────────────────────────────────

CONFIG_USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT,
    roles TEXT, -- Comma-separated
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

CONFIG_ROLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    role_name TEXT PRIMARY KEY,
    permissions TEXT -- Comma-separated
);
"""

CONFIG_WATCHLIST_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_watchlist (
    username TEXT DEFAULT 'default',
    instrument_key TEXT NOT NULL,
    trading_symbol TEXT,
    exchange TEXT,
    market_type TEXT,
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (username, instrument_key)
);
"""

CONFIG_INSTRUMENT_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS instrument_meta (
    symbol TEXT PRIMARY KEY,
    trading_symbol TEXT,
    instrument_key TEXT,
    exchange TEXT,
    market_type TEXT,
    lot_size INTEGER DEFAULT 1,
    tick_size DOUBLE DEFAULT 0.05,
    is_active BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

CONFIG_RUNNER_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS runner_state (
    symbol TEXT,
    strategy_id TEXT,
    timeframe TEXT DEFAULT '1m',
    current_bias TEXT,
    signal_state TEXT,
    confidence REAL,
    last_bar_ts DATETIME,
    status TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, strategy_id)
);
"""

CONFIG_WEBSOCKET_STATUS_SCHEMA = """
CREATE TABLE IF NOT EXISTS websocket_status (
    key TEXT PRIMARY KEY DEFAULT 'singleton',
    status TEXT NOT NULL,
    updated_at DATETIME NOT NULL,
    pid INTEGER
);
"""

CONFIG_FO_STOCKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS fo_stocks (
    trading_symbol TEXT PRIMARY KEY,
    instrument_key TEXT NOT NULL,
    name TEXT,
    lot_size INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT 1,
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# ─────────────────────────────────────────────────────────────
# BACKGROUND JOBS (SQLite)
# ─────────────────────────────────────────────────────────────

CONFIG_DOWNLOAD_JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS download_jobs (
    job_id TEXT PRIMARY KEY,
    symbols TEXT, -- Comma-separated or 'ALL'
    unit TEXT,
    interval INTEGER,
    from_date DATE,
    to_date DATE,
    status TEXT DEFAULT 'PENDING', -- 'PENDING', 'RUNNING', 'COMPLETED', 'FAILED'
    progress TEXT, -- e.g. "5/100" or "50%"
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# ─────────────────────────────────────────────────────────────
# BACKTEST (DuckDB/SQLite)
# ─────────────────────────────────────────────────────────────

BACKTEST_INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY,
    strategy_id TEXT,
    symbol TEXT,
    start_date DATE,
    end_date DATE,
    params TEXT, -- JSON
    total_trades INTEGER,
    win_rate DOUBLE,
    total_pnl DOUBLE,
    max_drawdown DOUBLE,
    sharpe_ratio DOUBLE,
    status TEXT DEFAULT 'PENDING',
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

BACKTEST_RUN_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    symbol TEXT,
    entry_ts TIMESTAMP,
    exit_ts TIMESTAMP,
    direction TEXT,
    entry_price DOUBLE,
    exit_price DOUBLE,
    qty INTEGER,
    pnl DOUBLE,
    fees DOUBLE,
    metadata JSON
);
"""
