# backend/routers/market_data.py
"""
Market Data Router
==================

Endpoints for real-time market data (LTP, quotes).
Uses the in-memory MarketDataStore for instant access.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

from backend.models.schemas import (
    LTPResponse,
    LTPBatchRequest,
    LTPBatchResponse,
    QuoteData,
    QuoteResponse,
    QuoteBatchRequest,
    QuoteBatchResponse
)

logger = logging.getLogger(__name__)

router = APIRouter()


def get_market_store():
    """Get MarketDataStore singleton"""
    try:
        from core.websocket import MarketDataStore
        return MarketDataStore.get_instance()
    except ImportError:
        logger.warning("MarketDataStore not available")
        return None


def get_ws_manager():
    """Get WebSocketManager singleton"""
    try:
        from core.websocket import WebSocketManager
        return WebSocketManager.get_instance()
    except ImportError:
        logger.warning("WebSocketManager not available")
        return None


# ============================================================
# LTP ENDPOINTS
# ============================================================

@router.get("/ltp/{instrument_key:path}", response_model=LTPResponse)
async def get_ltp(instrument_key: str):
    """
    Get instant LTP for a single instrument.

    The instrument_key should be in Upstox format: NSE_EQ|INE002A01018

    This endpoint reads from in-memory store - no database query.
    Response time is typically < 1ms.
    """
    store = get_market_store()

    if not store:
        return LTPResponse(
            instrument_key=instrument_key,
            success=False,
            error="Market data store not available"
        )

    ltp = store.get_ltp(instrument_key)

    if ltp is None:
        return LTPResponse(
            instrument_key=instrument_key,
            ltp=None,
            success=True,
            error="No data available for this instrument"
        )

    quote = store.get_quote(instrument_key)
    is_stale = store.is_stale(instrument_key)

    return LTPResponse(
        instrument_key=instrument_key,
        ltp=ltp,
        change=quote.change if quote else None,
        change_percent=quote.change_percent if quote else None,
        last_update=quote.last_update if quote else None,
        is_stale=is_stale,
        success=True
    )


@router.post("/ltps", response_model=LTPBatchResponse)
async def get_ltps_batch(request: LTPBatchRequest):
    """
    Get LTPs for multiple instruments in a single request.

    Maximum 500 instruments per request.
    Returns a dict mapping instrument_key to LTP (or null if not available).
    """
    store = get_market_store()

    if not store:
        return LTPBatchResponse(
            data={},
            count=0,
            success=False,
            error="Market data store not available"
        )

    ltps = store.get_ltps(request.instrument_keys)

    return LTPBatchResponse(
        data=ltps,
        count=len([v for v in ltps.values() if v is not None]),
        success=True
    )


@router.get("/ltps/all", response_model=LTPBatchResponse)
async def get_all_ltps():
    """
    Get all available LTPs from the store.

    Returns all instruments currently being tracked by WebSocket.
    """
    store = get_market_store()

    if not store:
        return LTPBatchResponse(
            data={},
            count=0,
            success=False,
            error="Market data store not available"
        )

    ltps = store.get_all_ltps()

    return LTPBatchResponse(
        data=ltps,
        count=len(ltps),
        success=True
    )


# ============================================================
# QUOTE ENDPOINTS
# ============================================================

@router.get("/quote/{instrument_key:path}", response_model=QuoteResponse)
async def get_quote(instrument_key: str):
    """
    Get full quote data for a single instrument.

    Includes OHLC, volume, bid/ask, and Greeks (for options).
    """
    store = get_market_store()

    if not store:
        return QuoteResponse(
            success=False,
            error="Market data store not available"
        )

    quote = store.get_quote(instrument_key)

    if quote is None:
        return QuoteResponse(
            data=None,
            success=True,
            error="No data available for this instrument"
        )

    return QuoteResponse(
        data=QuoteData(
            instrument_key=quote.instrument_key,
            ltp=quote.ltp,
            change=quote.change,
            change_percent=quote.change_percent,
            open=quote.open,
            high=quote.high,
            low=quote.low,
            close=quote.close,
            volume=quote.volume,
            oi=quote.oi,
            bid_price=quote.bid_price,
            ask_price=quote.ask_price,
            bid_qty=quote.bid_qty,
            ask_qty=quote.ask_qty,
            delta=quote.delta,
            theta=quote.theta,
            gamma=quote.gamma,
            vega=quote.vega,
            iv=quote.iv,
            last_update=quote.last_update
        ),
        success=True
    )


@router.post("/quotes", response_model=QuoteBatchResponse)
async def get_quotes_batch(request: QuoteBatchRequest):
    """
    Get full quotes for multiple instruments.

    Maximum 100 instruments per request.
    """
    store = get_market_store()

    if not store:
        return QuoteBatchResponse(
            data={},
            count=0,
            success=False,
            error="Market data store not available"
        )

    result = {}
    for key in request.instrument_keys:
        quote = store.get_quote(key)
        if quote:
            result[key] = QuoteData(
                instrument_key=quote.instrument_key,
                ltp=quote.ltp,
                change=quote.change,
                change_percent=quote.change_percent,
                open=quote.open,
                high=quote.high,
                low=quote.low,
                close=quote.close,
                volume=quote.volume,
                oi=quote.oi,
                bid_price=quote.bid_price,
                ask_price=quote.ask_price,
                bid_qty=quote.bid_qty,
                ask_qty=quote.ask_qty,
                delta=quote.delta,
                theta=quote.theta,
                gamma=quote.gamma,
                vega=quote.vega,
                iv=quote.iv,
                last_update=quote.last_update
            )
        else:
            result[key] = None

    return QuoteBatchResponse(
        data=result,
        count=len([v for v in result.values() if v is not None]),
        success=True
    )


# ============================================================
# STATUS ENDPOINTS
# ============================================================

@router.get("/status")
async def get_market_data_status():
    """
    Get status of market data system.

    Returns WebSocket connection status, data freshness, etc.
    """
    store = get_market_store()
    ws_manager = get_ws_manager()

    status = {
        "store": None,
        "websocket": None,
        "success": True
    }

    if store:
        health = store.get_health()
        status["store"] = {
            "total_instruments": health.total_instruments,
            "stale_instruments": health.stale_instruments,
            "total_updates": health.total_updates,
            "updates_per_second": health.updates_per_second,
            "oldest_data_age": health.oldest_data_age,
            "newest_data_age": health.newest_data_age
        }

    if ws_manager:
        ws_status = ws_manager.get_status()
        status["websocket"] = {
            "connected": ws_status.connected,
            "running": ws_status.running,
            "subscribed_instruments": ws_status.subscribed_instruments,
            "instruments_with_data": ws_status.instruments_with_data,
            "total_ticks_received": ws_status.total_ticks_received,
            "reconnect_count": ws_status.reconnect_count,
            "last_error": ws_status.last_error
        }

    return status


@router.get("/stale")
async def get_stale_instruments():
    """
    Get list of instruments with stale data (> 60 seconds old).
    """
    store = get_market_store()

    if not store:
        return {
            "stale_instruments": [],
            "count": 0,
            "success": False,
            "error": "Market data store not available"
        }

    stale = store.get_stale_instruments()

    return {
        "stale_instruments": stale,
        "count": len(stale),
        "success": True
    }


# ============================================================
# WEBSOCKET CONTROL ENDPOINTS
# ============================================================

@router.post("/websocket/start")
async def start_websocket():
    """
    Start the WebSocket connection to Upstox.

    Only works during market hours (9:15 AM - 3:30 PM IST).
    """
    ws_manager = get_ws_manager()

    if not ws_manager:
        raise HTTPException(
            status_code=500,
            detail="WebSocket manager not available"
        )

    try:
        from core.config import get_access_token
        from core.live_trading_manager import LiveTradingManager

        access_token = get_access_token()
        if not access_token:
            raise HTTPException(
                status_code=401,
                detail="No access token available. Please login first."
            )

        # Get instruments
        live_manager = LiveTradingManager()
        instruments = live_manager.get_active_instruments()
        instrument_keys = [key for key, _ in instruments]

        if not instrument_keys:
            raise HTTPException(
                status_code=400,
                detail="No active instruments found"
            )

        # Start WebSocket
        success = ws_manager.start(access_token, instrument_keys)

        return {
            "success": success,
            "message": "WebSocket started" if success else "Failed to start WebSocket",
            "instruments_subscribed": len(instrument_keys) if success else 0
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting WebSocket: {e}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.post("/websocket/stop")
async def stop_websocket():
    """
    Stop the WebSocket connection.
    """
    ws_manager = get_ws_manager()

    if not ws_manager:
        raise HTTPException(
            status_code=500,
            detail="WebSocket manager not available"
        )

    ws_manager.stop()

    return {
        "success": True,
        "message": "WebSocket stopped"
    }


@router.post("/websocket/subscribe")
async def subscribe_instruments(instrument_keys: list[str]):
    """
    Subscribe to additional instruments.
    """
    ws_manager = get_ws_manager()

    if not ws_manager:
        raise HTTPException(
            status_code=500,
            detail="WebSocket manager not available"
        )

    if not ws_manager.is_running:
        raise HTTPException(
            status_code=400,
            detail="WebSocket not running. Start it first."
        )

    ws_manager.subscribe(instrument_keys)

    return {
        "success": True,
        "message": f"Subscribed to {len(instrument_keys)} instruments"
    }


@router.post("/websocket/unsubscribe")
async def unsubscribe_instruments(instrument_keys: list[str]):
    """
    Unsubscribe from instruments.
    """
    ws_manager = get_ws_manager()

    if not ws_manager:
        raise HTTPException(
            status_code=500,
            detail="WebSocket manager not available"
        )

    ws_manager.unsubscribe(instrument_keys)

    return {
        "success": True,
        "message": f"Unsubscribed from {len(instrument_keys)} instruments"
    }
