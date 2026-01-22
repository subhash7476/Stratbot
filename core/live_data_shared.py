# core/live_data_shared.py
"""
Shared Live Data Access
=======================
Import this module in any page that needs live data access.
Ensures all pages use the SAME LiveTradingManager instance.

NEW in v2.0: Integrated with enhanced WebSocket module for:
- Instant LTP access (no DB query)
- Enhanced reconnection with exponential backoff
- Thread-safe in-memory market data store

Usage in any page:
    from core.live_data_shared import (
        get_shared_live_manager,
        ensure_websocket_connected,
        get_instant_ltp,      # NEW: No DB query!
        get_instant_ltps,     # NEW: Batch LTPs
        get_websocket_status  # NEW: Connection health
    )

    # Traditional MTF data (from DB)
    live_manager = get_shared_live_manager()
    if live_manager:
        df_60m, df_15m, df_5m = live_manager.get_live_mtf_data(instrument_key, lookback_days=60)

    # NEW: Instant LTP (from memory - no DB!)
    ltp = get_instant_ltp("NSE_EQ|INE002A01018")
"""

import streamlit as st
from typing import Optional, Tuple, Dict, List
import pandas as pd
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Lazy imports to avoid circular dependencies
_live_manager_class = None
_ws_manager = None
_market_store = None


def _get_live_manager_class():
    global _live_manager_class
    if _live_manager_class is None:
        from core.live_trading_manager import LiveTradingManager
        _live_manager_class = LiveTradingManager
    return _live_manager_class


def _get_ws_manager():
    """Get WebSocketManager singleton (lazy load)"""
    global _ws_manager
    if _ws_manager is None:
        try:
            from core.websocket import WebSocketManager
            _ws_manager = WebSocketManager.get_instance()
        except ImportError as e:
            logger.warning(f"Enhanced WebSocket module not available: {e}")
    return _ws_manager


def _get_market_store():
    """Get MarketDataStore singleton (lazy load)"""
    global _market_store
    if _market_store is None:
        try:
            from core.websocket import MarketDataStore
            _market_store = MarketDataStore.get_instance()
        except ImportError as e:
            logger.warning(f"MarketDataStore not available: {e}")
    return _market_store


def get_shared_live_manager():
    """
    Get or create the SINGLE LiveTradingManager instance.

    All pages MUST use this function to access live data.
    This ensures WebSocket connection is shared and data is consistent.

    Returns:
        LiveTradingManager instance or None if initialization failed
    """
    # Use a consistent key across ALL pages
    SESSION_KEY = "live_manager"

    if SESSION_KEY not in st.session_state or st.session_state[SESSION_KEY] is None:
        try:
            LiveTradingManager = _get_live_manager_class()
            st.session_state[SESSION_KEY] = LiveTradingManager()
        except Exception as e:
            print(f"[LIVE] Failed to initialize LiveTradingManager: {e}")
            st.session_state[SESSION_KEY] = None

    return st.session_state[SESSION_KEY]


def ensure_websocket_connected(access_token: str, use_enhanced: bool = True) -> bool:
    """
    Ensure WebSocket is connected and receiving data.

    Args:
        access_token: Upstox API access token
        use_enhanced: Use enhanced WebSocket module (default True)

    Returns:
        True if connected, False otherwise
    """
    # Try enhanced WebSocket first
    if use_enhanced:
        ws_manager = _get_ws_manager()
        if ws_manager:
            if ws_manager.is_connected:
                return True

            # Get instruments from LiveTradingManager
            live_manager = get_shared_live_manager()
            if live_manager:
                instruments = live_manager.get_active_instruments()
                instrument_keys = [key for key, _ in instruments]

                if instrument_keys:
                    success = ws_manager.start(access_token, instrument_keys)
                    if success:
                        logger.info("Enhanced WebSocket connected")
                        return True

    # Fallback to legacy WebSocket
    live_manager = get_shared_live_manager()
    if not live_manager:
        return False

    live_manager.start_websocket_if_needed(access_token)
    return getattr(live_manager, "ws_connected", False)


