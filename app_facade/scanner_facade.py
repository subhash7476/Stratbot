from dataclasses import dataclass
from typing import List, Dict, Optional, Any
from datetime import datetime, date
import pandas as pd

from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.backtest.scan_persistence import ScanPersistence

@dataclass
class WatchlistRow:
    """Single row in the Watchlist panel."""
    symbol: str
    trading_symbol: str
    market_type: str
    open: float
    high: float
    low: float
    last_price: float
    price_change_pct: float
    volume: float
    last_updated: Optional[datetime]
    instrument_key: Optional[str] = None

















































@dataclass
class ScannerRow:
    """Single row in the Live Scanner panel."""
    symbol: str
    trading_symbol: str  # Friendly display name
    strategy_id: str
    strategy_name: str
    timeframe: str
    current_bias: str
    signal_state: str
    confidence: float
    last_bar_ts: Optional[datetime]
    status: str

@dataclass
class SymbolContext:
    """Full context for a selected symbol."""
    symbol: str
    trading_symbol: str
    market_type: str
    latest_bias: Optional[str]
    latest_confidence: Optional[float]
    indicator_states: Optional[Dict[str, Any]]
    regime: Optional[str]
    momentum_bias: Optional[str]
    trend_strength: Optional[float]
    volatility_level: Optional[str]
    active_strategies: List[Dict[str, Any]]
    recent_signals: List[Dict[str, Any]]
    last_trade: Optional[Dict[str, Any]]

