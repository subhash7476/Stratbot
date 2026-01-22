# backend/routers/signals.py
"""
Signals Router
==============

Endpoints for signal management (CRUD operations).
Signals are stored in the unified_signals table.
"""

import sys
from pathlib import Path

# Add project root to path for core imports
_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from datetime import datetime
import uuid
import logging

from backend.models.schemas import (
    Signal,
    SignalCreate,
    SignalUpdate,
    SignalResponse,
    SignalListResponse,
    SignalType,
    SignalStatus
)

logger = logging.getLogger(__name__)

router = APIRouter()


def get_db():
    """Get database connection"""
    try:
        from core.database import get_db as get_trading_db
        return get_trading_db()
    except ImportError as e:
        logger.error(f"Database module not available: {e}. Project root: {_project_root}, sys.path includes root: {str(_project_root) in sys.path}")
        return None


def ensure_signals_table():
    """Ensure unified_signals table exists"""
    db = get_db()
    if not db:
        return

    try:
        db.con.execute("""
            CREATE TABLE IF NOT EXISTS unified_signals (
                signal_id VARCHAR PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                instrument_key VARCHAR NOT NULL,
                signal_type VARCHAR NOT NULL,
                status VARCHAR DEFAULT 'active',
                entry_price DOUBLE NOT NULL,
                sl_price DOUBLE NOT NULL,
                tp_price DOUBLE NOT NULL,
                current_price DOUBLE,
                score INTEGER DEFAULT 5,
                strategy VARCHAR DEFAULT 'manual',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP,
                triggered_at TIMESTAMP,
                notes TEXT
            )
        """)
    except Exception as e:
        logger.warning(f"Could not ensure signals table: {e}")


# Ensure table exists on module load
ensure_signals_table()


# ============================================================
# SIGNAL CRUD ENDPOINTS
# ============================================================

@router.get("", response_model=SignalListResponse)
async def list_signals(
    status: Optional[SignalStatus] = None,
    strategy: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0)
):
    """
    List all signals with optional filtering.

    Query parameters:
    - status: Filter by signal status (active, triggered, expired, cancelled)
    - strategy: Filter by strategy name
    - limit: Maximum number of signals to return (default 100)
    - offset: Number of signals to skip for pagination
    """
    db = get_db()
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        # Build query
        query = "SELECT * FROM unified_signals WHERE 1=1"
        params = []

        if status:
            query += " AND status = ?"
            params.append(status.value)

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = db.con.execute(query, params).fetchall()
        columns = [desc[0] for desc in db.con.execute(query, params).description]

        signals = []
        for row in rows:
            row_dict = dict(zip(columns, row))
            signals.append(Signal(
                signal_id=row_dict["signal_id"],
                symbol=row_dict["symbol"],
                instrument_key=row_dict["instrument_key"],
                signal_type=SignalType(row_dict["signal_type"]),
                status=SignalStatus(row_dict.get("status", "active")),
                entry_price=row_dict["entry_price"],
                sl_price=row_dict["sl_price"],
                tp_price=row_dict["tp_price"],
                current_price=row_dict.get("current_price"),
                pnl_percent=None,  # Calculate if needed
                score=row_dict.get("score", 5),
                strategy=row_dict.get("strategy", "unknown"),
                created_at=row_dict["created_at"],
                updated_at=row_dict.get("updated_at"),
                triggered_at=row_dict.get("triggered_at"),
                notes=row_dict.get("notes")
            ))

        # Get counts
        total = db.con.execute(
            "SELECT COUNT(*) FROM unified_signals"
        ).fetchone()[0]

        active = db.con.execute(
            "SELECT COUNT(*) FROM unified_signals WHERE status = 'active'"
        ).fetchone()[0]

        triggered = db.con.execute(
            "SELECT COUNT(*) FROM unified_signals WHERE status = 'triggered'"
        ).fetchone()[0]

        return SignalListResponse(
            signals=signals,
            total=total,
            active=active,
            triggered=triggered,
            success=True
        )

    except Exception as e:
        logger.error(f"Error listing signals: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{signal_id}", response_model=SignalResponse)
async def get_signal(signal_id: str):
    """
    Get a single signal by ID.
    """
    db = get_db()
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        row = db.con.execute(
            "SELECT * FROM unified_signals WHERE signal_id = ?",
            [signal_id]
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")

        columns = [desc[0] for desc in db.con.execute(
            "SELECT * FROM unified_signals LIMIT 1"
        ).description]
        row_dict = dict(zip(columns, row))

        signal = Signal(
            signal_id=row_dict["signal_id"],
            symbol=row_dict["symbol"],
            instrument_key=row_dict["instrument_key"],
            signal_type=SignalType(row_dict["signal_type"]),
            status=SignalStatus(row_dict.get("status", "active")),
            entry_price=row_dict["entry_price"],
            sl_price=row_dict["sl_price"],
            tp_price=row_dict["tp_price"],
            current_price=row_dict.get("current_price"),
            pnl_percent=None,
            score=row_dict.get("score", 5),
            strategy=row_dict.get("strategy", "unknown"),
            created_at=row_dict["created_at"],
            updated_at=row_dict.get("updated_at"),
            triggered_at=row_dict.get("triggered_at"),
            notes=row_dict.get("notes")
        )

        return SignalResponse(signal=signal, success=True)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting signal: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=SignalResponse)
