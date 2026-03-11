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
    symbol VARCHAR DEFAULT '',
    instrument_key VARCHAR DEFAULT '',
    timeframe VARCHAR DEFAULT '1m',
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

MARKET_OHLCV_RESAMPLED_SCHEMA = """
CREATE TABLE IF NOT EXISTS ohlcv_resampled (
    symbol VARCHAR NOT NULL,
    timeframe VARCHAR NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    volume BIGINT NOT NULL,
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
    direction TEXT,
    entry_price DOUBLE,
    price DOUBLE,
    exit_price DOUBLE,
    quantity INTEGER,
    pnl DOUBLE,
    fees DOUBLE,
    status TEXT,
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

TRADING_COMMODITY_STRATEGY_SNAPSHOTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS commodity_strategy_snapshots (
    timestamp TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    regime TEXT,
    selected_strike REAL,
    liquidity_pass INTEGER,
    risk_size INTEGER,
    decision TEXT,
    rejection_reason TEXT,
    metrics_json TEXT,
    snapshot_json TEXT
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

SIGNALS_REGIME_SNAPSHOTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS regime_snapshots (
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

CONFIG_ROLES_SEED = """
INSERT INTO roles (role_name, permissions)
VALUES
    ('admin', 'all'),
    ('viewer', 'read')
ON CONFLICT(role_name) DO NOTHING;
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

CONFIG_FO_STOCKS_MASTER_SCHEMA = """
CREATE TABLE IF NOT EXISTS fo_stocks_master (
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

# ─────────────────────────────────────────────────────────────
# SCANNER (SQLite)
# ─────────────────────────────────────────────────────────────

SCANNER_RESULTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS scanner_results (
    scan_id TEXT PRIMARY KEY,
    scan_timestamp DATETIME,
    total_symbols INTEGER,
    profitable_symbols INTEGER DEFAULT 0,
    scan_params TEXT,
    status TEXT DEFAULT 'RUNNING',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

SCANNER_SYMBOL_RESULTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS scanner_symbol_results (
    scan_id TEXT,
    symbol TEXT,
    trading_symbol TEXT,
    train_pnl DOUBLE,
    train_trades INTEGER,
    train_win_rate DOUBLE,
    train_max_dd DOUBLE,
    train_run_id TEXT,
    train_status TEXT,
    test_pnl DOUBLE,
    test_trades INTEGER,
    test_win_rate DOUBLE,
    test_max_dd DOUBLE,
    test_run_id TEXT,
    test_status TEXT,
    is_profitable BOOLEAN DEFAULT 0,
    rank INTEGER DEFAULT 0,
    error TEXT,
    PRIMARY KEY (scan_id, symbol)
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

BACKTEST_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_trades (
    trade_id TEXT PRIMARY KEY,
    run_id TEXT,
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

# ─────────────────────────────────────────────────────────────
# STOCK DAY-TYPE PAPER TRADING (SQLite — trading.db)
# ─────────────────────────────────────────────────────────────

PAPER_SIGNALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_paper_signals (
    id          BIGINT PRIMARY KEY,
    session_date TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    trading_symbol TEXT,
    predicted_state TEXT NOT NULL,   -- BullTrend | BearTrend | Choppy
    confidence   REAL    NOT NULL,
    p_bull       REAL,
    p_bear       REAL,
    p_choppy     REAL,
    signal_time  TEXT    NOT NULL,   -- ISO timestamp of the checkpoint bar
    c_ret        REAL,               -- return from open to checkpoint
    c_range      REAL,               -- H-L range from open to checkpoint / open
    c_close_loc  REAL,               -- close location in AM range (0=low, 1=high)
    broker       TEXT    DEFAULT 'paper',
    UNIQUE(session_date, symbol)
);
"""