class ScannerFacade:
    """
    Facade for Scanner and Watchlist data operations.
    Updated to use the refactored DatabaseManager.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.query = MarketDataQuery(db_manager)
        self._scan_persistence = None

    @property
    def scan_persistence(self) -> ScanPersistence:
        if self._scan_persistence is None:
            self._scan_persistence = ScanPersistence(self.db)
        return self._scan_persistence

    # ─── Backtest Scanner Methods ──────────────────────────────

    def get_all_scans(self):
        """Return all scan summaries."""
        return self.scan_persistence.get_all_scans()

    def get_scan_results(self, scan_id: str):
        """Return full scan details with per-symbol results."""
        return self.scan_persistence.get_scan_results(scan_id)

    def get_profitable_symbols(self, scan_id=None):
        """Return profitable symbols from latest (or specified) scan."""
        return self.scan_persistence.get_profitable_symbols(scan_id)

    # ─── Live Scanner & Watchlist Methods ──────────────────────

    def get_debug_stats(self) -> Dict[str, Any]:
        """Returns internal stats for debugging live data flow."""
        stats = {}
        try:
            with self.db.live_buffer_reader() as conns:
                if 'ticks' in conns:
                    stats["tick_count"] = conns['ticks'].execute("SELECT count(*) FROM ticks").fetchone()[0]
                if 'candles' in conns:
                    stats["ohlcv_count"] = conns['candles'].execute("SELECT count(*) FROM candles").fetchone()[0]
            
            with self.db.config_reader() as conn:
                stats["instrument_count"] = conn.execute("SELECT count(*) FROM instrument_meta").fetchone()[0]
                
            return stats
        except Exception as e:
            return {"error": str(e)}

    def get_watchlist_snapshot(self) -> List[WatchlistRow]:
        """
        Returns watchlist snapshot.
        1. Fetch metadata from config.db
        2. Fetch latest prices from live_buffer via query interface
        """
        rows = []
        try:
            # 1. Get watchlist from config
            with self.db.config_reader() as conn:
                # For now, we take a sample or common instruments if no user watchlist
                meta = conn.execute("""
                    SELECT symbol, trading_symbol, market_type 
                    FROM instrument_meta 
                    WHERE is_active = 1 
                    LIMIT 100
                """).fetchall()
            
            # 2. Enrichment with live prices
            for symbol, t_symbol, m_type in meta:
                data = self._get_latest_price_data(symbol)
                
                rows.append(WatchlistRow(
                    symbol=symbol,
                    trading_symbol=t_symbol,
                    market_type=m_type,
                    open=data['open'],
                    high=data['high'],
                    low=data['low'],
                    last_price=data['last_price'],
                    price_change_pct=data['change_pct'],
                    volume=data['volume'],
                    last_updated=data['last_updated']
                ))

        except Exception as e:
            print(f"[SCANNER FACADE] Watchlist error: {e}")
        return rows

    def _get_latest_price_data(self, symbol: str) -> Dict[str, Any]:
        """Helper to get best available price data (Tick > Candle)."""
        data = {
            'open': 0.0, 'high': 0.0, 'low': 0.0, 
            'last_price': 0.0, 'volume': 0.0, 
            'last_updated': None, 'change_pct': 0.0
        }
        
        try:
            # Try to get latest Tick first (Real-time)
            with self.db.live_buffer_reader() as conns:
                if 'ticks' in conns:
                    tick = conns['ticks'].execute("""
                        SELECT price, volume, timestamp 
                        FROM ticks 
                        WHERE symbol = ? 
                        ORDER BY timestamp DESC LIMIT 1
                    """, [symbol]).fetchone()
                    
                    if tick:
                        data['last_price'] = float(tick[0])
                        # prioritizing tick volume might be weird if it's just tick volume vs cumulative?
                        # Usually ticks table stores cumulative volume if available, or just trade size.
                        # If trade size, we shouldn't use it as 'Volume'.
                        # Let's check candles for OHLCV and volume, but use tick for LTP.
            
            # Get OHLCV from candles for context
            now = datetime.now()
            df = self.query.get_candles(symbol, 'nse', '1m', now.replace(hour=0, minute=0, second=0), now)
            
            if not df.empty:
                data['open'] = float(df.iloc[0]['open'])
                data['high'] = float(df['high'].max())
                data['low'] = float(df['low'].min())
                
                last_row = df.iloc[-1]
                data['volume'] = float(last_row['volume']) # This is volume of last candle, not day.
                # actually we want day volume. sum of volume?
                data['volume'] = float(df['volume'].sum())
                
                # If we didn't get a tick, use candle close
                if data['last_price'] == 0.0:
                    data['last_price'] = float(last_row['close'])
                    data['last_updated'] = last_row['timestamp']
                
                # Update High/Low with current price if outside range
                if data['last_price'] > data['high']: data['high'] = data['last_price']
                if data['last_price'] > 0 and (data['low'] == 0 or data['last_price'] < data['low']): 
                    data['low'] = data['last_price']

                # Change Pct (relative to Open of day for now, as prev_close might be missing)
                if data['open'] > 0:
                    data['change_pct'] = ((data['last_price'] - data['open']) / data['open']) * 100.0

        except Exception as e:
            # print(f"Price fetch error {symbol}: {e}")
            pass
            
        return data



    def get_filter_options(self) -> Dict[str, List[str]]:
        """Returns unique values for filtering instruments."""
        options = {"exchanges": [], "market_types": [], "indices": []}
        try:
            with self.db.config_reader() as conn:
                exchanges = conn.execute("SELECT DISTINCT exchange FROM instrument_meta WHERE exchange IS NOT NULL").fetchall()
                options["exchanges"] = [e[0] for e in exchanges]

                market_types = conn.execute("SELECT DISTINCT market_type FROM instrument_meta WHERE market_type IS NOT NULL").fetchall()
                options["market_types"] = [m[0] for m in market_types]

                # We can add predefined indices or fetch from a table if available
                options["indices"] = ["NIFTY 50", "NIFTY BANK", "NIFTY NEXT 50"]
        except Exception as e:
            print(f"[SCANNER FACADE] Filter options error: {e}")
        return options

    def get_filtered_instruments(self, filters: Dict[str, str]) -> List[Dict[str, Any]]:
        """Returns filtered instruments based on criteria."""
        results = []
        try:
            query = "SELECT symbol, trading_symbol, exchange, market_type FROM instrument_meta WHERE is_active = 1"
            params = []

            if filters.get('exchange'):
                query += " AND exchange = ?"
                params.append(filters['exchange'])
            
            if filters.get('market_type'):
                query += " AND market_type = ?"
                params.append(filters['market_type'])

            if filters.get('search'):
                query += " AND (trading_symbol LIKE ? OR symbol LIKE ?)"
                search_val = f"%{filters['search']}%"
                params.append(search_val)
                params.append(search_val)

            query += " ORDER BY trading_symbol LIMIT 500"

            with self.db.config_reader() as conn:
                res = conn.execute(query, params).fetchall()
                for r in res:
                    results.append({
                        "instrument_key": r[0],
                        "trading_symbol": r[1],
                        "exchange": r[2],
                        "market_type": r[3]
                    })
        except Exception as e:
            print(f"[SCANNER FACADE] Filtered instruments error: {e}")
        return results

    def add_bulk_to_watchlist(self, username: str, instruments: List[Dict[str, str]]) -> bool:
        """Adds multiple instruments to user watchlist in one transaction."""
        try:
            with self.db.config_writer() as conn:
                for inst in instruments:
                    conn.execute("""
                        INSERT OR IGNORE INTO user_watchlist
                        (username, instrument_key, trading_symbol, exchange, market_type)
                        VALUES (?, ?, ?, ?, ?)
                    """, [
                        username, 
                        inst['instrument_key'], 
                        inst.get('trading_symbol', ''), 
                        inst.get('exchange', 'NSE'), 
                        inst.get('market_type', 'EQ')
                    ])
            return True
        except Exception as e:
            print(f"[SCANNER FACADE] Bulk add error: {e}")
            return False

    def get_fo_stocks(self) -> List[Dict[str, Any]]:
        """Returns all F&O stocks from fo_stocks table."""
        results = []
        try:
            with self.db.config_reader() as conn:
                res = conn.execute("""
                    SELECT instrument_key, trading_symbol, 'NSE' as exchange, 'FO' as market_type
                    FROM fo_stocks
                    WHERE is_active = 1
                """).fetchall()
                for r in res:
                    results.append({
                        "instrument_key": r[0],
                        "trading_symbol": r[1],
                        "exchange": r[2],
                        "market_type": r[3]
                    })
        except Exception as e:
            print(f"[SCANNER FACADE] FO stocks error: {e}")
        return results

    def get_user_watchlist(self, username: str = 'default') -> List[WatchlistRow]:
        rows = []
        try:
            with self.db.config_reader() as conn:
                meta = conn.execute("""
                    SELECT instrument_key, trading_symbol, market_type 
                    FROM user_watchlist 
                    WHERE username = ?
                """, [username]).fetchall()
            
            for key, t_symbol, m_type in meta:
                # key here is used as symbol
                data = self._get_latest_price_data(key)

                rows.append(WatchlistRow(
                    symbol=key,
                    trading_symbol=t_symbol,
                    market_type=m_type,
                    open=data['open'],
                    high=data['high'],
                    low=data['low'],
                    last_price=data['last_price'],
                    price_change_pct=data['change_pct'],
                    volume=data['volume'],
                    last_updated=data['last_updated'],
                    instrument_key=key
                ))
        except Exception as e:
            print(f"[SCANNER FACADE] User watchlist error: {e}")
        return rows

    def get_live_scanner_state(self) -> List[ScannerRow]:
        rows = []
        try:
            with self.db.config_reader() as conn:
                res = conn.execute("""
                    SELECT
                        rs.symbol,
                        COALESCE(im.trading_symbol, rs.symbol) as trading_symbol,
                        rs.strategy_id,
                        rs.timeframe,
                        rs.current_bias,
                        rs.signal_state,
                        rs.confidence,
                        rs.last_bar_ts,
                        rs.status
                    FROM runner_state rs
                    LEFT JOIN instrument_meta im ON rs.symbol = im.symbol
                    ORDER BY rs.updated_at DESC
                """).fetchall()
                for r in res:
                    rows.append(ScannerRow(
                        symbol=r[0],
                        trading_symbol=r[1],  # Friendly display name
                        strategy_id=r[2],
                        strategy_name=r[2],
                        timeframe=r[3],
                        current_bias=r[4],
                        signal_state=r[5],
                        confidence=r[6],
                        last_bar_ts=r[7],
                        status=r[8]
                    ))
        except Exception as e:
            print(f"[SCANNER FACADE] Scanner error: {e}")
        return rows

    def get_symbol_context(self, symbol: str) -> Optional[SymbolContext]:
        try:
            # 1. Base Meta (Config DB)
            with self.db.config_reader() as conn:
                meta_res = conn.execute("SELECT trading_symbol, market_type FROM instrument_meta WHERE symbol = ?", [symbol]).fetchone()
                trading_symbol = meta_res[0] if meta_res else symbol
                market_type = meta_res[1] if meta_res else 'EQ'

            # 2. Latest Insights & Regime (Signals DB)
            with self.db.signals_reader() as conn:
                insight = conn.execute("""
                    SELECT bias, confidence, indicator_states 
                    FROM confluence_insights 
                    WHERE symbol = ? 
                    ORDER BY timestamp DESC LIMIT 1
                """, [symbol]).fetchone()

                regime_res = conn.execute("""
                    SELECT regime, momentum_bias, trend_strength, volatility_level 
                    FROM regime_insights 
                    WHERE symbol = ? 
                    ORDER BY timestamp DESC LIMIT 1
                """, [symbol]).fetchone()
                
                signals = conn.execute("""
                    SELECT signal_type, confidence, bar_ts, status 
                    FROM signals 
                    WHERE symbol = ? 
                    ORDER BY created_at DESC LIMIT 10
                """, [symbol]).fetchall()

            # 3. Last Trade (Trading DB)
            with self.db.trading_reader() as conn:
                trade = conn.execute("""
                    SELECT side, entry_price, exit_price, pnl, timestamp 
                    FROM trades 
                    WHERE symbol = ? 
                    ORDER BY timestamp DESC LIMIT 1
                """, [symbol]).fetchone()

            # 4. Active Strategies (Config DB - runner_state)
            with self.db.config_reader() as conn:
                strategies = conn.execute("""
                    SELECT strategy_id, status, current_bias, confidence 
                    FROM runner_state 
                    WHERE symbol = ?
                """, [symbol]).fetchall()

            return SymbolContext(
                symbol=symbol,
                trading_symbol=trading_symbol,
                market_type=market_type,
                latest_bias=insight[0] if insight else None,
                latest_confidence=insight[1] if insight else None,
                indicator_states=insight[2] if insight else None,
                regime=regime_res[0] if regime_res else None,
                momentum_bias=regime_res[1] if regime_res else None,
                trend_strength=regime_res[2] if regime_res else None,
                volatility_level=regime_res[3] if regime_res else None,
                active_strategies=[{"id": s[0], "status": s[1], "bias": s[2], "conf": s[3]} for s in strategies],
                recent_signals=[{"type": sig[0], "conf": sig[1], "time": sig[2], "status": sig[3]} for sig in signals],
                last_trade={"side": trade[0], "entry": trade[1], "exit": trade[2], "pnl": trade[3], "time": trade[4]} if trade else None
            )
        except Exception as e:
            print(f"[SCANNER FACADE] Context error for {symbol}: {e}")
            return None