async def create_signal(request: SignalCreate):
    """
    Create a new signal.
    """
    db = get_db()
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        signal_id = str(uuid.uuid4())[:8]
        now = datetime.now()

        db.con.execute("""
            INSERT INTO unified_signals
            (signal_id, symbol, instrument_key, signal_type, status,
             entry_price, sl_price, tp_price, score, strategy, created_at, notes)
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
        """, [
            signal_id,
            request.symbol,
            request.instrument_key,
            request.signal_type.value,
            request.entry_price,
            request.sl_price,
            request.tp_price,
            request.score,
            request.strategy,
            now,
            request.notes
        ])

        signal = Signal(
            signal_id=signal_id,
            symbol=request.symbol,
            instrument_key=request.instrument_key,
            signal_type=request.signal_type,
            status=SignalStatus.ACTIVE,
            entry_price=request.entry_price,
            sl_price=request.sl_price,
            tp_price=request.tp_price,
            current_price=None,
            pnl_percent=None,
            score=request.score,
            strategy=request.strategy,
            created_at=now,
            updated_at=None,
            triggered_at=None,
            notes=request.notes
        )

        return SignalResponse(
            signal=signal,
            success=True,
            message=f"Signal {signal_id} created"
        )

    except Exception as e:
        logger.error(f"Error creating signal: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{signal_id}", response_model=SignalResponse)
async def update_signal(signal_id: str, request: SignalUpdate):
    """
    Update an existing signal.

    Only provided fields will be updated.
    """
    db = get_db()
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        # Check if signal exists
        existing = db.con.execute(
            "SELECT * FROM unified_signals WHERE signal_id = ?",
            [signal_id]
        ).fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")

        # Build update query
        updates = []
        params = []

        if request.entry_price is not None:
            updates.append("entry_price = ?")
            params.append(request.entry_price)

        if request.sl_price is not None:
            updates.append("sl_price = ?")
            params.append(request.sl_price)

        if request.tp_price is not None:
            updates.append("tp_price = ?")
            params.append(request.tp_price)

        if request.status is not None:
            updates.append("status = ?")
            params.append(request.status.value)

            if request.status == SignalStatus.TRIGGERED:
                updates.append("triggered_at = ?")
                params.append(datetime.now())

        if request.notes is not None:
            updates.append("notes = ?")
            params.append(request.notes)

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        updates.append("updated_at = ?")
        params.append(datetime.now())

        params.append(signal_id)

        db.con.execute(
            f"UPDATE unified_signals SET {', '.join(updates)} WHERE signal_id = ?",
            params
        )

        # Fetch updated signal
        return await get_signal(signal_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating signal: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{signal_id}")
async def delete_signal(signal_id: str):
    """
    Delete a signal.
    """
    db = get_db()
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        # Check if signal exists
        existing = db.con.execute(
            "SELECT * FROM unified_signals WHERE signal_id = ?",
            [signal_id]
        ).fetchone()

        if not existing:
            raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")

        db.con.execute(
            "DELETE FROM unified_signals WHERE signal_id = ?",
            [signal_id]
        )

        return {
            "success": True,
            "message": f"Signal {signal_id} deleted"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting signal: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# BULK OPERATIONS
# ============================================================

@router.post("/bulk")
async def create_signals_bulk(signals: List[SignalCreate]):
    """
    Create multiple signals at once.

    Maximum 100 signals per request.
    """
    if len(signals) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 signals per request")

    db = get_db()
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        created = []
        now = datetime.now()

        for request in signals:
            signal_id = str(uuid.uuid4())[:8]

            db.con.execute("""
                INSERT INTO unified_signals
                (signal_id, symbol, instrument_key, signal_type, status,
                 entry_price, sl_price, tp_price, score, strategy, created_at, notes)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
            """, [
                signal_id,
                request.symbol,
                request.instrument_key,
                request.signal_type.value,
                request.entry_price,
                request.sl_price,
                request.tp_price,
                request.score,
                request.strategy,
                now,
                request.notes
            ])

            created.append(signal_id)

        return {
            "success": True,
            "created": len(created),
            "signal_ids": created
        }

    except Exception as e:
        logger.error(f"Error creating signals bulk: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/bulk")
async def delete_signals_bulk(signal_ids: List[str]):
    """
    Delete multiple signals at once.
    """
    if len(signal_ids) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 signals per request")

    db = get_db()
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        deleted = 0
        for signal_id in signal_ids:
            result = db.con.execute(
                "DELETE FROM unified_signals WHERE signal_id = ?",
                [signal_id]
            )
            deleted += 1

        return {
            "success": True,
            "deleted": deleted
        }

    except Exception as e:
        logger.error(f"Error deleting signals bulk: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clear")
async def clear_signals(
    status: Optional[SignalStatus] = None,
    strategy: Optional[str] = None
):
    """
    Clear signals with optional filtering.

    If no filters provided, clears ALL signals.
    """
    db = get_db()
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        query = "DELETE FROM unified_signals WHERE 1=1"
        params = []

        if status:
            query += " AND status = ?"
            params.append(status.value)

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        db.con.execute(query, params)

        return {
            "success": True,
            "message": "Signals cleared"
        }

    except Exception as e:
        logger.error(f"Error clearing signals: {e}")
        raise HTTPException(status_code=500, detail=str(e))
