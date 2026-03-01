"""All constants for the FTMO Challenge system. Single source of truth."""

from datetime import time
from enum import Enum

# ── FTMO Account Rules (immutable) ──────────────────────────────────
ACCOUNT_SIZE = 50_000.0
PROFIT_TARGET = 5_000.0          # 10%
DAILY_MAX_LOSS = 2_500.0         # 5%
MAX_OVERALL_LOSS = 5_000.0       # 10%
CHALLENGE_DAYS = 30

# ── Internal Risk Overlay (stricter than FTMO) ──────────────────────
RISK_PER_TRADE_PCT = 0.005       # 0.5% = $250
DAILY_STOP_PCT = 0.02            # 2% = $1,000
MAX_TRADES_PER_DAY = 2
MAX_CONSECUTIVE_LOSSES = 2
REDUCED_RISK_PCT = 0.003         # 0.3% after +4% equity gain
REDUCED_RISK_THRESHOLD = 0.04   # +4% equity gain triggers risk reduction

# ── Session Times (IST, UTC+5:30) ──────────────────────────────────
PRE_NY_START = time(16, 30)      # 4:30 PM IST
PRE_NY_END = time(18, 0)         # 6:00 PM IST (= NY open)
NY_START = time(18, 0)           # 6:00 PM IST
NY_END = time(20, 0)             # 8:00 PM IST (hard cutoff)

# ── Strategy Parameters ─────────────────────────────────────────────
SWEEP_ATR_MULT = 0.25            # Sweep extension threshold: 0.25 × M15 ATR
DISPLACEMENT_BODY_MULT = 1.2     # Displacement candle body ≥ 1.2 × M5 ATR
SL_BUFFER_ATR_MULT = 0.15        # SL buffer beyond sweep extreme
RR_RATIO = 2.0                   # Fixed 2R take profit
M15_ATR_PERIOD = 14
M5_ATR_PERIOD = 14

# ── Instrument ──────────────────────────────────────────────────────
SYMBOL = "US100"
TIMEFRAME = "5m"
POINT_VALUE = 1.0                # CFD: $1 per point per standard lot


class RiskStatus(Enum):
    GREEN = "GREEN"
    CAUTION = "CAUTION"
    STOP = "STOP"
