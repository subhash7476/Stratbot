# core/signal_manager.py
"""
Unified Signal Manager
======================
Central system for managing trading signals from multiple strategies.

Architecture:
- Multiple strategy pages (Squeeze, EHMA, VCB, etc.) write signals here
- Page 13 (Options Analyzer) reads all signals and generates options
- Signals stored in DuckDB with strategy signature
- Supports real-time updates and historical tracking

Author: Trading Bot Pro
Version: 1.0
Date: 2026-01-17
"""

from typing import List, Dict, Optional, Literal
from dataclasses import dataclass, asdict
from datetime import datetime, date
import pandas as pd
import logging

from core.database import get_db

logger = logging.getLogger(__name__)


@dataclass
class UnifiedSignal:
    """
    Universal signal format for all strategies

    This replaces individual signal formats and provides a common interface
    for all strategy pages to write signals that can be consumed by the
    options analyzer.
    """
    # Identification
    signal_id: str  # Unique ID: {strategy}_{symbol}_{timestamp}
    strategy: str  # SQUEEZE_15M, EHMA_MTF, VCB, etc.
    symbol: str
    instrument_key: str

    # Signal Details
    signal_type: Literal['LONG', 'SHORT']
    timeframe: str  # 5minute, 15minute, 60minute, etc.
    timestamp: datetime

    # Price Levels
    entry_price: float
    sl_price: float
    tp_price: float

    # Signal Quality
    score: float  # 0-100 or strategy-specific (e.g., 4-5 for Squeeze)
    confidence: float  # 0-100 normalized confidence

    # Strategy-Specific Metadata
    reasons: str  # JSON string or comma-separated
    metadata: str = ""  # Additional strategy data (JSON)

    # Status
    status: Literal['ACTIVE', 'FILLED', 'CANCELLED', 'EXPIRED'] = 'ACTIVE'
    created_at: datetime = None
    updated_at: datetime = None

    def __post_init__(self):
        """Auto-generate timestamps if not provided"""
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()

    @property
    def risk_points(self) -> float:
        """Calculate risk in points"""
        return abs(self.entry_price - self.sl_price)

    @property
    def reward_points(self) -> float:
        """Calculate reward in points"""
        return abs(self.tp_price - self.entry_price)

    @property
    def risk_reward_ratio(self) -> float:
        """Calculate R:R ratio"""
        risk = self.risk_points
        return self.reward_points / risk if risk > 0 else 0


