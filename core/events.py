"""
Standardized Event Contracts
---------------------------
Frozen dataclasses for system-wide communication.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    EXIT = "EXIT"
    NEUTRAL = "NEUTRAL"


class TradeStatus(Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"


class OrderStatus(Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class OHLCVBar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class TradeStructuralContext:
    """Snapshot of market conditions at the time of signal generation."""
    regime_state: str
    regime_confidence: float
    session_type: str        # 'AM' or 'PM'
    dispersion_value: float
    dispersion_pct: float
    volatility_value: float
    volatility_pct: float
    breadth_ratio: float
    signal_rank: int
    signal_percentile: float
    sl_distance: float       # Mandatory absolute price distance to SL
    risk_r: float            # Mandatory risk unit
    model_version: str = "TLP_V1_CORE"
    universe_version: str = "NIFTY_UNIVERSE_V1"


@dataclass(frozen=True)
class SignalEvent:
    strategy_id: str
    symbol: str
    timestamp: datetime
    signal_type: SignalType
    confidence: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    context: Optional[TradeStructuralContext] = None



@dataclass(frozen=True)
class TradeEvent:
    trade_id: str
    signal_id_reference: str
    timestamp: datetime
    symbol: str
    status: TradeStatus
    direction: str
    quantity: float
    price: float
    fees: float = 0.0
    broker_reference_id: Optional[str] = None
    rejection_reason: Optional[str] = None


@dataclass(frozen=True)
class OrderEvent:
    order_id: str
    signal_id_reference: str
    timestamp: datetime
    symbol: str
    order_type: OrderType
    side: str
    quantity: float
    price: float
    stop_price: Optional[float] = None
    time_in_force: str = "DAY"
    status: OrderStatus = OrderStatus.CREATED
