# backend/models/schemas.py
"""
Pydantic Models for API Request/Response
========================================

Defines all data models used in API endpoints.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


# ============================================================
# ENUMS
# ============================================================

class ScanStatus(str, Enum):
    """Scan job status"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SignalType(str, Enum):
    """Signal type"""
    LONG = "LONG"
    SHORT = "SHORT"


class SignalStatus(str, Enum):
    """Signal status"""
    ACTIVE = "active"
    TRIGGERED = "triggered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SubscriptionMode(str, Enum):
    """WebSocket subscription mode"""
    LTPC = "ltpc"
    FULL = "full"
    OPTION_GREEKS = "option_greeks"


# ============================================================
# MARKET DATA MODELS
# ============================================================

class LTPResponse(BaseModel):
    """Response for single LTP request"""
    instrument_key: str
    ltp: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    last_update: Optional[float] = None
    is_stale: bool = False
    success: bool = True
    error: Optional[str] = None


class LTPBatchRequest(BaseModel):
    """Request for batch LTP"""
    instrument_keys: List[str] = Field(..., min_length=1, max_length=500)


class LTPBatchResponse(BaseModel):
    """Response for batch LTP request"""
    data: Dict[str, Optional[float]]
    count: int
    success: bool = True
    error: Optional[str] = None


class QuoteData(BaseModel):
    """Full quote data"""
    instrument_key: str
    ltp: float
    change: float = 0.0
    change_percent: float = 0.0
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[int] = None
    oi: Optional[float] = None
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None
    bid_qty: Optional[int] = None
    ask_qty: Optional[int] = None
    # Greeks (for options)
    delta: Optional[float] = None
    theta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    iv: Optional[float] = None
    last_update: Optional[float] = None


class QuoteResponse(BaseModel):
    """Response for single quote request"""
    data: Optional[QuoteData] = None
    success: bool = True
    error: Optional[str] = None


class QuoteBatchRequest(BaseModel):
    """Request for batch quotes"""
    instrument_keys: List[str] = Field(..., min_length=1, max_length=100)


class QuoteBatchResponse(BaseModel):
    """Response for batch quotes request"""
    data: Dict[str, Optional[QuoteData]]
    count: int
    success: bool = True
    error: Optional[str] = None


# ============================================================
# SCANNER MODELS
# ============================================================

class ScannerConfig(BaseModel):
    """Configuration for a scan job"""
    # Strategy settings
    strategy: str = Field(default="squeeze_15m", description="Strategy to run")
    timeframe: str = Field(default="15minute", description="Primary timeframe")
    lookback_days: int = Field(default=60, ge=1, le=365)

    # SL/TP settings
    sl_mode: str = Field(default="ATR based", description="ATR based or Fixed %")
    atr_mult: float = Field(default=2.0, ge=0.5, le=5.0)
    rr_ratio: float = Field(default=2.0, ge=1.0, le=5.0)
    sl_pct: float = Field(default=1.0, ge=0.2, le=10.0)
    tp_pct: float = Field(default=2.0, ge=0.5, le=20.0)

    # Filter settings
    min_score: int = Field(default=4, ge=1, le=5)
    exclude_symbols: List[str] = Field(default_factory=list)

    # Execution settings
    rebuild_resampled: bool = Field(default=True)
    use_live_data: bool = Field(default=True)


class ScanStartRequest(BaseModel):
    """Request to start a scan"""
    config: Optional[ScannerConfig] = None
    instruments: Optional[List[str]] = None  # None = use all active F&O stocks


class ScanStartResponse(BaseModel):
    """Response when scan is started"""
    scan_id: str
    status: ScanStatus
    message: str
    started_at: datetime
    success: bool = True


class ScanProgress(BaseModel):
    """Scan progress information"""
    current: int = 0
    total: int = 0
    percent: float = 0.0
    current_symbol: Optional[str] = None
    phase: str = "initializing"  # initializing, resampling, scanning, finalizing


