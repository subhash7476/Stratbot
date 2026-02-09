"""
Symbol Utilities
----------------
Utilities for resolving and working with instrument symbols.
Features in-memory caching for high performance in UI and analysis.
"""

import logging
from typing import Optional, List, Dict
from pathlib import Path
from functools import lru_cache

from core.database.manager import DatabaseManager

logger = logging.getLogger(__name__)

# Global caches for resolution
_KEY_TO_SYMBOL_CACHE: Dict[str, str] = {}
_SYMBOL_TO_KEY_CACHE: Dict[str, str] = {}

def get_symbol_map(db_manager: Optional[DatabaseManager] = None) -> Dict[str, str]:
    """Returns a full mapping of instrument_key -> trading_symbol."""
    global _KEY_TO_SYMBOL_CACHE
    if _KEY_TO_SYMBOL_CACHE:
        return _KEY_TO_SYMBOL_CACHE

    db = db_manager or DatabaseManager(Path("data").resolve())
    mapping = {}
    
    try:
        with db.config_reader() as conn:
            # Load from instrument_meta first (base truth)
            rows = conn.execute("SELECT instrument_key, trading_symbol FROM instrument_meta").fetchall()
            for k, s in rows:
                mapping[k] = s
                
            # Then supplement with fo_stocks (can override with cleaner names if needed)
            # but for now we trust instrument_meta
    except Exception as e:
        logger.error(f"Failed to load symbol map: {e}")
        
    _KEY_TO_SYMBOL_CACHE = mapping
    return mapping

def key_to_symbol(instrument_key: str, db_manager: Optional[DatabaseManager] = None) -> str:
    """
    Fast resolution of key to trading symbol.
    Example: NSE_EQ|INE002A01018 -> RELIANCE
    """
    if not instrument_key: return "UNKNOWN"
    
    mapping = get_symbol_map(db_manager)
    return mapping.get(instrument_key, instrument_key)

def get_exchange_from_key(instrument_key: str) -> str:
    """Derive exchange folder name from instrument key."""
    if not instrument_key or '|' not in instrument_key:
        return 'nse' # Default fallback
        
    segment = instrument_key.split('|')[0].upper()
    if segment.startswith('NSE'): return 'nse'
    if segment.startswith('MCX'): return 'mcx'
    if segment.startswith('BSE'): return 'bse'
    return segment.lower()

def symbol_to_key(trading_symbol: str, db_manager: Optional[DatabaseManager] = None) -> Optional[str]:
    """
    Resolves a trading symbol to its primary instrument key (prefers NSE_EQ).
    """
    global _SYMBOL_TO_KEY_CACHE
    if trading_symbol in _SYMBOL_TO_KEY_CACHE:
        return _SYMBOL_TO_KEY_CACHE[trading_symbol]

    db = db_manager or DatabaseManager(Path("data").resolve())
    
    try:
        with db.config_reader() as conn:
            # 1. Try fo_stocks first (Our curated universe)
            row = conn.execute(
                "SELECT instrument_key FROM fo_stocks WHERE UPPER(trading_symbol) = UPPER(?) LIMIT 1",
                [trading_symbol]
            ).fetchone()
            if row:
                _SYMBOL_TO_KEY_CACHE[trading_symbol] = row[0]
                return row[0]
                
            # 2. Try instrument_meta (fallback for any symbol)
            # Prefer NSE_EQ if multiple exist
            row = conn.execute(
                """
                SELECT instrument_key FROM instrument_meta 
                WHERE UPPER(trading_symbol) = UPPER(?) 
                ORDER BY (market_type = 'NSE_EQ') DESC 
                LIMIT 1
                """,
                [trading_symbol]
            ).fetchone()
            if row:
                _SYMBOL_TO_KEY_CACHE[trading_symbol] = row[0]
                return row[0]
    except Exception as e:
        logger.error(f"Resolution failed for {trading_symbol}: {e}")
        
    return None

def resolve_to_instrument_key(symbol: str, db_path: Optional[str] = None) -> Optional[str]:
    """Compatibility wrapper for existing code."""
    if not symbol: return None
    if "|" in symbol: return symbol
    
    db_manager = None
    if db_path:
        db_manager = DatabaseManager(Path(db_path).resolve())
        
    return symbol_to_key(symbol, db_manager)

def get_trading_symbol(instrument_key: str, db_path: Optional[str] = None) -> Optional[str]:
    """Compatibility wrapper for existing code."""
    db_manager = None
    if db_path:
        db_manager = DatabaseManager(Path(db_path).resolve())
    return key_to_symbol(instrument_key, db_manager)

def clear_symbol_caches():
    """Wipes memory caches to force reload from DB."""
    global _KEY_TO_SYMBOL_CACHE, _SYMBOL_TO_KEY_CACHE
    _KEY_TO_SYMBOL_CACHE = {}
    _SYMBOL_TO_KEY_CACHE = {}
