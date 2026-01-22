# backend/routers/scanner.py
"""
Scanner Router
==============

Endpoints for background scanning operations.
Scans run asynchronously - start a scan and poll for results.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Optional, List
from datetime import datetime
import logging

from backend.models.schemas import (
    ScannerConfig,
    ScanStartRequest,
    ScanStartResponse,
    ScanStatusResponse,
    ScanResultsResponse,
    ScanListResponse,
    ScanListItem,
    ScanProgress,
    ScanStatus,
    SignalResult,
    SignalType
)
from backend.services.scanner_service import (
    ScannerService,
    ScanConfig,
    ScanStatus as ServiceScanStatus
)

logger = logging.getLogger(__name__)

router = APIRouter()


def get_scanner_service() -> ScannerService:
    """Get scanner service singleton"""
    return ScannerService.get_instance()


def convert_config(api_config: Optional[ScannerConfig]) -> ScanConfig:
    """Convert API config to service config"""
    if api_config is None:
        return ScanConfig()

    return ScanConfig(
        strategy=api_config.strategy,
        timeframe=api_config.timeframe,
        lookback_days=api_config.lookback_days,
        sl_mode=api_config.sl_mode,
        atr_mult=api_config.atr_mult,
        rr_ratio=api_config.rr_ratio,
        sl_pct=api_config.sl_pct,
        tp_pct=api_config.tp_pct,
        min_score=api_config.min_score,
        exclude_symbols=api_config.exclude_symbols,
        rebuild_resampled=api_config.rebuild_resampled,
        use_live_data=api_config.use_live_data
    )


# ============================================================
# SCAN ENDPOINTS
# ============================================================

@router.post("/start", response_model=ScanStartResponse)
async def start_scan(request: Optional[ScanStartRequest] = None):
    """
    Start a new scan in the background.

    The scan runs asynchronously. Use /status/{scan_id} to check progress
    and /results/{scan_id} to get results when complete.

    Returns immediately with a scan_id for tracking.
    """
    service = get_scanner_service()

    try:
        config = None
        instruments = None

        if request:
            config = convert_config(request.config)
            instruments = request.instruments

        scan_id = service.start_scan(config=config, instruments=instruments)

        return ScanStartResponse(
            scan_id=scan_id,
            status=ScanStatus.PENDING,
            message="Scan started successfully",
            started_at=datetime.now(),
            success=True
        )

    except RuntimeError as e:
        # Max concurrent scans reached
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting scan: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{scan_id}", response_model=ScanStatusResponse)
async def get_scan_status(scan_id: str):
    """
    Get the status and progress of a scan.

    Poll this endpoint to track scan progress.
    """
    service = get_scanner_service()
    status = service.get_scan_status(scan_id)

    if not status:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found")

    return ScanStatusResponse(
        scan_id=status["scan_id"],
        status=ScanStatus(status["status"]),
        progress=ScanProgress(
            current=status["progress"]["current"],
            total=status["progress"]["total"],
            percent=status["progress"]["percent"],
            current_symbol=status["progress"]["current_symbol"],
            phase=status["progress"]["phase"]
        ),
        started_at=datetime.fromisoformat(status["started_at"]),
        completed_at=datetime.fromisoformat(status["completed_at"]) if status["completed_at"] else None,
        duration_seconds=status["duration_seconds"],
        error=status["error"],
        success=True
    )


@router.get("/results/{scan_id}", response_model=ScanResultsResponse)
async def get_scan_results(scan_id: str):
    """
    Get the results of a completed scan.

    Returns full results including all signals found.
    """
    service = get_scanner_service()
    results = service.get_scan_results(scan_id)

    if not results:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found")

    # Convert signal dicts to SignalResult objects
    tradable = [
        SignalResult(
            symbol=s["symbol"],
            instrument_key=s["instrument_key"],
            signal_type=SignalType(s["signal_type"]),
            entry_price=s["entry_price"],
            sl_price=s["sl_price"],
            tp_price=s["tp_price"],
            score=s["score"],
            timestamp=datetime.fromisoformat(s["timestamp"]),
            squeeze_count=s.get("squeeze_count"),
            momentum=s.get("momentum"),
            trend_60m=s.get("trend_60m"),
            volume_ratio=s.get("volume_ratio")
        )
        for s in results["tradable_signals"]
    ]

    ready = [
        SignalResult(
            symbol=s["symbol"],
            instrument_key=s["instrument_key"],
            signal_type=SignalType(s["signal_type"]),
            entry_price=s["entry_price"],
            sl_price=s["sl_price"],
            tp_price=s["tp_price"],
            score=s["score"],
            timestamp=datetime.fromisoformat(s["timestamp"]),
            squeeze_count=s.get("squeeze_count"),
            momentum=s.get("momentum"),
            trend_60m=s.get("trend_60m"),
            volume_ratio=s.get("volume_ratio")
        )
        for s in results["ready_signals"]
    ]

    return ScanResultsResponse(
        scan_id=results["scan_id"],
        status=ScanStatus(results["status"]),
        config=ScannerConfig(**results["config"]),
        total_scanned=results["total_scanned"],
        signals_found=results["signals_found"],
        tradable_signals=tradable,
        ready_signals=ready,
        started_at=datetime.fromisoformat(results["started_at"]),
        completed_at=datetime.fromisoformat(results["completed_at"]) if results["completed_at"] else None,
        duration_seconds=results["duration_seconds"],
        success=True,
        error=results["error"]
    )


@router.post("/cancel/{scan_id}")
async def cancel_scan(scan_id: str):
    """
    Cancel a running scan.

    Only works for scans that are currently running.
    """
    service = get_scanner_service()
    success = service.cancel_scan(scan_id)

    if not success:
        job = service.get_scan(scan_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Scan {scan_id} not found")
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Scan {scan_id} is not running (status: {job.status.value})"
            )

    return {
        "success": True,
        "message": f"Scan {scan_id} cancellation requested"
    }


@router.get("/list", response_model=ScanListResponse)
async def list_scans():
    """
    List all scans (active and recent).

    Returns summary of each scan for dashboard display.
    """
    service = get_scanner_service()
    scans = service.get_all_scans()
    active = service.get_active_scans()

    items = [
        ScanListItem(
            scan_id=s["scan_id"],
            status=ScanStatus(s["status"]),
            strategy=s["strategy"],
            started_at=datetime.fromisoformat(s["started_at"]),
            completed_at=datetime.fromisoformat(s["completed_at"]) if s["completed_at"] else None,
            signals_found=s["signals_found"]
        )
        for s in scans
    ]

    return ScanListResponse(
        scans=items,
        active_count=len(active),
        total_count=len(scans),
        success=True
    )


@router.get("/active")
async def get_active_scans():
    """
    Get list of currently active (running or pending) scans.
    """
    service = get_scanner_service()
    active_ids = service.get_active_scans()

    return {
        "active_scans": active_ids,
        "count": len(active_ids),
        "success": True
    }


# ============================================================
# QUICK SCAN ENDPOINTS (Convenience)
# ============================================================

@router.post("/quick")
async def quick_scan():
    """
    Start a quick scan with default settings.

    Convenience endpoint for quick scans without configuration.
    """
    service = get_scanner_service()

    try:
        scan_id = service.start_scan()
        return {
            "scan_id": scan_id,
            "message": "Quick scan started",
            "success": True
        }
    except RuntimeError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting quick scan: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/quick/single/{symbol}")
async def quick_scan_single(symbol: str):
    """
    Scan a single symbol.

    Useful for checking signals on specific stocks.
    """
    service = get_scanner_service()

    try:
        # Get instrument key for symbol
        from core.database import get_db
        db = get_db()

        row = db.con.execute(
            "SELECT instrument_key FROM fo_stocks_master WHERE trading_symbol = ?",
            [symbol.upper()]
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")

        instrument_key = row[0]

        scan_id = service.start_scan(instruments=[instrument_key])

        return {
            "scan_id": scan_id,
            "symbol": symbol.upper(),
            "instrument_key": instrument_key,
            "message": "Single symbol scan started",
            "success": True
        }

    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting single scan: {e}")
        raise HTTPException(status_code=500, detail=str(e))