PAPER_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_paper_trades (
    id               BIGINT PRIMARY KEY,
    session_date     TEXT    NOT NULL,
    symbol           TEXT    NOT NULL,
    trading_symbol   TEXT,
    direction        TEXT    NOT NULL,   -- long | short
    broker           TEXT    NOT NULL DEFAULT 'paper',
    confidence       REAL,
    predicted_state  TEXT,
    qty              INTEGER NOT NULL DEFAULT 1,
    capital          REAL    NOT NULL DEFAULT 0,
    entry_time       TEXT    NOT NULL,
    entry_price      REAL    NOT NULL,
    stop_price       REAL    NOT NULL,
    target_price     REAL,
    exit_time        TEXT,
    exit_price       REAL,
    exit_reason      TEXT,              -- time_exit | stop_hit | target_hit | trailing_stop | session_reset
    pnl_gross_pct    REAL,
    pnl_net_pct      REAL,
    pnl_rs           REAL,             -- actual Rs profit/loss
    cost_pct         REAL    DEFAULT 0.0,
    created_at       TEXT    DEFAULT CURRENT_TIMESTAMP,
    -- Trade Learning Protocol V1 (all nullable) --
    regime_state          TEXT,    -- EXPANSION | CONTRACTION | SHOCK (VIX-based)
    session_type          TEXT,    -- AM | PM
    index_return_entry    REAL,    -- Nifty return from open to entry bar
    breadth_ratio         REAL,    -- pct of signals with positive c_ret at entry
    signal_rank           INTEGER, -- rank by confidence in today's universe (1=best)
    signal_percentile     REAL,    -- confidence percentile vs universe (0-100)
    intended_entry        REAL,    -- bar open at entry (model target price)
    slippage_bps          REAL,    -- (actual - intended) / intended * 10000
    dispersion_csad       REAL,    -- CSAD from dispersion engine at 11am
    dispersion_pct        REAL,    -- CSAD rolling 60-day percentile
    mae_pct               REAL,    -- max adverse excursion % from entry
    mfe_pct               REAL,    -- max favorable excursion % from entry
    mae_r                 REAL,    -- MAE in R-multiples (MAE / SL distance)
    mfe_r                 REAL,    -- MFE in R-multiples (MFE / SL distance)
    exit_efficiency       REAL     -- pnl_gross_pct / mfe_pct (0-1, higher=better)
);
"""

# ─────────────────────────────────────────────────────────────
# V9 PM SCALPER PAPER TRADING (SQLite — trading.db)
# ─────────────────────────────────────────────────────────────

V9_PAPER_SIGNALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS v9_paper_signals (
    id          BIGINT PRIMARY KEY,
    session_date TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    predicted_state TEXT NOT NULL,
    confidence   REAL    NOT NULL,
    model_version TEXT,
    signal_time  TEXT    NOT NULL,
    UNIQUE(session_date, symbol)
);
"""

V9_PAPER_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS v9_paper_trades (
    id               BIGINT PRIMARY KEY,
    session_date     TEXT    NOT NULL,
    entry_time       TEXT    NOT NULL,
    entry_price      REAL    NOT NULL,
    stop_level       REAL    NOT NULL,
    exit_time        TEXT,
    exit_price       REAL,
    exit_reason      TEXT,
    confidence       REAL,
    predicted_state  TEXT,
    pnl_gross_pct    REAL,
    pnl_net_pct      REAL,
    model_version    TEXT,
    option_symbol    TEXT,          -- e.g. NIFTY06MAR2622500CE
    entry_premium    REAL,          -- Upstox LTP at entry
    exit_premium     REAL,          -- Upstox LTP at exit
    lot_size         INTEGER DEFAULT 75,
    pnl_rs           REAL,          -- net PnL in Rupees
    created_at       TEXT    DEFAULT CURRENT_TIMESTAMP
);
"""

# ─────────────────────────────────────────────────────────────
# NIFTY SHIELD — Weekly Options Selling (SQLite — trading.db)
# ─────────────────────────────────────────────────────────────

NS_PAPER_SIGNALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ns_paper_signals (
    id              BIGINT PRIMARY KEY,
    session_date    TEXT NOT NULL,
    underlying      TEXT NOT NULL,
    predicted_state TEXT NOT NULL,
    confidence      REAL NOT NULL,
    vix_close       REAL,
    regime_sizing   REAL,
    lots            INTEGER,
    structure       TEXT,
    signal_time     TEXT NOT NULL,
    UNIQUE(session_date, underlying)
);
"""

