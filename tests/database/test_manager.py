"""
Tests for DatabaseManager and related components.
"""

import os
import pytest
import tempfile
from datetime import datetime
from pathlib import Path

import duckdb

from core.database.manager import DatabaseManager, DatabaseDomain
from core.database.schema import BOOTSTRAP_STATEMENTS
from core.database.queries import MarketDataQuery, TradingQuery, AnalyticsQuery
from core.database.writers import MarketDataWriter, TradingWriter, AnalyticsWriter
from core.database.legacy_adapter import db_cursor, get_connection


@pytest.fixture
def test_db_path(tmp_path):
    """Create a temporary database path."""
    db_path = str(tmp_path / "test_trading.duckdb")
    yield db_path
    # Cleanup is handled by tmp_path fixture


@pytest.fixture
def initialized_db(test_db_path):
    """Create and initialize a test database."""
    conn = duckdb.connect(test_db_path)
    for stmt in BOOTSTRAP_STATEMENTS:
        conn.execute(stmt)
    conn.close()
    return test_db_path


@pytest.fixture
def db_manager(initialized_db):
    """Create a DatabaseManager with test database."""
    # Reset singleton for test isolation
    DatabaseManager.reset_instance()
    manager = DatabaseManager(initialized_db)
    yield manager
    DatabaseManager.reset_instance()


class TestDatabaseManager:
    """Tests for DatabaseManager singleton."""

    def test_singleton_pattern(self, initialized_db):
        """DatabaseManager should be a singleton."""
        DatabaseManager.reset_instance()

        manager1 = DatabaseManager(initialized_db)
        manager2 = DatabaseManager(initialized_db)

        assert manager1 is manager2
        DatabaseManager.reset_instance()

    def test_read_connection(self, db_manager, initialized_db):
        """Read context should return a working connection."""
        with db_manager.read() as conn:
            result = conn.execute("SELECT 1").fetchone()
            assert result == (1,)

    def test_write_connection(self, db_manager):
        """Write context should allow INSERT operations."""
        with db_manager.write(DatabaseDomain.CONFIG) as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, roles) VALUES (?, ?, ?)",
                ["test_user", "hash123", "viewer"],
            )

        # Verify the insert worked
        with db_manager.read() as conn:
            result = conn.execute(
                "SELECT username FROM users WHERE username = ?", ["test_user"]
            ).fetchone()
            assert result == ("test_user",)

    def test_transaction_commit(self, db_manager):
        """Transaction should commit on success."""
        with db_manager.transaction(DatabaseDomain.CONFIG) as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, roles) VALUES (?, ?, ?)",
                ["tx_user", "hash", "admin"],
            )

        with db_manager.read() as conn:
            result = conn.execute(
                "SELECT username FROM users WHERE username = ?", ["tx_user"]
            ).fetchone()
            assert result == ("tx_user",)

    def test_transaction_rollback(self, db_manager):
        """Transaction should rollback on exception."""
        try:
            with db_manager.transaction(DatabaseDomain.CONFIG) as conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash, roles) VALUES (?, ?, ?)",
                    ["rollback_user", "hash", "admin"],
                )
                raise ValueError("Simulated error")
        except ValueError:
            pass

        with db_manager.read() as conn:
            result = conn.execute(
                "SELECT username FROM users WHERE username = ?", ["rollback_user"]
            ).fetchone()
            assert result is None

    def test_isolated_connection(self, db_manager, tmp_path):
        """Isolated connection should use different database."""
        isolated_path = str(tmp_path / "isolated.duckdb")

        with db_manager.isolated_connection(isolated_path) as conn:
            conn.execute("CREATE TABLE test_table (id INTEGER)")
            conn.execute("INSERT INTO test_table VALUES (42)")
            result = conn.execute("SELECT id FROM test_table").fetchone()
            assert result == (42,)


class TestLegacyAdapter:
    """Tests for backward-compatible db_cursor."""

    def test_db_cursor_read_only(self, db_manager, initialized_db):
        """db_cursor with read_only=True should work."""
        # Set environment variable for the default path
        os.environ["TRADING_DB_PATH"] = initialized_db

        with db_cursor(read_only=True) as conn:
            result = conn.execute("SELECT COUNT(*) FROM roles").fetchone()
            assert result[0] >= 2  # admin and viewer

    def test_db_cursor_write(self, db_manager, initialized_db):
        """db_cursor without read_only should allow writes."""
        os.environ["TRADING_DB_PATH"] = initialized_db

        with db_cursor() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, roles) VALUES (?, ?, ?)",
                ["legacy_user", "hash", "viewer"],
            )

        with db_cursor(read_only=True) as conn:
            result = conn.execute(
                "SELECT username FROM users WHERE username = ?", ["legacy_user"]
            ).fetchone()
            assert result == ("legacy_user",)

    def test_db_cursor_custom_path(self, tmp_path):
        """db_cursor with custom path should use that database."""
        custom_path = str(tmp_path / "custom.duckdb")

        with db_cursor(db_path=custom_path) as conn:
            conn.execute("CREATE TABLE custom_test (val INTEGER)")
            conn.execute("INSERT INTO custom_test VALUES (123)")
            result = conn.execute("SELECT val FROM custom_test").fetchone()
            assert result == (123,)