def get_live_data_status() -> dict:
    """
    Get current status of live data.

    Returns:
        Dict with instruments_with_data, total_candles_today, first_candle, last_candle
    """
    live_manager = get_shared_live_manager()
    if not live_manager:
        return {
            "instruments_with_data": 0,
            "total_candles_today": 0,
            "first_candle": None,
            "last_candle": None,
            "error": "Live manager not available"
        }

    return live_manager.get_live_data_summary()


def get_mtf_data_for_scanning(
    instrument_key: str,
    lookback_days: int = 60
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """
    Get MTF data (60m, 15m, 5m) properly combined with historical data.

    This is the CORRECT way to get data for live scanning.
    It combines historical data + today's live data for proper indicator warmup.

    Args:
        instrument_key: Upstox instrument key
        lookback_days: Days of historical data to include (default 60)

    Returns:
        Tuple of (df_60m, df_15m, df_5m) DataFrames
    """
    live_manager = get_shared_live_manager()
    if not live_manager:
        return None, None, None

    return live_manager.get_live_mtf_data(instrument_key, lookback_days=lookback_days)


def rebuild_live_resampled():
    """
    Rebuild today's resampled data (5m/15m/60m) from 1m cache.
    Call this after WebSocket has collected new data.
    """
    live_manager = get_shared_live_manager()
    if live_manager:
        live_manager.rebuild_today_resampled()


# ============================================================
# NEW: INSTANT DATA ACCESS (No DB Query!)
# ============================================================

def get_instant_ltp(instrument_key: str) -> Optional[float]:
    """
    Get instant LTP from in-memory store (NO database query!).

    This is much faster than querying the database.
    Use for real-time displays and quick price checks.

    Args:
        instrument_key: Instrument key (e.g., "NSE_EQ|INE002A01018")

    Returns:
        LTP or None if not available
    """
    store = _get_market_store()
    if store:
        return store.get_ltp(instrument_key)
    return None


def get_instant_ltps(instrument_keys: List[str]) -> Dict[str, Optional[float]]:
    """
    Get instant LTPs for multiple instruments (NO database query!).

    Args:
        instrument_keys: List of instrument keys

    Returns:
        Dict mapping instrument_key -> LTP (or None)
    """
    store = _get_market_store()
    if store:
        return store.get_ltps(instrument_keys)
    return {k: None for k in instrument_keys}


def get_instant_quote(instrument_key: str) -> Optional[dict]:
    """
    Get full quote from in-memory store (NO database query!).

    Returns OHLC, volume, bid/ask, Greeks (for options) if available.

    Args:
        instrument_key: Instrument key

    Returns:
        Dict with quote data or None
    """
    store = _get_market_store()
    if store:
        quote = store.get_quote(instrument_key)
        if quote:
            return {
                'instrument_key': quote.instrument_key,
                'ltp': quote.ltp,
                'change': quote.change,
                'change_percent': quote.change_percent,
                'open': quote.open,
                'high': quote.high,
                'low': quote.low,
                'close': quote.close,
                'volume': quote.volume,
                'oi': quote.oi,
                'bid_price': quote.bid_price,
                'ask_price': quote.ask_price,
                'bid_qty': quote.bid_qty,
                'ask_qty': quote.ask_qty,
                'delta': quote.delta,
                'theta': quote.theta,
                'gamma': quote.gamma,
                'vega': quote.vega,
                'iv': quote.iv,
                'last_update': quote.last_update
            }
    return None


def get_all_instant_ltps() -> Dict[str, float]:
    """
    Get all available LTPs from in-memory store.

    Returns:
        Dict mapping instrument_key -> LTP
    """
    store = _get_market_store()
    if store:
        return store.get_all_ltps()
    return {}


def is_data_stale(instrument_key: str) -> bool:
    """
    Check if data for an instrument is stale (>60 seconds old).

    Args:
        instrument_key: Instrument key

    Returns:
        True if stale or not available
    """
    store = _get_market_store()
    if store:
        return store.is_stale(instrument_key)
    return True


def get_websocket_status() -> dict:
    """
    Get comprehensive WebSocket connection status.

    Returns:
        Dict with connection health, data stats, and errors
    """
    ws_manager = _get_ws_manager()
    if ws_manager:
        status = ws_manager.get_status()
        return {
            'connected': status.connected,
            'running': status.running,
            'ws_started_at': status.ws_started_at,
            'subscribed_instruments': status.subscribed_instruments,
            'instruments_with_data': status.instruments_with_data,
            'total_ticks_received': status.total_ticks_received,
            'candles_written': status.candles_written,
            'last_tick_time': status.last_tick_time,
            'last_error': status.last_error,
            'reconnect_count': status.reconnect_count,
            # Store health
            'stale_instruments': status.store_health.stale_instruments if status.store_health else 0,
            'updates_per_second': status.store_health.updates_per_second if status.store_health else 0
        }

    # Fallback to legacy status
    live_manager = get_shared_live_manager()
    if live_manager:
        return {
            'connected': getattr(live_manager, 'ws_connected', False),
            'running': getattr(live_manager, 'ws_builder', None) is not None,
            'ws_started_at': getattr(live_manager.ws_builder, 'ws_started_at', None) if live_manager.ws_builder else None,
            'enhanced': False
        }

    return {'connected': False, 'running': False, 'error': 'No WebSocket available'}


def stop_websocket():
    """Stop the WebSocket connection"""
    ws_manager = _get_ws_manager()
    if ws_manager:
        ws_manager.stop()
        logger.info("Enhanced WebSocket stopped")


def restart_websocket(access_token: str) -> bool:
    """
    Restart WebSocket with fresh connection.

    Args:
        access_token: Upstox API access token

    Returns:
        True if restarted successfully
    """
    ws_manager = _get_ws_manager()
    if ws_manager:
        ws_manager.restart(access_token)
        return ws_manager.is_connected
    return False


def display_live_status_widget(show_enhanced: bool = True):
    """
    Display a compact live data status widget.
    Can be added to any page's sidebar or header.

    Args:
        show_enhanced: Show enhanced WebSocket stats if available
    """
    # Try enhanced status first
    if show_enhanced:
        ws_status = get_websocket_status()
        if ws_status.get('running'):
            col1, col2, col3 = st.columns(3)
            with col1:
                connected = ws_status.get('connected', False)
                st.metric(
                    "WebSocket",
                    "Connected" if connected else "Disconnected",
                    delta=None,
                    delta_color="normal" if connected else "off"
                )
            with col2:
                st.metric("Instruments", ws_status.get('instruments_with_data', 0))
            with col3:
                tps = ws_status.get('updates_per_second', 0)
                st.metric("Ticks/sec", f"{tps:.1f}")
            return

    # Fallback to legacy status
    status = get_live_data_status()

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Live Instruments", status.get("instruments_with_data", 0))
    with col2:
        last_candle = status.get("last_candle")
        if last_candle:
            st.metric("Last Candle", pd.to_datetime(
                last_candle).strftime("%H:%M"))
        else:
            st.metric("Last Candle", "N/A")


# ============================================================
# MIGRATION HELPERS
# ============================================================

def migrate_old_session_keys():
    """
    Migrate old page-specific session keys to the shared key.
    Call this once at app startup if needed.
    """
    OLD_KEYS = [
        "sq_live_manager",      # Page 4 old key
        "ehma_live_manager",    # Potential old key
    ]

    for old_key in OLD_KEYS:
        if old_key in st.session_state and st.session_state[old_key] is not None:
            # If we don't have a shared manager yet, use the old one
            if "live_manager" not in st.session_state or st.session_state["live_manager"] is None:
                st.session_state["live_manager"] = st.session_state[old_key]
            # Clean up old key
            del st.session_state[old_key]
