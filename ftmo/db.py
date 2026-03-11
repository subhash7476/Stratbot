"""DuckDB schema and helpers for FTMO trading data."""

import duckdb
from pathlib import Path

DB_PATH = Path(__file__).parent / "ftmo_trading.db"

TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    timestamp_entry TIMESTAMP NOT NULL,
    timestamp_exit TIMESTAMP NOT NULL,
    direction TEXT NOT NULL,
    entry_price DOUBLE NOT NULL,
    exit_price DOUBLE NOT NULL,
    stop_loss DOUBLE NOT NULL,
    take_profit DOUBLE NOT NULL,
    risk_amount DOUBLE NOT NULL,
    pnl_dollar DOUBLE NOT NULL,
    pnl_r DOUBLE NOT NULL,
    exit_reason TEXT NOT NULL,
    sweep_direction TEXT,
    sweep_price DOUBLE,
    pre_ny_high DOUBLE,
    pre_ny_low DOUBLE,
    m15_atr DOUBLE,
    m5_atr DOUBLE,
    session_date TEXT NOT NULL
)
"""

DAILY_STATS_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_stats (
    session_date TEXT PRIMARY KEY,
    starting_equity DOUBLE NOT NULL,
    ending_equity DOUBLE NOT NULL,
    daily_pnl DOUBLE NOT NULL,
    daily_pnl_pct DOUBLE NOT NULL,
    trades_taken INTEGER NOT NULL,
    wins INTEGER NOT NULL,
    losses INTEGER NOT NULL,
    max_equity DOUBLE NOT NULL,
    daily_drawdown DOUBLE NOT NULL,
    overall_drawdown DOUBLE NOT NULL,
    risk_status TEXT NOT NULL,
    consecutive_losses INTEGER NOT NULL
)
"""

SIMULATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS simulations (
    sim_id TEXT PRIMARY KEY,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    starting_equity DOUBLE NOT NULL,
    ending_equity DOUBLE NOT NULL,
    total_pnl DOUBLE NOT NULL,
    total_trades INTEGER NOT NULL,
    win_rate DOUBLE NOT NULL,
    avg_r DOUBLE NOT NULL,
    expectancy DOUBLE NOT NULL,
    max_drawdown_dollar DOUBLE NOT NULL,
    max_drawdown_pct DOUBLE NOT NULL,
    worst_losing_streak INTEGER NOT NULL,
    days_to_target INTEGER,
    passed BOOLEAN NOT NULL,
    breached_daily_limit BOOLEAN NOT NULL,
    breached_overall_limit BOOLEAN NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def get_connection(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH), read_only=read_only)


def init_db():
    con = get_connection()
    con.execute(TRADES_SCHEMA)
    con.execute(DAILY_STATS_SCHEMA)
    con.execute(SIMULATIONS_SCHEMA)
    con.close()


def clear_tables():
    con = get_connection()
    con.execute("DELETE FROM trades")
    con.execute("DELETE FROM daily_stats")
    con.execute("DELETE FROM simulations")
    con.close()


def insert_trades(trades: list[dict]):
    if not trades:
        return
    con = get_connection()
    cols = list(trades[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_str = ", ".join(cols)
    con.executemany(
        f"INSERT OR REPLACE INTO trades ({col_str}) VALUES ({placeholders})",
        [tuple(t[c] for c in cols) for t in trades],
    )
    con.close()


def insert_daily_stats(stats: list[dict]):
    if not stats:
        return
    con = get_connection()
    cols = list(stats[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_str = ", ".join(cols)
    con.executemany(
        f"INSERT OR REPLACE INTO daily_stats ({col_str}) VALUES ({placeholders})",
        [tuple(s[c] for c in cols) for s in stats],
    )
    con.close()


def insert_simulations(sims: list[dict]):
    if not sims:
        return
    con = get_connection()
    cols = list(sims[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_str = ", ".join(cols)
    con.executemany(
        f"INSERT OR REPLACE INTO simulations ({col_str}) VALUES ({placeholders})",
        [tuple(s[c] for c in cols) for s in sims],
    )
    con.close()


def get_all_trades() -> list[dict]:
    con = get_connection(read_only=True)
    result = con.execute("SELECT * FROM trades ORDER BY timestamp_entry").fetchdf()
    con.close()
    return result.to_dict("records") if len(result) > 0 else []


def get_daily_stats() -> list[dict]:
    con = get_connection(read_only=True)
    result = con.execute("SELECT * FROM daily_stats ORDER BY session_date").fetchdf()
    con.close()
    return result.to_dict("records") if len(result) > 0 else []


def get_simulations() -> list[dict]:
    con = get_connection(read_only=True)
    result = con.execute("SELECT * FROM simulations ORDER BY start_date").fetchdf()
    con.close()
    return result.to_dict("records") if len(result) > 0 else []