class TestMarketDataQuery:
    """Tests for MarketDataQuery."""

    def test_get_ohlcv_empty(self, db_manager):
        """get_ohlcv should return empty DataFrame when no data."""
        query = MarketDataQuery(db_manager)
        df = query.get_ohlcv("TEST|SYMBOL")
        assert len(df) == 0

    def test_get_ohlcv_with_data(self, db_manager):
        """get_ohlcv should return data when present."""
        # Insert test data
        with db_manager.write(DatabaseDomain.MARKET_DATA) as conn:
            conn.execute(
                """
                INSERT INTO candles (instrument_key, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ["TEST|SYMBOL", datetime(2024, 1, 1, 9, 15), 100.0, 101.0, 99.0, 100.5, 1000],
            )

        query = MarketDataQuery(db_manager)
        df = query.get_ohlcv("TEST|SYMBOL")

        assert len(df) == 1
        assert df.iloc[0]["close"] == 100.5

    def test_get_latest_bar(self, db_manager):
        """get_latest_bar should return most recent bar."""
        with db_manager.write(DatabaseDomain.MARKET_DATA) as conn:
            conn.execute(
                """
                INSERT INTO candles (instrument_key, timestamp, open, high, low, close, volume)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?),
                    (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    "TEST|SYMBOL", datetime(2024, 1, 1, 9, 15), 100.0, 101.0, 99.0, 100.5, 1000,
                    "TEST|SYMBOL", datetime(2024, 1, 1, 9, 16), 100.5, 102.0, 100.0, 101.5, 1200,
                ],
            )

        query = MarketDataQuery(db_manager)
        bar = query.get_latest_bar("TEST|SYMBOL")

        assert bar is not None
        assert bar["close"] == 101.5


class TestMarketDataWriter:
    """Tests for MarketDataWriter."""

    def test_insert_candle(self, db_manager):
        """insert_candle should insert a new bar."""
        writer = MarketDataWriter(db_manager)
        result = writer.insert_candle(
            instrument_key="TEST|SYMBOL",
            timestamp=datetime(2024, 1, 1, 9, 15),
            open_=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
        )

        assert result is True

        # Verify
        query = MarketDataQuery(db_manager)
        bar = query.get_latest_bar("TEST|SYMBOL")
        assert bar is not None
        assert bar["close"] == 100.5

    def test_insert_candle_deduplication(self, db_manager):
        """insert_candle should skip duplicates when deduplicate=True."""
        writer = MarketDataWriter(db_manager)
        ts = datetime(2024, 1, 1, 9, 15)

        # First insert
        result1 = writer.insert_candle(
            instrument_key="TEST|SYMBOL",
            timestamp=ts,
            open_=100.0, high=101.0, low=99.0, close=100.5, volume=1000,
        )

        # Second insert (duplicate)
        result2 = writer.insert_candle(
            instrument_key="TEST|SYMBOL",
            timestamp=ts,
            open_=200.0, high=201.0, low=199.0, close=200.5, volume=2000,
        )

        assert result1 is True
        assert result2 is False

        # Verify original data preserved
        query = MarketDataQuery(db_manager)
        bar = query.get_latest_bar("TEST|SYMBOL")
        assert bar["close"] == 100.5  # Original, not the duplicate


class TestTradingQuery:
    """Tests for TradingQuery."""

    def test_signal_exists_false(self, db_manager):
        """signal_exists should return False for non-existent signal."""
        query = TradingQuery(db_manager)
        assert query.signal_exists("nonexistent_signal_id") is False

    def test_signal_exists_true(self, db_manager):
        """signal_exists should return True for existing signal."""
        # Insert a trade with signal_id
        with db_manager.write(DatabaseDomain.TRADING) as conn:
            conn.execute(
                """
                INSERT INTO trades (trade_id, signal_id, timestamp, symbol, direction, quantity, price, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ["trade_1", "signal_123", datetime.now(), "TEST", "BUY", 10.0, 100.0, "FILLED"],
            )

        query = TradingQuery(db_manager)
        assert query.signal_exists("signal_123") is True


class TestSchemaBootstrap:
    """Tests for schema bootstrap statements."""

    def test_bootstrap_creates_all_tables(self, test_db_path):
        """Bootstrap should create all required tables."""
        conn = duckdb.connect(test_db_path)

        for stmt in BOOTSTRAP_STATEMENTS:
            conn.execute(stmt)

        # Check core tables exist
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
        table_names = [t[0] for t in tables]

        expected_tables = [
            "candles",
            "ohlcv_resampled",
            "trades",
            "signals",
            "confluence_insights",
            "regime_snapshots",
            "backtest_runs",
            "backtest_trades",
            "users",
            "roles",
            "fo_stocks_master",
            "websocket_status",
        ]

        for table in expected_tables:
            assert table in table_names, f"Table {table} not found"

        conn.close()

    def test_bootstrap_idempotent(self, test_db_path):
        """Bootstrap should be safe to run multiple times."""
        conn = duckdb.connect(test_db_path)

        # Run bootstrap twice
        for _ in range(2):
            for stmt in BOOTSTRAP_STATEMENTS:
                conn.execute(stmt)

        # Should still work
        result = conn.execute("SELECT COUNT(*) FROM roles").fetchone()
        assert result[0] >= 2

        conn.close()