NS_PAPER_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS ns_paper_trades (
    id                BIGINT PRIMARY KEY,
    session_date      TEXT NOT NULL,
    underlying        TEXT NOT NULL,
    structure         TEXT NOT NULL,
    entry_time        TEXT NOT NULL,
    entry_price       REAL NOT NULL,
    ce_symbol         TEXT NOT NULL,
    pe_symbol         TEXT NOT NULL,
    ce_strike         REAL NOT NULL,
    pe_strike         REAL NOT NULL,
    ce_entry_premium  REAL NOT NULL,
    pe_entry_premium  REAL NOT NULL,
    total_premium     REAL NOT NULL,
    lots              INTEGER NOT NULL,
    entry_delta       REAL,
    entry_theta       REAL,
    exit_time         TEXT,
    exit_price        REAL,
    ce_exit_premium   REAL,
    pe_exit_premium   REAL,
    exit_reason       TEXT,
    pnl_gross_rs      REAL,
    pnl_net_rs        REAL,
    costs_rs          REAL,
    max_loss_rs       REAL,
    adjustments       INTEGER DEFAULT 0,
    predicted_state   TEXT,
    confidence        REAL,
    vix_close         REAL,
    source            TEXT DEFAULT 'live',
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# ─────────────────────────────────────────────────────────────
# TRADE LEARNING PROTOCOL V1 (SQLite — trading.db / signals.db)
# ─────────────────────────────────────────────────────────────

TRADING_TRADE_CONTEXT_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_context (
    trade_id            TEXT PRIMARY KEY,
    model_version       TEXT DEFAULT 'TLP_V1_CORE',
    universe_version    TEXT DEFAULT 'NIFTY_UNIVERSE_V1',
    regime_state        TEXT,
    regime_confidence   REAL,
    session_type        TEXT,
    dispersion_value    REAL,
    dispersion_pct      REAL,
    volatility_value    REAL,
    volatility_pct      REAL,
    breadth_ratio       REAL,
    sl_distance         REAL,
    risk_r              REAL,
    pnl_rs              REAL,
    mae_points          REAL,
    mfe_points          REAL,
    mae_r               REAL,
    mfe_r               REAL,
    theoretical_max_pnl REAL,
    exit_efficiency     REAL,
    signal_timestamp    TEXT,
    entry_timestamp     TEXT,
    exit_timestamp      TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(trade_id) REFERENCES trades(trade_id)
);
"""

SIGNALS_DAILY_METRICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_structural_metrics (
    timestamp           DATETIME PRIMARY KEY,
    dispersion_csad     REAL,
    volatility_atr      REAL,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# ─────────────────────────────────────────────────────────────
# OPTIONS STRUCTURAL ENGINE (DuckDB)
# ─────────────────────────────────────────────────────────────

OPTIONS_CHAIN_SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS option_chain_snapshot (
    snapshot_id       BIGINT PRIMARY KEY,
    snapshot_timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    underlying_symbol  TEXT NOT NULL,  -- "NSE_INDEX|Nifty 50" or "NSE_INDEX|Nifty Bank"
    expiry_date        TEXT NOT NULL,  -- YYYY-MM-DD format
    strike_price       REAL NOT NULL,
    option_type        TEXT NOT NULL,  -- 'CE' or 'PE'
    instrument_key     TEXT NOT NULL,  -- Upstox key: "NSE_FO|54710"
    tradingsymbol      TEXT NOT NULL,  -- Human-readable: "NIFTY04MAR2622500CE"

    -- Price data
    ltp                REAL,           -- Last traded price
    open               REAL,
    high               REAL,
    low                REAL,
    close              REAL,

    -- OI data
    oi                 INTEGER DEFAULT 0,
    oi_change          INTEGER DEFAULT 0,
    oi_change_pct      REAL DEFAULT 0.0,
    volume             INTEGER DEFAULT 0,

    -- Greeks (from API or calculated)
    iv                 REAL,           -- Implied volatility (decimal: 0.14 = 14%)
    delta              REAL,
    gamma              REAL,
    theta              REAL,
    vega               REAL,
    rho                REAL,

    -- Metadata
    lot_size           INTEGER DEFAULT 75,
    underlying_ltp     REAL            -- Spot price at snapshot time
);
"""

OPTIONS_DAILY_OI_SUMMARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_oi_summary (
    summary_id         BIGINT PRIMARY KEY,
    date               TEXT NOT NULL,
    underlying_symbol  TEXT NOT NULL,
    expiry_date        TEXT NOT NULL,

    -- CE totals
    total_ce_oi        INTEGER DEFAULT 0,
    total_ce_volume    INTEGER DEFAULT 0,
    avg_ce_iv          REAL,

    -- PE totals
    total_pe_oi        INTEGER DEFAULT 0,
    total_pe_volume    INTEGER DEFAULT 0,
    avg_pe_iv          REAL,

    -- Calculated metrics
    pcr                REAL,           -- PE OI / CE OI
    max_pain_strike    REAL,
    atm_strike         REAL,

    -- Net Gamma Exposure (GEX)
    net_gamma_ce       REAL DEFAULT 0,
    net_gamma_pe       REAL DEFAULT 0,
    net_gamma_total    REAL DEFAULT 0,
    zero_gamma_level   REAL,

    -- Market context
    underlying_close   REAL,
    underlying_change_pct REAL,

    UNIQUE(date, underlying_symbol, expiry_date)
);
"""

OPTIONS_GEX_SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS gex_snapshot (
    gex_id             BIGINT PRIMARY KEY,
    snapshot_timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    underlying_symbol  TEXT NOT NULL,
    expiry_date        TEXT NOT NULL,
    strike_price       REAL NOT NULL,
    option_type        TEXT NOT NULL,  -- 'CE' or 'PE'

    -- Gamma exposure
    gamma              REAL,
    oi                 INTEGER,
    lot_size           INTEGER,
    gamma_exposure     REAL,  -- Gamma × OI × LotSize

    -- Higher-order Greeks (optional)
    vanna              REAL,
    charm              REAL
);
"""

OPTIONS_INDEXES_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_option_chain_underlying ON option_chain_snapshot(underlying_symbol);
CREATE INDEX IF NOT EXISTS idx_option_chain_expiry ON option_chain_snapshot(expiry_date);
CREATE INDEX IF NOT EXISTS idx_option_chain_strike ON option_chain_snapshot(strike_price);
CREATE INDEX IF NOT EXISTS idx_option_chain_timestamp ON option_chain_snapshot(snapshot_timestamp);
CREATE INDEX IF NOT EXISTS idx_daily_oi_date ON daily_oi_summary(date);
CREATE INDEX IF NOT EXISTS idx_gex_underlying ON gex_snapshot(underlying_symbol);
CREATE INDEX IF NOT EXISTS idx_gex_timestamp ON gex_snapshot(snapshot_timestamp);
"""

# ─────────────────────────────────────────────────────────────
# BOOTSTRAP STATEMENTS (Combined Schema)
# ─────────────────────────────────────────────────────────────

BOOTSTRAP_STATEMENTS = [
    # Market Data (DuckDB)
    MARKET_TICKS_SCHEMA,
    MARKET_CANDLES_SCHEMA,
    MARKET_OHLCV_RESAMPLED_SCHEMA,

    # Trading (SQLite)
    TRADING_ORDERS_SCHEMA,
    TRADING_TRADES_SCHEMA,
    TRADING_POSITIONS_SCHEMA,
    TRADING_COMMODITY_STRATEGY_SNAPSHOTS_SCHEMA,

    # Signals & Scanners (SQLite)
    SIGNALS_INSIGHTS_SCHEMA,
    SIGNALS_REGIME_SCHEMA,
    SIGNALS_REGIME_SNAPSHOTS_SCHEMA,
    SIGNALS_STRATEGY_SIGNALS_SCHEMA,

    # User & Config (SQLite)
    CONFIG_USERS_SCHEMA,
    CONFIG_ROLES_SCHEMA,
    CONFIG_ROLES_SEED,
    CONFIG_WATCHLIST_SCHEMA,
    CONFIG_INSTRUMENT_META_SCHEMA,
    CONFIG_RUNNER_STATE_SCHEMA,
    CONFIG_WEBSOCKET_STATUS_SCHEMA,
    CONFIG_FO_STOCKS_SCHEMA,
    CONFIG_FO_STOCKS_MASTER_SCHEMA,
    CONFIG_DOWNLOAD_JOBS_SCHEMA,

    # Backtest (DuckDB/SQLite)
    BACKTEST_INDEX_SCHEMA,
    SCANNER_RESULTS_SCHEMA,
    SCANNER_SYMBOL_RESULTS_SCHEMA,
    BACKTEST_RUN_TRADES_SCHEMA,
    BACKTEST_TRADES_SCHEMA,

    # Stock Day-Type Paper Trading
    PAPER_SIGNALS_SCHEMA,
    PAPER_TRADES_SCHEMA,

    # V9 PM Scalper
    V9_PAPER_SIGNALS_SCHEMA,
    V9_PAPER_TRADES_SCHEMA,

    # Nifty Shield
    NS_PAPER_SIGNALS_SCHEMA,
    NS_PAPER_TRADES_SCHEMA,

    # Trade Learning Protocol V1
    TRADING_TRADE_CONTEXT_SCHEMA,
    SIGNALS_DAILY_METRICS_SCHEMA,

    # Options Structural Engine
    OPTIONS_CHAIN_SNAPSHOT_SCHEMA,
    OPTIONS_DAILY_OI_SUMMARY_SCHEMA,
    OPTIONS_GEX_SNAPSHOT_SCHEMA,
]

INDEX_STATEMENTS = [
    OPTIONS_INDEXES_SCHEMA,
]
