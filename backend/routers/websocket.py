# backend/routers/websocket.py
"""
WebSocket Router
================

WebSocket endpoints for real-time data streaming.
Supports market data streaming and scan progress updates.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, Set, List
import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectionManager:
    """
    Manages WebSocket connections for broadcasting.
    """

    def __init__(self):
        # Market data subscribers: ws -> set of instrument_keys
        self.market_data_connections: Dict[WebSocket, Set[str]] = {}

        # Scanner subscribers: ws -> set of scan_ids (or "all")
        self.scanner_connections: Dict[WebSocket, Set[str]] = {}

        # Locks
        self._market_lock = asyncio.Lock()
        self._scanner_lock = asyncio.Lock()

    async def connect_market_data(self, websocket: WebSocket):
        """Accept market data connection"""
        await websocket.accept()
        async with self._market_lock:
            self.market_data_connections[websocket] = set()
        logger.info(f"Market data client connected: {id(websocket)}")

    async def connect_scanner(self, websocket: WebSocket):
        """Accept scanner connection"""
        await websocket.accept()
        async with self._scanner_lock:
            self.scanner_connections[websocket] = {"all"}  # Subscribe to all by default
        logger.info(f"Scanner client connected: {id(websocket)}")

    async def disconnect_market_data(self, websocket: WebSocket):
        """Remove market data connection"""
        async with self._market_lock:
            self.market_data_connections.pop(websocket, None)
        logger.info(f"Market data client disconnected: {id(websocket)}")

    async def disconnect_scanner(self, websocket: WebSocket):
        """Remove scanner connection"""
        async with self._scanner_lock:
            self.scanner_connections.pop(websocket, None)
        logger.info(f"Scanner client disconnected: {id(websocket)}")

    async def subscribe_market_data(self, websocket: WebSocket, instrument_keys: List[str]):
        """Subscribe to market data for instruments"""
        async with self._market_lock:
            if websocket in self.market_data_connections:
                self.market_data_connections[websocket].update(instrument_keys)
                logger.debug(f"Subscribed to {len(instrument_keys)} instruments")

    async def unsubscribe_market_data(self, websocket: WebSocket, instrument_keys: List[str]):
        """Unsubscribe from market data"""
        async with self._market_lock:
            if websocket in self.market_data_connections:
                self.market_data_connections[websocket] -= set(instrument_keys)
                logger.debug(f"Unsubscribed from {len(instrument_keys)} instruments")

    async def broadcast_tick(self, instrument_key: str, data: dict):
        """Broadcast tick to subscribed clients"""
        async with self._market_lock:
            disconnected = []

            for ws, subscriptions in self.market_data_connections.items():
                if instrument_key in subscriptions or not subscriptions:
                    try:
                        await ws.send_json({
                            "type": "tick",
                            "instrument_key": instrument_key,
                            **data
                        })
                    except Exception:
                        disconnected.append(ws)

            # Clean up disconnected
            for ws in disconnected:
                self.market_data_connections.pop(ws, None)

    async def broadcast_scan_update(self, scan_id: str, data: dict):
        """Broadcast scan update to subscribers"""
        async with self._scanner_lock:
            disconnected = []

            for ws, subscriptions in self.scanner_connections.items():
                if "all" in subscriptions or scan_id in subscriptions:
                    try:
                        await ws.send_json({
                            "type": "scan_update",
                            "scan_id": scan_id,
                            **data
                        })
                    except Exception:
                        disconnected.append(ws)

            # Clean up disconnected
            for ws in disconnected:
                self.scanner_connections.pop(ws, None)


# Global connection manager
manager = ConnectionManager()


# ============================================================
# MARKET DATA WEBSOCKET
# ============================================================

@router.websocket("/market-data")
async def websocket_market_data(websocket: WebSocket):
    """
    WebSocket endpoint for real-time market data.

    Messages from client:
    - {"action": "subscribe", "instrument_keys": ["NSE_EQ|..."]}
    - {"action": "unsubscribe", "instrument_keys": ["NSE_EQ|..."]}

    Messages to client:
    - {"type": "tick", "instrument_key": "...", "ltp": 123.45, ...}
    - {"type": "error", "error": "..."}
    """
    await manager.connect_market_data(websocket)

    # Start background task to push data
    push_task = asyncio.create_task(_push_market_data(websocket))

    try:
        while True:
            # Receive messages from client
            data = await websocket.receive_text()

            try:
                message = json.loads(data)
                action = message.get("action")

                if action == "subscribe":
                    keys = message.get("instrument_keys", [])
                    await manager.subscribe_market_data(websocket, keys)
                    await websocket.send_json({
                        "type": "subscribed",
                        "instrument_keys": keys
                    })

                elif action == "unsubscribe":
                    keys = message.get("instrument_keys", [])
                    await manager.unsubscribe_market_data(websocket, keys)
                    await websocket.send_json({
                        "type": "unsubscribed",
                        "instrument_keys": keys
                    })

                else:
                    await websocket.send_json({
                        "type": "error",
                        "error": f"Unknown action: {action}"
                    })

            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "error": "Invalid JSON"
                })

    except WebSocketDisconnect:
        pass
    finally:
        push_task.cancel()
        await manager.disconnect_market_data(websocket)


async def _push_market_data(websocket: WebSocket):
    """Background task to push market data to client"""
    try:
        from core.websocket import MarketDataStore
        store = MarketDataStore.get_instance()
    except ImportError:
        return

    last_push: Dict[str, float] = {}  # instrument_key -> last push time
    THROTTLE_MS = 100  # Minimum interval between pushes for same instrument

    while True:
        try:
            await asyncio.sleep(0.05)  # 50ms check interval

            # Get subscribed instruments
            subscriptions = manager.market_data_connections.get(websocket, set())
            if not subscriptions:
                continue

            now = time.time()

            for instrument_key in subscriptions:
                # Throttle
                last = last_push.get(instrument_key, 0)
                if (now - last) * 1000 < THROTTLE_MS:
                    continue

                # Get data
                ltp = store.get_ltp(instrument_key)
                if ltp is None:
                    continue

                quote = store.get_quote(instrument_key)
                if quote and quote.last_update > last:
                    last_push[instrument_key] = now

                    await websocket.send_json({
                        "type": "tick",
                        "instrument_key": instrument_key,
                        "ltp": quote.ltp,
                        "change": quote.change,
                        "change_percent": quote.change_percent,
                        "timestamp": quote.last_update
                    })

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in market data push: {e}")
            await asyncio.sleep(1)


# ============================================================
# SCANNER WEBSOCKET
# ============================================================

@router.websocket("/scanner")
async def websocket_scanner(websocket: WebSocket):
    """
    WebSocket endpoint for scan progress updates.

    Messages from client:
    - {"action": "subscribe", "scan_ids": ["abc123"]} or {"action": "subscribe", "scan_ids": ["all"]}
    - {"action": "unsubscribe", "scan_ids": ["abc123"]}

    Messages to client:
    - {"type": "scan_update", "scan_id": "...", "status": "...", "progress": {...}}
    - {"type": "scan_complete", "scan_id": "...", "signals_found": 5}
    - {"type": "error", "error": "..."}
    """
    await manager.connect_scanner(websocket)

    # Register callback for scan updates
    from backend.services.scanner_service import ScannerService

    service = ScannerService.get_instance()

    async def on_scan_update(scan_id: str, job):
        """Callback when scan updates"""
        try:
            await manager.broadcast_scan_update(scan_id, {
                "status": job.status.value,
                "progress": {
                    "current": job.progress.current,
                    "total": job.progress.total,
                    "percent": job.progress.percent,
                    "current_symbol": job.progress.current_symbol,
                    "phase": job.progress.phase
                },
                "signals_found": len(job.tradable_signals) + len(job.ready_signals)
            })
        except Exception as e:
            logger.error(f"Error broadcasting scan update: {e}")

    # Wrap async callback for sync scanner service
    def sync_callback(scan_id: str, job):
        asyncio.create_task(on_scan_update(scan_id, job))

    service.add_callback(sync_callback)

    try:
        while True:
            data = await websocket.receive_text()

            try:
                message = json.loads(data)
                action = message.get("action")

                if action == "subscribe":
                    scan_ids = message.get("scan_ids", ["all"])
                    async with manager._scanner_lock:
                        manager.scanner_connections[websocket] = set(scan_ids)
                    await websocket.send_json({
                        "type": "subscribed",
                        "scan_ids": scan_ids
                    })

                elif action == "unsubscribe":
                    scan_ids = message.get("scan_ids", [])
                    async with manager._scanner_lock:
                        if websocket in manager.scanner_connections:
                            manager.scanner_connections[websocket] -= set(scan_ids)
                    await websocket.send_json({
                        "type": "unsubscribed",
                        "scan_ids": scan_ids
                    })

                elif action == "start_scan":
                    # Allow starting scan via WebSocket
                    config = message.get("config")
                    try:
                        scan_id = service.start_scan()
                        await websocket.send_json({
                            "type": "scan_started",
                            "scan_id": scan_id
                        })
                    except RuntimeError as e:
                        await websocket.send_json({
                            "type": "error",
                            "error": str(e)
                        })

                else:
                    await websocket.send_json({
                        "type": "error",
                        "error": f"Unknown action: {action}"
                    })

            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "error": "Invalid JSON"
                })

    except WebSocketDisconnect:
        pass
    finally:
        service.remove_callback(sync_callback)
        await manager.disconnect_scanner(websocket)


# ============================================================
# SIMPLE PING/PONG
# ============================================================

@router.websocket("/ping")
async def websocket_ping(websocket: WebSocket):
    """
    Simple ping/pong WebSocket for connection testing.
    """
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_text()

            if data == "ping":
                await websocket.send_text("pong")
            else:
                await websocket.send_json({
                    "type": "echo",
                    "data": data,
                    "timestamp": time.time()
                })

    except WebSocketDisconnect:
        pass
