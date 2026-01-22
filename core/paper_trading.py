# core/paper_trading.py
"""
Paper Trading System for Options
==================================
Manages paper trades with real-time P&L tracking using live market data.

Features:
- Create paper trades from options recommendations
- Track open positions with live LTP updates
- Square off positions and record in trade log
- Real-time P&L calculation
- Position summary with Greeks tracking

Author: Trading Bot Pro
Version: 1.0
Date: 2026-01-17
"""

from typing import List, Dict, Optional, Literal
from dataclasses import dataclass, asdict
from datetime import datetime
import pandas as pd
import logging
from core.database import get_db
from core.config import get_access_token
import requests

logger = logging.getLogger(__name__)


@dataclass
class PaperTrade:
    """Paper trade position"""
    trade_id: str  # PAPER_YYYYMMDD_HHMMSS_001
    signal_id: str  # Reference to unified_signals
    symbol: str  # Underlying symbol
    strategy: str  # SQUEEZE_15M, EHMA_MTF, etc.

    # Option details
    option_instrument_key: str
    option_type: str  # CE or PE
    strike_price: float
    expiry_date: str

    # Trade details
    side: Literal['BUY', 'SELL']  # BUY to open long position
    entry_price: float  # Premium at entry
    quantity: int  # Number of lots * lot_size
    lot_size: int

    # Entry info
    entry_time: datetime
    entry_greeks: str  # JSON string with Delta, Theta, etc.

    # Exit info (None for open positions)
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_greeks: Optional[str] = None

    # P&L
    realized_pnl: Optional[float] = None  # After square off
    unrealized_pnl: Optional[float] = None  # For open positions

    # Status
    status: Literal['OPEN', 'CLOSED'] = 'OPEN'

    # Metadata
    notes: str = ""
    created_at: datetime = None
    updated_at: datetime = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()


