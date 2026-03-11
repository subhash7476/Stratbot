from contextlib import contextmanager
from datetime import datetime, timedelta

import duckdb

from core.database.queries import MarketDataQuery


class _StubDb:
    def __init__(self, conn):
        self._conn = conn

    @contextmanager
    def live_buffer_reader(self):
        yield {"candles": self._conn}

    @contextmanager
    def historical_reader(self, exchange, data_type, timeframe, dt):
        raise FileNotFoundError("no historical file")


def _build_candles_conn():
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE candles (
            symbol TEXT,
            timeframe TEXT,
            timestamp TIMESTAMP,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            is_synthetic BOOLEAN
        )
        """
    )
    now = datetime.now().replace(microsecond=0)
    conn.execute(
        """
        INSERT INTO candles (symbol, timeframe, timestamp, open, high, low, close, volume, is_synthetic)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ["MCX_FO|USDINR", "1m", now, 83.1, 83.4, 83.0, 83.2, 1000, False],
    )
    return conn, now


def test_get_candles_falls_back_when_instrument_key_missing():
    conn, now = _build_candles_conn()
    query = MarketDataQuery(_StubDb(conn))

    # Force path that initially attempts instrument_key and must recover.
    query._has_column = lambda *_args, **_kwargs: True  # noqa: SLF001

    df = query.get_candles(
        symbol="MCX_FO|USDINR",
        exchange="mcx",
        timeframe="1m",
        start=now - timedelta(minutes=5),
        end=now + timedelta(minutes=1),
        limit=1,
    )

    assert not df.empty
    assert str(df.iloc[0]["symbol"]) == "MCX_FO|USDINR"


def test_get_latest_bar_falls_back_when_instrument_key_missing():
    conn, _ = _build_candles_conn()
    query = MarketDataQuery(_StubDb(conn))

    # Force path that initially attempts instrument_key and must recover.
    query._has_column = lambda *_args, **_kwargs: True  # noqa: SLF001

    row = query.get_latest_bar("MCX_FO|USDINR", exchange="mcx", timeframe="1m")

    assert row is not None
    assert row.get("symbol") == "MCX_FO|USDINR"