class ScanStatusResponse(BaseModel):
    """Response for scan status check"""
    scan_id: str
    status: ScanStatus
    progress: ScanProgress
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    success: bool = True


class SignalResult(BaseModel):
    """Individual signal from scan"""
    symbol: str
    instrument_key: str
    signal_type: SignalType
    entry_price: float
    sl_price: float
    tp_price: float
    score: int
    timestamp: datetime
    # Additional metrics
    squeeze_count: Optional[int] = None
    momentum: Optional[float] = None
    trend_60m: Optional[str] = None
    volume_ratio: Optional[float] = None


class ScanResultsResponse(BaseModel):
    """Response with scan results"""
    scan_id: str
    status: ScanStatus
    config: ScannerConfig
    # Results
    total_scanned: int = 0
    signals_found: int = 0
    tradable_signals: List[SignalResult] = []  # Score = 5
    ready_signals: List[SignalResult] = []     # Score = 4
    # Metadata
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    success: bool = True
    error: Optional[str] = None


class ScanListItem(BaseModel):
    """Summary of a scan for listing"""
    scan_id: str
    status: ScanStatus
    strategy: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    signals_found: int = 0


class ScanListResponse(BaseModel):
    """Response for scan list"""
    scans: List[ScanListItem]
    active_count: int
    total_count: int
    success: bool = True


# ============================================================
# SIGNAL MODELS
# ============================================================

class SignalCreate(BaseModel):
    """Request to create a signal"""
    symbol: str
    instrument_key: str
    signal_type: SignalType
    entry_price: float
    sl_price: float
    tp_price: float
    score: int = Field(default=5, ge=1, le=5)
    strategy: str = "manual"
    notes: Optional[str] = None


class SignalUpdate(BaseModel):
    """Request to update a signal"""
    entry_price: Optional[float] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    status: Optional[SignalStatus] = None
    notes: Optional[str] = None


class Signal(BaseModel):
    """Full signal model"""
    signal_id: str
    symbol: str
    instrument_key: str
    signal_type: SignalType
    status: SignalStatus
    entry_price: float
    sl_price: float
    tp_price: float
    current_price: Optional[float] = None
    pnl_percent: Optional[float] = None
    score: int
    strategy: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    triggered_at: Optional[datetime] = None
    notes: Optional[str] = None


class SignalListResponse(BaseModel):
    """Response for signal list"""
    signals: List[Signal]
    total: int
    active: int
    triggered: int
    success: bool = True


class SignalResponse(BaseModel):
    """Response for single signal operations"""
    signal: Optional[Signal] = None
    success: bool = True
    message: Optional[str] = None
    error: Optional[str] = None


# ============================================================
# WEBSOCKET MODELS
# ============================================================

class WSSubscribeRequest(BaseModel):
    """WebSocket subscribe request"""
    action: str = "subscribe"
    instrument_keys: List[str]
    mode: SubscriptionMode = SubscriptionMode.LTPC


class WSUnsubscribeRequest(BaseModel):
    """WebSocket unsubscribe request"""
    action: str = "unsubscribe"
    instrument_keys: List[str]


class WSTickMessage(BaseModel):
    """WebSocket tick message"""
    type: str = "tick"
    instrument_key: str
    ltp: float
    change: float = 0.0
    timestamp: float


class WSScanUpdateMessage(BaseModel):
    """WebSocket scan update message"""
    type: str = "scan_update"
    scan_id: str
    status: ScanStatus
    progress: ScanProgress


class WSErrorMessage(BaseModel):
    """WebSocket error message"""
    type: str = "error"
    error: str
    code: Optional[str] = None


# ============================================================
# SYSTEM MODELS
# ============================================================

class HealthResponse(BaseModel):
    """Health check response"""
    status: str  # healthy, degraded, unhealthy
    components: Dict[str, Dict[str, Any]]


class ErrorResponse(BaseModel):
    """Standard error response"""
    success: bool = False
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None