class PaperTradingManager:
    """
    Manages paper trading positions

    Usage:
        >>> manager = PaperTradingManager()
        >>>
        >>> # Create trade from recommendation
        >>> trade = manager.create_trade(
        ...     signal_id="SQUEEZE_15M_RELIANCE_20260117_1315",
        ...     symbol="RELIANCE",
        ...     strategy="SQUEEZE_15M",
        ...     recommendation=rec  # OptionRecommendation object
        ... )
        >>>
        >>> # Get open positions with live P&L
        >>> positions = manager.get_open_positions_with_live_pnl()
        >>>
        >>> # Square off
        >>> manager.square_off_trade(trade_id, exit_price=85.5)
    """

    def __init__(self):
        self.db = get_db()
        self._ensure_table()

    def _ensure_table(self):
        """Create paper_trades table if not exists"""
        self.db.con.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                trade_id VARCHAR PRIMARY KEY,
                signal_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                strategy VARCHAR NOT NULL,

                option_instrument_key VARCHAR NOT NULL,
                option_type VARCHAR NOT NULL,
                strike_price DECIMAL(12,2) NOT NULL,
                expiry_date VARCHAR NOT NULL,

                side VARCHAR NOT NULL,
                entry_price DECIMAL(12,2) NOT NULL,
                quantity INTEGER NOT NULL,
                lot_size INTEGER NOT NULL,

                entry_time TIMESTAMP NOT NULL,
                entry_greeks TEXT,

                exit_price DECIMAL(12,2),
                exit_time TIMESTAMP,
                exit_greeks TEXT,

                realized_pnl DECIMAL(12,2),
                unrealized_pnl DECIMAL(12,2),

                status VARCHAR DEFAULT 'OPEN',
                notes TEXT,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create indexes
        self.db.con.execute("""
            CREATE INDEX IF NOT EXISTS idx_paper_trades_status
            ON paper_trades(status)
        """)

        self.db.con.execute("""
            CREATE INDEX IF NOT EXISTS idx_paper_trades_signal
            ON paper_trades(signal_id)
        """)

        logger.info("✅ Paper trades table ready")

    def generate_trade_id(self) -> str:
        """Generate unique trade ID: PAPER_YYYYMMDD_HHMMSS_XXX"""
        timestamp = datetime.now()
        ts_str = timestamp.strftime("%Y%m%d_%H%M%S")

        # Get count of trades today
        today_start = datetime.combine(timestamp.date(), datetime.min.time())
        try:
            result = self.db.con.execute("""
                SELECT COUNT(*) FROM paper_trades
                WHERE created_at >= ?
            """, [today_start]).fetchone()
            count = result[0] if result else 0
        except Exception as e:
            logger.warning(f"Error getting trade count: {e}")
            count = 0

        return f"PAPER_{ts_str}_{count+1:03d}"

    def create_trade(
        self,
        signal_id: str,
        symbol: str,
        strategy: str,
        recommendation,  # OptionRecommendation object
        quantity_override: Optional[int] = None
    ) -> PaperTrade:
        """
        Create a paper trade from an option recommendation

        Args:
            signal_id: Signal ID from unified_signals
            symbol: Underlying symbol
            strategy: Strategy name
            recommendation: OptionRecommendation object
            quantity_override: Override calculated quantity

        Returns:
            PaperTrade object
        """
        import json

        trade_id = self.generate_trade_id()

        # Get lot size from recommendation
        lot_size = getattr(recommendation, 'lot_size', 1)
        if lot_size <= 0:
            lot_size = 1

        # Calculate number of lots to trade
        if quantity_override:
            lots = quantity_override
        else:
            # Calculate lots based on capital allocation
            if recommendation.premium > 0 and lot_size > 0:
                lots = max(1, int(recommendation.capital_required / (recommendation.premium * lot_size)))
            else:
                lots = 1

        # Total quantity = lots * lot_size
        quantity = lots * lot_size

        # Collect Greeks
        greeks = {
            'delta': recommendation.delta,
            'gamma': getattr(recommendation, 'gamma', None),
            'theta': getattr(recommendation, 'theta', None),
            'vega': getattr(recommendation, 'vega', None),
            'iv': getattr(recommendation, 'iv', None),
        }

        # Get instrument key from recommendation
        inst_key = getattr(recommendation, 'instrument_key', '')
        logger.info(f"Creating paper trade for {symbol}: instrument_key='{inst_key}'")

        if not inst_key:
            logger.warning(f"⚠️ No instrument_key in recommendation for {symbol} - LTP will not update!")

        trade = PaperTrade(
            trade_id=trade_id,
            signal_id=signal_id,
            symbol=symbol,
            strategy=strategy,
            option_instrument_key=inst_key,
            option_type=recommendation.option_type,
            strike_price=recommendation.strike,
            expiry_date=str(getattr(recommendation, 'expiry_date', '')),
            side='BUY',
            entry_price=recommendation.premium,
            quantity=quantity,
            lot_size=getattr(recommendation, 'lot_size', 1),
            entry_time=datetime.now(),
            entry_greeks=json.dumps(greeks),
            notes=f"Rank: {recommendation.rank_score}/100 | {recommendation.rank_reason}"
        )

        # Write to database
        success = self.write_trade(trade)

        if success:
            logger.info(f"✅ Paper trade created: {trade_id}")
            return trade
        else:
            raise Exception(f"Failed to create paper trade {trade_id}")

    def write_trade(self, trade: PaperTrade) -> bool:
        """Write trade to database"""
        try:
            self.db.con.execute("""
                INSERT INTO paper_trades (
                    trade_id, signal_id, symbol, strategy,
                    option_instrument_key, option_type, strike_price, expiry_date,
                    side, entry_price, quantity, lot_size,
                    entry_time, entry_greeks,
                    exit_price, exit_time, exit_greeks,
                    realized_pnl, unrealized_pnl,
                    status, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                trade.trade_id, trade.signal_id, trade.symbol, trade.strategy,
                trade.option_instrument_key, trade.option_type, trade.strike_price, trade.expiry_date,
                trade.side, trade.entry_price, trade.quantity, trade.lot_size,
                trade.entry_time, trade.entry_greeks,
                trade.exit_price, trade.exit_time, trade.exit_greeks,
                trade.realized_pnl, trade.unrealized_pnl,
                trade.status, trade.notes, trade.created_at, trade.updated_at
            ])

            self.db.con.commit()
            return True

        except Exception as e:
            logger.error(f"❌ Error writing trade {trade.trade_id}: {e}")
            return False

    def get_open_positions(self) -> pd.DataFrame:
        """Get all open paper trades"""
        try:
            df = self.db.con.execute("""
                SELECT * FROM paper_trades
                WHERE status = 'OPEN'
                ORDER BY entry_time DESC
            """).df()

            logger.info(f"📊 Retrieved {len(df)} open positions")
            return df

        except Exception as e:
            logger.error(f"❌ Error retrieving open positions: {e}")
            return pd.DataFrame()

    def get_closed_positions(self, limit: int = 100) -> pd.DataFrame:
        """Get closed trades (trade log)"""
        try:
            df = self.db.con.execute(f"""
                SELECT * FROM paper_trades
                WHERE status = 'CLOSED'
                ORDER BY exit_time DESC
                LIMIT {limit}
            """).df()

            return df

        except Exception as e:
            logger.error(f"❌ Error retrieving closed positions: {e}")
            return pd.DataFrame()

    def fetch_live_ltp(self, instrument_key: str) -> Optional[float]:
        """
        Fetch live LTP from Upstox API

        Args:
            instrument_key: Option instrument key

        Returns:
            Current LTP or None
        """
        import urllib.parse

        try:
            token = get_access_token()
            if not token:
                logger.warning("No access token available for LTP fetch")
                return None

            if not instrument_key:
                logger.warning("Empty instrument_key provided")
                return None

            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json"
            }

            # URL-encode the instrument_key (contains | character)
            encoded_key = urllib.parse.quote(instrument_key, safe='')

            # Market quote API
            url = f"https://api.upstox.com/v2/market-quote/ltp?instrument_key={encoded_key}"

            resp = requests.get(url, headers=headers, timeout=5)

            logger.debug(f"LTP API response for {instrument_key}: status={resp.status_code}")

            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    # Response uses the original (non-encoded) key
                    ltp_data = data.get("data", {}).get(instrument_key, {})
                    ltp = ltp_data.get("last_price")
                    if ltp is not None:
                        logger.debug(f"LTP for {instrument_key}: {ltp}")
                        return float(ltp)
                    else:
                        logger.warning(f"No last_price in response for {instrument_key}")
                else:
                    logger.warning(f"LTP API error: {data.get('errors', data)}")
            else:
                logger.warning(f"LTP API HTTP {resp.status_code}: {resp.text[:200]}")

            return None

        except Exception as e:
            logger.error(f"❌ Error fetching LTP for {instrument_key}: {e}")
            return None

    def get_open_positions_with_live_pnl(self) -> pd.DataFrame:
        """
        Get open positions with live P&L calculation

        Fetches current LTP and calculates unrealized P&L
        """
        df = self.get_open_positions()

        if df.empty:
            return df

        # Fetch live LTP for each position
        live_prices = []
        unrealized_pnls = []
        pnl_pcts = []

        for _, row in df.iterrows():
            instrument_key = row['option_instrument_key']
            entry_price = float(row['entry_price'])
            quantity = int(row['quantity'])

            # Skip if no instrument key stored
            if not instrument_key or instrument_key == '':
                logger.warning(f"No instrument_key for trade {row['trade_id']}, using entry price")
                live_prices.append(entry_price)
                unrealized_pnls.append(0)
                pnl_pcts.append(0)
                continue

            # Fetch live price
            live_ltp = self.fetch_live_ltp(instrument_key)

            if live_ltp is not None:
                live_prices.append(live_ltp)

                # Calculate P&L
                pnl = (live_ltp - entry_price) * quantity
                pnl_pct = ((live_ltp - entry_price) / entry_price) * 100 if entry_price > 0 else 0

                unrealized_pnls.append(pnl)
                pnl_pcts.append(pnl_pct)
            else:
                logger.warning(f"Could not fetch LTP for {instrument_key}, using entry price")
                live_prices.append(entry_price)  # Fallback to entry
                unrealized_pnls.append(0)
                pnl_pcts.append(0)

        df['live_ltp'] = live_prices
        df['unrealized_pnl'] = unrealized_pnls
        df['pnl_pct'] = pnl_pcts

        return df

    def square_off_trade(
        self,
        trade_id: str,
        exit_price: Optional[float] = None
    ) -> bool:
        """
        Square off (close) a paper trade

        Args:
            trade_id: Trade ID to close
            exit_price: Exit price (if None, fetches live LTP)

        Returns:
            True if successful
        """
        import json

        try:
            # Get trade details
            trade = self.db.con.execute("""
                SELECT * FROM paper_trades WHERE trade_id = ?
            """, [trade_id]).fetchone()

            if not trade:
                logger.error(f"Trade {trade_id} not found")
                return False

            # Get exit price
            if exit_price is None:
                exit_price = self.fetch_live_ltp(trade[4])  # option_instrument_key
                if exit_price is None:
                    logger.error(f"Could not fetch exit price for {trade_id}")
                    return False

            # Calculate realized P&L
            entry_price = trade[9]  # entry_price
            quantity = trade[10]  # quantity
            realized_pnl = (exit_price - entry_price) * quantity

            # Update database
            self.db.con.execute("""
                UPDATE paper_trades
                SET exit_price = ?,
                    exit_time = ?,
                    realized_pnl = ?,
                    status = 'CLOSED',
                    updated_at = CURRENT_TIMESTAMP
                WHERE trade_id = ?
            """, [exit_price, datetime.now(), realized_pnl, trade_id])

            self.db.con.commit()

            logger.info(f"✅ Trade {trade_id} squared off | P&L: ₹{realized_pnl:,.2f}")
            return True

        except Exception as e:
            logger.error(f"❌ Error squaring off trade {trade_id}: {e}")
            return False

    def get_trade_stats(self) -> Dict:
        """Get paper trading statistics"""
        try:
            stats = {}

            # Open positions
            open_df = self.get_open_positions()
            stats['open_count'] = len(open_df)
            stats['open_capital'] = (open_df['entry_price'] * open_df['quantity']).sum() if not open_df.empty else 0

            # Closed positions
            closed_df = self.get_closed_positions()
            if not closed_df.empty:
                stats['closed_count'] = len(closed_df)
                stats['total_pnl'] = closed_df['realized_pnl'].sum()
                stats['win_count'] = (closed_df['realized_pnl'] > 0).sum()
                stats['loss_count'] = (closed_df['realized_pnl'] < 0).sum()
                stats['win_rate'] = (stats['win_count'] / stats['closed_count'] * 100) if stats['closed_count'] > 0 else 0
                stats['avg_pnl'] = closed_df['realized_pnl'].mean()
            else:
                stats['closed_count'] = 0
                stats['total_pnl'] = 0
                stats['win_count'] = 0
                stats['loss_count'] = 0
                stats['win_rate'] = 0
                stats['avg_pnl'] = 0

            return stats

        except Exception as e:
            logger.error(f"❌ Error getting trade stats: {e}")
            return {}


if __name__ == "__main__":
    # Test the paper trading system
    print("=== Paper Trading Manager Test ===\n")

    manager = PaperTradingManager()

    # Get stats
    stats = manager.get_trade_stats()
    print(f"Stats: {stats}")

    # Get open positions
    open_positions = manager.get_open_positions()
    print(f"\nOpen positions: {len(open_positions)}")

    if not open_positions.empty:
        print(open_positions[['trade_id', 'symbol', 'option_type', 'strike_price', 'entry_price']])
