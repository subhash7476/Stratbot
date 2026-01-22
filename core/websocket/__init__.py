# core/websocket/__init__.py
"""
Enhanced WebSocket Module for Upstox Market Data
================================================

This module provides a robust WebSocket implementation for real-time market data
streaming from Upstox, optimized for single-user usage.

Components:
- UpstoxWebSocketClient: Async WebSocket client with protobuf support
- MarketDataStore: Thread-safe in-memory store for instant LTP access
- WebSocketManager: Orchestrates connection, subscriptions, and data flow

Usage:
    from core.websocket import WebSocketManager, MarketDataStore

    # Get singleton instances
    manager = WebSocketManager.get_instance()
    store = MarketDataStore.get_instance()

    # Start streaming
    manager.start(access_token, instrument_keys)

    # Get instant LTP (no DB query)
    ltp = store.get_ltp("NSE_EQ|INE002A01018")

    # Get all LTPs
    all_ltps = store.get_all_ltps()

Architecture (simplified from OpenAlgo for single-user):

    [Upstox WebSocket API]
            │
            ▼ (protobuf + JSON)
    [UpstoxWebSocketClient]
            │
            ├──▶ [MarketDataStore] ──▶ Instant LTP access
            │
            └──▶ [Candle Builder] ──▶ DuckDB (live_ohlcv_cache)
"""

from .market_data_store import MarketDataStore
from .upstox_ws_client import UpstoxWebSocketClient
from .manager import WebSocketManager

__all__ = [
    'MarketDataStore',
    'UpstoxWebSocketClient',
    'WebSocketManager'
]