class SignalManager:
    """
    Central manager for all trading signals

    Features:
    - Write signals from any strategy
    - Read active signals for options analysis
    - Update signal status (filled, cancelled, expired)
    - Historical signal tracking
    - Multi-strategy filtering

    Usage:
        >>> manager = SignalManager()
        >>>
        >>> # Write signal from Squeeze strategy
        >>> signal = UnifiedSignal(
        ...     signal_id="SQUEEZE_15M_RELIANCE_20260117_1315",
        ...     strategy="SQUEEZE_15M",
        ...     symbol="RELIANCE",
        ...     signal_type="LONG",
        ...     entry_price=2450,
        ...     sl_price=2430,
        ...     tp_price=2490,
        ...     score=5,
        ...     confidence=100,
        ...     ...
        ... )
        >>> manager.write_signal(signal)
        >>>
        >>> # Read all active signals
        >>> active = manager.get_active_signals()
        >>>
        >>> # Read signals from specific strategy
        >>> squeeze_signals = manager.get_active_signals(strategy="SQUEEZE_15M")
    """

    def __init__(self):
        self.db = get_db()
        self._ensure_table()

    def _ensure_table(self):
        """Create unified signals table if not exists"""
        self.db.con.execute("""
            CREATE TABLE IF NOT EXISTS unified_signals (
                signal_id VARCHAR PRIMARY KEY,
                strategy VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                instrument_key VARCHAR NOT NULL,

                signal_type VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                timestamp TIMESTAMP NOT NULL,

                entry_price DECIMAL(12,2) NOT NULL,
                sl_price DECIMAL(12,2) NOT NULL,
                tp_price DECIMAL(12,2) NOT NULL,

                score DECIMAL(6,2),
                confidence DECIMAL(6,2),

                reasons TEXT,
                metadata TEXT,

                status VARCHAR DEFAULT 'ACTIVE',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create indexes for fast queries
        self.db.con.execute("""
            CREATE INDEX IF NOT EXISTS idx_unified_signals_status
            ON unified_signals(status)
        """)

        self.db.con.execute("""
            CREATE INDEX IF NOT EXISTS idx_unified_signals_strategy
            ON unified_signals(strategy, status)
        """)

        self.db.con.execute("""
            CREATE INDEX IF NOT EXISTS idx_unified_signals_timestamp
            ON unified_signals(timestamp DESC)
        """)

        logger.info("✅ Unified signals table ready")

    def write_signal(self, signal: UnifiedSignal) -> bool:
        """
        Write a signal to database

        Args:
            signal: UnifiedSignal object

        Returns:
            True if successful, False otherwise

        Example:
            >>> manager = SignalManager()
            >>> success = manager.write_signal(my_signal)
        """
        try:
            self.db.con.execute("""
                INSERT INTO unified_signals (
                    signal_id, strategy, symbol, instrument_key,
                    signal_type, timeframe, timestamp,
                    entry_price, sl_price, tp_price,
                    score, confidence, reasons, metadata,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (signal_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at
            """, [
                signal.signal_id,
                signal.strategy,
                signal.symbol,
                signal.instrument_key,
                signal.signal_type,
                signal.timeframe,
                signal.timestamp,
                signal.entry_price,
                signal.sl_price,
                signal.tp_price,
                signal.score,
                signal.confidence,
                signal.reasons,
                signal.metadata,
                signal.status,
                signal.created_at,
                signal.updated_at
            ])

            self.db.con.commit()
            logger.info(f"✅ Signal written: {signal.signal_id}")
            return True

        except Exception as e:
            logger.error(f"❌ Error writing signal {signal.signal_id}: {e}")
            return False

    def write_signals_batch(self, signals: List[UnifiedSignal]) -> int:
        """
        Write multiple signals in batch

        Args:
            signals: List of UnifiedSignal objects

        Returns:
            Number of signals successfully written
        """
        success_count = 0
        for signal in signals:
            if self.write_signal(signal):
                success_count += 1

        logger.info(f"✅ Batch write: {success_count}/{len(signals)} signals")
        return success_count

    def get_active_signals(
        self,
        strategy: Optional[str] = None,
        symbol: Optional[str] = None,
        min_score: Optional[float] = None,
        min_confidence: Optional[float] = None
    ) -> pd.DataFrame:
        """
        Get all active signals with optional filters

        Args:
            strategy: Filter by strategy (e.g., "SQUEEZE_15M")
            symbol: Filter by symbol (e.g., "RELIANCE")
            min_score: Minimum score threshold
            min_confidence: Minimum confidence threshold

        Returns:
            DataFrame with active signals

        Example:
            >>> # Get all active signals
            >>> df = manager.get_active_signals()
            >>>
            >>> # Get only Squeeze signals
            >>> df = manager.get_active_signals(strategy="SQUEEZE_15M")
            >>>
            >>> # Get high-confidence signals
            >>> df = manager.get_active_signals(min_confidence=80)
        """
        query = "SELECT * FROM unified_signals WHERE status = 'ACTIVE'"
        params = []

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())

        if min_score is not None:
            query += " AND score >= ?"
            params.append(min_score)

        if min_confidence is not None:
            query += " AND confidence >= ?"
            params.append(min_confidence)

        query += " ORDER BY timestamp DESC"

        try:
            df = self.db.con.execute(query, params).df()
            logger.info(f"📊 Retrieved {len(df)} active signals")
            return df
        except Exception as e:
            logger.error(f"❌ Error retrieving signals: {e}")
            return pd.DataFrame()

    def get_signals_for_options(
        self,
        min_score: float = 4.0,
        strategies: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """
        Get signals ready for options analysis

        This is the main method used by page 13 (Options Analyzer)

        Args:
            min_score: Minimum score (default: 4.0 for Score 4+)
            strategies: List of strategies to include (None = all)

        Returns:
            DataFrame formatted for options analyzer
        """
        df = self.get_active_signals(min_score=min_score)

        if df.empty:
            return df

        # Filter by strategies if specified
        if strategies:
            df = df[df['strategy'].isin(strategies)]

        # Rename columns to match options analyzer expectations
        df_formatted = df.rename(columns={
            'symbol': 'Symbol',
            'signal_type': 'Signal',
            'entry_price': 'Entry',
            'sl_price': 'SL',
            'tp_price': 'TP',
            'score': 'Score',
            'instrument_key': 'Instrument Key',
            'reasons': 'Reasons',
            'timestamp': 'Time'
        })

        # Format Time column
        if 'Time' in df_formatted.columns:
            df_formatted['Time'] = pd.to_datetime(df_formatted['Time']).dt.strftime('%H:%M')

        return df_formatted[[
            'Symbol', 'Signal', 'Entry', 'SL', 'TP', 'Score',
            'Instrument Key', 'Reasons', 'Time', 'strategy', 'confidence'
        ]]

    def update_signal_status(
        self,
        signal_id: str,
        status: Literal['ACTIVE', 'FILLED', 'CANCELLED', 'EXPIRED']
    ) -> bool:
        """
        Update signal status

        Args:
            signal_id: Signal ID to update
            status: New status

        Returns:
            True if successful
        """
        try:
            self.db.con.execute("""
                UPDATE unified_signals
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE signal_id = ?
            """, [status, signal_id])

            self.db.con.commit()
            logger.info(f"✅ Signal {signal_id} updated to {status}")
            return True

        except Exception as e:
            logger.error(f"❌ Error updating signal {signal_id}: {e}")
            return False

    def expire_old_signals(self, hours: int = 24) -> int:
        """
        Mark signals older than X hours as EXPIRED

        Args:
            hours: Age threshold in hours

        Returns:
            Number of signals expired
        """
        try:
            result = self.db.con.execute("""
                UPDATE unified_signals
                SET status = 'EXPIRED', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'ACTIVE'
                  AND timestamp < CURRENT_TIMESTAMP - INTERVAL ? HOUR
            """, [hours])

            self.db.con.commit()
            count = result.fetchone()[0] if result else 0
            logger.info(f"✅ Expired {count} old signals (>{hours}h)")
            return count

        except Exception as e:
            logger.error(f"❌ Error expiring signals: {e}")
            return 0

    def check_tp_sl_hit(self, live_prices: Dict[str, float] = None) -> Dict[str, List]:
        """
        Check if any active signals have hit TP or SL based on live prices.

        Args:
            live_prices: Dict mapping instrument_key to current price.
                        If None, will fetch from Upstox API.

        Returns:
            Dict with 'tp_hit': [...], 'sl_hit': [...], 'updated': int
        """
        result = {'tp_hit': [], 'sl_hit': [], 'updated': 0}

        try:
            # Get all active signals
            active_signals = self.db.con.execute("""
                SELECT signal_id, instrument_key, symbol, signal_type,
                       entry_price, sl_price, tp_price
                FROM unified_signals
                WHERE status = 'ACTIVE'
            """).fetchall()

            if not active_signals:
                return result

            # Fetch live prices if not provided
            if live_prices is None:
                live_prices = self._fetch_live_prices(
                    [sig[1] for sig in active_signals]
                )

            # Check each signal
            for sig in active_signals:
                signal_id, instrument_key, symbol, signal_type, entry, sl, tp = sig

                current_price = live_prices.get(instrument_key)
                if current_price is None:
                    continue

                # Check TP/SL based on signal type
                if signal_type == 'LONG':
                    if current_price >= tp:
                        result['tp_hit'].append({
                            'signal_id': signal_id,
                            'symbol': symbol,
                            'entry': entry,
                            'tp': tp,
                            'current': current_price
                        })
                        self.update_signal_status(signal_id, 'FILLED')
                        result['updated'] += 1
                    elif current_price <= sl:
                        result['sl_hit'].append({
                            'signal_id': signal_id,
                            'symbol': symbol,
                            'entry': entry,
                            'sl': sl,
                            'current': current_price
                        })
                        self.update_signal_status(signal_id, 'CANCELLED')
                        result['updated'] += 1

                elif signal_type == 'SHORT':
                    if current_price <= tp:
                        result['tp_hit'].append({
                            'signal_id': signal_id,
                            'symbol': symbol,
                            'entry': entry,
                            'tp': tp,
                            'current': current_price
                        })
                        self.update_signal_status(signal_id, 'FILLED')
                        result['updated'] += 1
                    elif current_price >= sl:
                        result['sl_hit'].append({
                            'signal_id': signal_id,
                            'symbol': symbol,
                            'entry': entry,
                            'sl': sl,
                            'current': current_price
                        })
                        self.update_signal_status(signal_id, 'CANCELLED')
                        result['updated'] += 1

            logger.info(f"✅ TP/SL check: {len(result['tp_hit'])} TP hit, {len(result['sl_hit'])} SL hit")
            return result

        except Exception as e:
            logger.error(f"❌ Error checking TP/SL: {e}")
            return result

    def _fetch_live_prices(self, instrument_keys: List[str]) -> Dict[str, float]:
        """
        Fetch live prices from Upstox API for given instruments.

        Args:
            instrument_keys: List of instrument keys

        Returns:
            Dict mapping instrument_key to current LTP
        """
        import requests
        from core.config import get_access_token

        prices = {}

        try:
            token = get_access_token()
            if not token:
                logger.warning("No access token available for live price fetch")
                return prices

            # Upstox allows batch quotes
            # Process in batches of 50
            batch_size = 50
            for i in range(0, len(instrument_keys), batch_size):
                batch = instrument_keys[i:i + batch_size]
                keys_param = ",".join(batch)

                url = f"https://api.upstox.com/v2/market-quote/ltp?instrument_key={keys_param}"
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json"
                }

                resp = requests.get(url, headers=headers, timeout=10)

                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "success":
                        for key, quote in data.get("data", {}).items():
                            prices[key] = quote.get("last_price", 0)

        except Exception as e:
            logger.error(f"❌ Error fetching live prices: {e}")

        return prices

    def expire_signal(self, signal_id: str) -> bool:
        """
        Manually expire a specific signal.

        Args:
            signal_id: Signal ID to expire

        Returns:
            True if successful
        """
        return self.update_signal_status(signal_id, 'EXPIRED')

    def expire_signals_by_symbol(self, symbol: str) -> int:
        """
        Expire all active signals for a specific symbol.

        Args:
            symbol: Trading symbol (e.g., RELIANCE)

        Returns:
            Number of signals expired
        """
        try:
            self.db.con.execute("""
                UPDATE unified_signals
                SET status = 'EXPIRED', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'ACTIVE' AND symbol = ?
            """, [symbol.upper()])

            self.db.con.commit()

            # Get count of expired signals
            result = self.db.con.execute("""
                SELECT changes()
            """).fetchone()

            count = result[0] if result else 0
            logger.info(f"✅ Expired {count} signals for {symbol}")
            return count

        except Exception as e:
            logger.error(f"❌ Error expiring signals for {symbol}: {e}")
            return 0

    def get_signal_stats(self) -> Dict:
        """
        Get signal statistics

        Returns:
            Dictionary with counts by strategy and status
        """
        try:
            stats = {}

            # Total by status
            result = self.db.con.execute("""
                SELECT status, COUNT(*) as count
                FROM unified_signals
                GROUP BY status
            """).fetchall()

            stats['by_status'] = {row[0]: row[1] for row in result}

            # Active by strategy
            result = self.db.con.execute("""
                SELECT strategy, COUNT(*) as count
                FROM unified_signals
                WHERE status = 'ACTIVE'
                GROUP BY strategy
            """).fetchall()

            stats['active_by_strategy'] = {row[0]: row[1] for row in result}

            return stats

        except Exception as e:
            logger.error(f"❌ Error getting stats: {e}")
            return {}

    def clear_all_signals(self) -> bool:
        """
        Clear all signals (use with caution!)

        Returns:
            True if successful
        """
        try:
            self.db.con.execute("DELETE FROM unified_signals")
            self.db.con.commit()
            logger.warning("⚠️ All signals cleared!")
            return True
        except Exception as e:
            logger.error(f"❌ Error clearing signals: {e}")
            return False


# Helper function to generate signal ID
def generate_signal_id(strategy: str, symbol: str, timestamp: datetime) -> str:
    """
    Generate unique signal ID

    Format: {STRATEGY}_{SYMBOL}_{YYYYMMDD}_{HHMM}

    Example:
        >>> generate_signal_id("SQUEEZE_15M", "RELIANCE", datetime.now())
        'SQUEEZE_15M_RELIANCE_20260117_1315'
    """
    ts_str = timestamp.strftime("%Y%m%d_%H%M")
    return f"{strategy}_{symbol.upper()}_{ts_str}"


# Convenience functions for strategy pages

def write_squeeze_signal(
    symbol: str,
    instrument_key: str,
    signal_type: str,
    entry: float,
    sl: float,
    tp: float,
    score: float,
    reasons: str,
    timestamp: datetime = None
) -> bool:
    """
    Quick write for Squeeze strategy signals

    Example:
        >>> write_squeeze_signal(
        ...     symbol="RELIANCE",
        ...     instrument_key="NSE_EQ|INE002A01018",
        ...     signal_type="LONG",
        ...     entry=2450,
        ...     sl=2430,
        ...     tp=2490,
        ...     score=5,
        ...     reasons="SuperTrend bullish, WaveTrend cross"
        ... )
    """
    if timestamp is None:
        timestamp = datetime.now()

    signal_id = generate_signal_id("SQUEEZE_15M", symbol, timestamp)

    signal = UnifiedSignal(
        signal_id=signal_id,
        strategy="SQUEEZE_15M",
        symbol=symbol.upper(),
        instrument_key=instrument_key,
        signal_type=signal_type.upper(),
        timeframe="15minute",
        timestamp=timestamp,
        entry_price=entry,
        sl_price=sl,
        tp_price=tp,
        score=score,
        confidence=score * 20,  # Convert 5-point scale to 0-100
        reasons=reasons
    )

    manager = SignalManager()
    return manager.write_signal(signal)


if __name__ == "__main__":
    # Test the signal manager
    print("=== Signal Manager Test ===\n")

    manager = SignalManager()

    # Test signal
    test_signal = UnifiedSignal(
        signal_id=generate_signal_id("SQUEEZE_15M", "RELIANCE", datetime.now()),
        strategy="SQUEEZE_15M",
        symbol="RELIANCE",
        instrument_key="NSE_EQ|INE002A01018",
        signal_type="LONG",
        timeframe="15minute",
        timestamp=datetime.now(),
        entry_price=2450.0,
        sl_price=2430.0,
        tp_price=2490.0,
        score=5.0,
        confidence=100.0,
        reasons="SuperTrend bullish, WaveTrend cross, Recent squeeze"
    )

    # Write signal
    success = manager.write_signal(test_signal)
    print(f"Write signal: {'✅ Success' if success else '❌ Failed'}")

    # Read active signals
    active = manager.get_active_signals()
    print(f"\nActive signals: {len(active)}")

    if not active.empty:
        print(active[['symbol', 'signal_type', 'score', 'confidence']])

    # Get stats
    stats = manager.get_signal_stats()
    print(f"\nStats: {stats}")
