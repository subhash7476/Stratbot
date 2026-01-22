# backend/main.py
"""
FastAPI Main Application
========================

Entry point for the FastAPI backend server.
Provides REST API and WebSocket endpoints for the trading bot.

Run with:
    uvicorn backend.main:app --reload --port 8000

Or use run_backend.py for production.
"""

import sys
from pathlib import Path
from contextlib import asynccontextmanager
import logging

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import routers
from backend.routers import (
    market_data_router,
    scanner_router,
    signals_router,
    websocket_router
)

# Import services for startup/shutdown
from backend.services.scanner_service import ScannerService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Handles startup and shutdown events.
    """
    # Startup
    logger.info("Starting FastAPI backend...")

    # Initialize scanner service
    scanner_service = ScannerService.get_instance()
    logger.info("Scanner service initialized")

    # Try to start WebSocket if market is open
    try:
        from core.websocket import WebSocketManager
        from core.config import get_access_token

        access_token = get_access_token()
        if access_token:
            ws_manager = WebSocketManager.get_instance()
            # Note: WebSocket will auto-start during market hours when first used
            logger.info("WebSocket manager ready")
    except Exception as e:
        logger.warning(f"WebSocket initialization skipped: {e}")

    logger.info("FastAPI backend started successfully")

    yield  # Application runs here

    # Shutdown
    logger.info("Shutting down FastAPI backend...")

    # Stop any running scans
    scanner_service.cancel_all_scans()

    # Stop WebSocket
    try:
        from core.websocket import WebSocketManager
        ws_manager = WebSocketManager.get_instance()
        ws_manager.stop()
    except Exception:
        pass

    logger.info("FastAPI backend shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Trading Bot Pro API",
    description="""
    REST API and WebSocket endpoints for the Trading Bot Pro system.

    ## Features
    - Real-time market data (LTP, quotes)
    - Background scanning operations
    - Signal management
    - WebSocket streaming for live updates

    ## Authentication
    Currently uses the same access token as the main application.
    Token is read from config/credentials.json.
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS middleware - allow Streamlit and local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",      # Streamlit default
        "http://127.0.0.1:8501",
        "http://localhost:3000",       # React dev server
        "http://127.0.0.1:3000",
        "http://localhost:8000",       # FastAPI itself
        "http://127.0.0.1:8000",
        "*"                            # Allow all for development
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Add X-Process-Time header to all responses"""
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle all unhandled exceptions"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": str(exc),
            "detail": "An internal server error occurred"
        }
    )


# Include routers
app.include_router(
    market_data_router,
    prefix="/api/market",
    tags=["Market Data"]
)

app.include_router(
    scanner_router,
    prefix="/api/scanner",
    tags=["Scanner"]
)

app.include_router(
    signals_router,
    prefix="/api/signals",
    tags=["Signals"]
)

app.include_router(
    websocket_router,
    prefix="/ws",
    tags=["WebSocket"]
)


# Health check endpoint
@app.get("/health", tags=["System"])
async def health_check():
    """
    Health check endpoint.
    Returns system status and component health.
    """
    health_status = {
        "status": "healthy",
        "components": {}
    }

    # Check WebSocket
    try:
        from core.websocket import WebSocketManager
        ws_manager = WebSocketManager.get_instance()
        health_status["components"]["websocket"] = {
            "status": "running" if ws_manager.is_running else "stopped",
            "connected": ws_manager.is_connected
        }
    except Exception as e:
        health_status["components"]["websocket"] = {
            "status": "error",
            "error": str(e)
        }

    # Check database
    try:
        from core.database import get_db
        db = get_db()
        if db and db.con:
            db.con.execute("SELECT 1").fetchone()
            health_status["components"]["database"] = {"status": "connected"}
        else:
            health_status["components"]["database"] = {"status": "disconnected"}
    except Exception as e:
        health_status["components"]["database"] = {
            "status": "error",
            "error": str(e)
        }

    # Check scanner service
    try:
        scanner = ScannerService.get_instance()
        health_status["components"]["scanner"] = {
            "status": "ready",
            "active_scans": len(scanner.get_active_scans())
        }
    except Exception as e:
        health_status["components"]["scanner"] = {
            "status": "error",
            "error": str(e)
        }

    # Overall status
    has_errors = any(
        c.get("status") == "error"
        for c in health_status["components"].values()
    )
    health_status["status"] = "degraded" if has_errors else "healthy"

    return health_status


# Root endpoint
@app.get("/", tags=["System"])
async def root():
    """Root endpoint with API information"""
    return {
        "name": "Trading Bot Pro API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }


# API info endpoint
@app.get("/api", tags=["System"])
async def api_info():
    """API information and available endpoints"""
    return {
        "version": "1.0.0",
        "endpoints": {
            "market_data": {
                "ltp": "GET /api/market/ltp/{instrument_key}",
                "ltps": "POST /api/market/ltps",
                "quote": "GET /api/market/quote/{instrument_key}",
                "quotes": "POST /api/market/quotes"
            },
            "scanner": {
                "start": "POST /api/scanner/start",
                "status": "GET /api/scanner/status/{scan_id}",
                "results": "GET /api/scanner/results/{scan_id}",
                "cancel": "POST /api/scanner/cancel/{scan_id}",
                "list": "GET /api/scanner/list"
            },
            "signals": {
                "list": "GET /api/signals",
                "get": "GET /api/signals/{signal_id}",
                "create": "POST /api/signals",
                "update": "PUT /api/signals/{signal_id}",
                "delete": "DELETE /api/signals/{signal_id}"
            },
            "websocket": {
                "market_data": "WS /ws/market-data",
                "scanner": "WS /ws/scanner"
            }
        }
    }
