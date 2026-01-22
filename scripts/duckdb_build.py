import duckdb
from pathlib import Path
import sys

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "trading_bot.duckdb"


def bootstrap():
    if DB_PATH.exists():
        raise RuntimeError(
            "trading_bot.duckdb already exists. "
            "Rename or delete it before bootstrapping."
        )

    db = duckdb.connect(str(DB_PATH))
    print("Creating new DuckDB database:", DB_PATH)

    # ---------------------------
    # INSTRUMENTS
    # ---------------------------
    db.execute("""
    CREATE TABLE instruments (
        instrument_key VARCHAR PRIMARY KEY,
        trading_symbol VARCHAR,
        exchange VARCHAR,
        instrument_type VARCHAR,
        expiry DATE,
        strike DOUBLE,
        option_type VARCHAR,
        lot_size INTEGER,
        tick_size DOUBLE,
        name VARCHAR,
        last_updated TIMESTAMP
    );
    """)

    # ---------------------------
    # 1m OHLCV (GROUND TRUTH)
    # ---------------------------
    db.execute("""
    CREATE TABLE ohlcv_1m (
        instrument_key VARCHAR NOT NULL,
        timestamp TIMESTAMP NOT NULL,
        open DOUBLE NOT NULL,
        high DOUBLE NOT NULL,
        low DOUBLE NOT NULL,
        close DOUBLE NOT NULL,
        volume BIGINT NOT NULL,
        oi BIGINT,
        PRIMARY KEY (instrument_key, timestamp)
    );
    """)

    # ---------------------------
    # RESAMPLED (BATCH)
    # ---------------------------
    db.execute("""
    CREATE TABLE ohlcv_resampled (
        instrument_key VARCHAR NOT NULL,
        timeframe VARCHAR NOT NULL,
        timestamp TIMESTAMP NOT NULL,
        open DOUBLE NOT NULL,
        high DOUBLE NOT NULL,
        low DOUBLE NOT NULL,
        close DOUBLE NOT NULL,
        volume BIGINT NOT NULL,
        oi BIGINT,
        PRIMARY KEY (instrument_key, timeframe, timestamp)
    );
    """)

    # ---------------------------
    # LIVE RESAMPLED (WEBSOCKET)
    # ---------------------------
    db.execute("""
    CREATE TABLE ohlcv_resampled_live (
        instrument_key VARCHAR NOT NULL,
        timeframe VARCHAR NOT NULL,
        timestamp TIMESTAMP NOT NULL,
        open DOUBLE NOT NULL,
        high DOUBLE NOT NULL,
        low DOUBLE NOT NULL,
        close DOUBLE NOT NULL,
        volume BIGINT NOT NULL,
        oi BIGINT,
        PRIMARY KEY (instrument_key, timeframe, timestamp)
    );
    """)

    # ---------------------------
    # REGIME ANALYSIS HISTORY (FIXED)
    # ---------------------------
    db.execute("""
    CREATE TABLE regime_analysis_history (
        instrument_key VARCHAR NOT NULL,
        analysis_date TIMESTAMP NOT NULL,
        current_regime VARCHAR,
        confidence DOUBLE,
        persistence_prob DOUBLE,
        regime_duration INTEGER,
        backtest_sharpe DOUBLE,
        backtest_win_rate DOUBLE,
        total_days_analyzed INTEGER,
        PRIMARY KEY (instrument_key, analysis_date)
    );
    """)

    # ---------------------------
    # TRADABLE UNIVERSE
    # ---------------------------
    db.execute("""
    CREATE TABLE tradable_universe (
        instrument_key VARCHAR NOT NULL,
        signal_date DATE NOT NULL,
        direction VARCHAR,
        strategy VARCHAR,
        regime VARCHAR,
        confidence DOUBLE,
        persistence DOUBLE,
        sharpe DOUBLE,
        regime_maturity VARCHAR,
        option_buy_ok BOOLEAN,
        trade_permission VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (instrument_key, signal_date)
    );
    """)

    # ---------------------------
    # TRADES (LIVE / PAPER)
    # ---------------------------
    db.execute("""
    CREATE TABLE trades (
        trade_id VARCHAR PRIMARY KEY,
        instrument_key VARCHAR,
        option_instrument_key VARCHAR,
        side VARCHAR,
        qty INTEGER,
        entry_price DOUBLE,
        exit_price DOUBLE,
        pnl DOUBLE,
        status VARCHAR,
        created_at TIMESTAMP,
        closed_at TIMESTAMP
    );
    """)

    # ---------------------------
    # OPTION ALERTS
    # ---------------------------
    db.execute("""
    CREATE TABLE option_alerts (
        alert_id VARCHAR PRIMARY KEY,
        instrument_key VARCHAR,
        option_instrument_key VARCHAR,
        strategy VARCHAR,
        signal_strength DOUBLE,
        created_at TIMESTAMP
    );
    """)

    db.close()
    print("✅ Database bootstrap completed successfully.")


if __name__ == "__main__":
    bootstrap()
