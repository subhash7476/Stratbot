"""
Database Writer Classes
-----------------------
Write interfaces for each data domain with ownership enforcement.
Updated for partitioned daily DuckDB architecture.
"""

import json
import logging
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from collections import defaultdict

from core.database.manager import DatabaseManager
from core.database import schema

logger = logging.getLogger(__name__)

def _to_str(val):
    """Convert enums, pandas Timestamps, and other non-primitive types to SQLite-safe values."""
    if val is None:
        return None
    if hasattr(val, 'isoformat'):   # datetime / pd.Timestamp (check before .value — pd.Timestamp has both)
        return str(val)
    if hasattr(val, 'value'):       # Enum
        return val.value
    return val

class MarketDataWriter:
    """
    Write operations for market data.
    Only the Ingestor process or manual data fetchers should use this class.
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager()

    def get_exchange_from_key(self, instrument_key: str) -> str:
        """Derive exchange folder name from instrument key."""
        segment = instrument_key.split('|')[0].upper()
        if segment.startswith('NSE'): return 'nse'
        if segment.startswith('MCX'): return 'mcx'
        if segment.startswith('BSE'): return 'bse'
        return segment.lower()

    def insert_candles_batch(
        self,
        symbol: str,
        timeframe: str,
        candles: List[Dict[str, Any]],
    ) -> int:
        """
        Batch insert OHLCV candles into partitioned files.
        Args:
            symbol: Instrument key
            timeframe: e.g. '1m'
            candles: List of dicts with [timestamp, open, high, low, close, volume]
        """
        if not candles:
            return 0

        exchange = self.get_exchange_from_key(symbol)
        inserted = 0
        
        # Group by date
        by_date = defaultdict(list)
        for c in candles:
            ts = c['timestamp']
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            by_date[ts.date()].append({**c, 'ts_obj': ts})

        today = date.today()

        for d, daily_candles in by_date.items():
            try:
                if d >= today:
                    with self.db.live_buffer_writer() as conns:
                        conn = conns['candles']
                        conn.execute(schema.MARKET_CANDLES_SCHEMA)
                        inserted += self._execute_insert(conn, symbol, timeframe, daily_candles)
                else:
                    with self.db.historical_writer(exchange, 'candles', timeframe, d) as conn:
                        conn.execute(schema.MARKET_CANDLES_SCHEMA)
                        inserted += self._execute_insert(conn, symbol, timeframe, daily_candles)
            except Exception as e:
                logger.error(f"Failed to insert candles for {symbol} on {d}: {e}")

        return inserted

    def _execute_insert(self, conn, symbol, timeframe, candles):
        count = 0
        for c in candles:
            conn.execute("""
                INSERT INTO candles 
                (symbol, timeframe, timestamp, open, high, low, close, volume, is_synthetic)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, FALSE)
                ON CONFLICT (symbol, timeframe, timestamp) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    is_synthetic = FALSE
            """, [
                symbol, timeframe, c['ts_obj'], 
                c['open'], c['high'], c['low'], c['close'], int(c['volume'])
            ])
            count += 1
        return count

    def update_websocket_status(self, status: str, pid: int) -> None:
        """Update WebSocket connection status in config DB."""
        with self.db.config_writer() as conn:
            conn.execute(
                """
                INSERT INTO websocket_status (key, status, updated_at, pid)
                VALUES ('singleton', ?, ?, ?)
                ON CONFLICT (key) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    pid = excluded.pid
                """,
                [status, datetime.now(), pid],
            )


class TradingWriter:
    """
    Write operations for trades and signals in SQLite.
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager()

    def save_trade(self, trade) -> None:
        """Persist a trade record."""
        with self.db.trading_writer() as conn:
            conn.execute(
                """
                INSERT INTO trades
                (trade_id, signal_id, timestamp, symbol, side,
                 quantity, entry_price, exit_price, pnl, fees, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    getattr(trade, 'trade_id', None),
                    getattr(trade, 'signal_id', None),
                    getattr(trade, 'timestamp', datetime.now()),
                    getattr(trade, 'symbol', ''),
                    getattr(trade, 'side', ''),
                    getattr(trade, 'quantity', 0),
                    getattr(trade, 'entry_price', 0.0),
                    getattr(trade, 'exit_price', 0.0),
                    getattr(trade, 'pnl', 0.0),
                    getattr(trade, 'fees', 0.0),
                    json.dumps(getattr(trade, 'metadata', {}))
                ],
            )

    def save_signal(self, signal) -> None:
        """Persist a signal record."""
        with self.db.signals_writer() as conn:
            conn.execute(
                """
                INSERT INTO signals
                (signal_id, strategy_id, symbol, signal_type, confidence, bar_ts, status)
                VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
                """,
                [
                    getattr(signal, 'signal_id', None),
                    getattr(signal, 'strategy_id', None),
                    getattr(signal, 'symbol', ''),
                    _to_str(getattr(signal, 'signal_type', '')),
                    float(getattr(signal, 'confidence', 0.0)),
                    _to_str(getattr(signal, 'timestamp', None))
                ],
            )


class AnalyticsWriter:
    """
    Write operations for analytics data in SQLite.
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager()

    _INSIGHT_UPSERT_SQL = """
        INSERT INTO confluence_insights
        (timestamp, symbol, bias, confidence, agreement_level, indicator_states, insight_signal)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (timestamp, symbol) DO UPDATE SET
            bias = EXCLUDED.bias,
            confidence = EXCLUDED.confidence,
            agreement_level = EXCLUDED.agreement_level,
            indicator_states = EXCLUDED.indicator_states,
            insight_signal = EXCLUDED.insight_signal
    """

    @staticmethod
    def _serialize_insight(insight) -> tuple:
        """Convert a ConfluenceInsight to a tuple of SQLite-safe values."""
        return (
            str(insight.timestamp) if hasattr(insight.timestamp, 'isoformat') else insight.timestamp,
            insight.symbol,
            insight.bias.value if hasattr(insight.bias, 'value') else str(insight.bias),
            float(insight.confidence_score),
            float(getattr(insight, 'agreement_level', 0.0)),
            json.dumps(
                [ir.__dict__ for ir in insight.indicator_results],
                default=lambda o: o.value if hasattr(o, 'value') else str(o)
            ) if hasattr(insight, 'indicator_results') else "[]",
            insight.signal.value if hasattr(insight.signal, 'value') else str(insight.signal),
        )

    def save_insight(self, insight) -> None:
        """Save a single confluence insight."""
        with self.db.signals_writer() as conn:
            conn.execute(self._INSIGHT_UPSERT_SQL, self._serialize_insight(insight))

    def save_insights_batch(self, insights: list) -> int:
        """
        Save many insights in a single transaction.
        One lock acquire, one connection, one executemany, one commit.
        ~100x faster than individual save_insight() calls.
        """
        if not insights:
            return 0
        rows = [self._serialize_insight(i) for i in insights]
        with self.db.signals_writer() as conn:
            conn.executemany(self._INSIGHT_UPSERT_SQL, rows)
        return len(rows)

    def save_regime_snapshot(self, snapshot) -> None:
        """Save a market regime snapshot."""
        with self.db.signals_writer() as conn:
            conn.execute(
                """
                INSERT INTO regime_insights
                (symbol, timestamp, regime, momentum_bias, trend_strength, volatility_level, persistence_score)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol, timestamp) DO UPDATE SET
                    regime = EXCLUDED.regime,
                    momentum_bias = EXCLUDED.momentum_bias,
                    trend_strength = EXCLUDED.trend_strength,
                    volatility_level = EXCLUDED.volatility_level,
                    persistence_score = EXCLUDED.persistence_score
                """,
                [
                    _to_str(snapshot.symbol),
                    _to_str(snapshot.timestamp),
                    _to_str(snapshot.regime),
                    _to_str(snapshot.momentum_bias),
                    float(snapshot.trend_strength),
                    _to_str(snapshot.volatility_level),
                    float(snapshot.persistence_score)
                ],
            )
