"""All constants for the FTMO Challenge system. Single source of truth."""

from datetime import time
from enum import Enum

# ── FTMO Account Rules ($100K Challenge, immutable) ─────────────────
ACCOUNT_SIZE = 100_000.0
PROFIT_TARGET = 10_000.0         # 10% Phase 1
DAILY_MAX_LOSS = 5_000.0         # 5%
MAX_OVERALL_LOSS = 10_000.0      # 10%
CHALLENGE_DAYS = 30

# ── Internal Risk Overlay (stricter than FTMO) ──────────────────────
RISK_PER_TRADE_PCT = 0.01        # 1.0% = $1,000 per trade
DAILY_STOP_PCT = 0.02            # 2% = $2,000 internal daily stop
MAX_TRADES_PER_DAY = 3           # London session: up to 3 setups/day
MAX_CONSECUTIVE_LOSSES = 2
REDUCED_RISK_PCT = 0.003         # 0.3% after +4% equity gain
REDUCED_RISK_THRESHOLD = 0.04   # +4% equity gain triggers risk reduction

# ── Session Times (IST, UTC+5:30) — XAUUSD Dual-Session Strategy ───
# SESSION 1: Asian range → London sweep
PRE_NY_START = time(9, 30)       # Late Asian session (tighter range — fewer false sweeps)
PRE_NY_END = time(13, 30)        # London open (pre-session range lock)
NY_START = time(13, 30)          # London open (trading window start)
NY_END = time(19, 0)             # End of London / start of NY overlap

# SESSION 2: London range → NY open sweep
PRE_NY2_START = time(13, 30)     # London range formation start
PRE_NY2_END = time(19, 0)        # NY open (pre-session range lock)
NY2_START = time(19, 0)          # NY open (trading window start)
NY2_END = time(23, 0)            # NY session close (11 PM IST = 5:30 PM ET)

# ── Strategy Parameters ─────────────────────────────────────────────
SWEEP_ATR_MULT = 0.25            # Sweep extension threshold: 0.25 × M15 ATR
DISPLACEMENT_BODY_MULT = 1.2     # Displacement candle body ≥ 1.2 × M5 ATR
SL_BUFFER_ATR_MULT = 0.15        # SL buffer beyond sweep extreme
RR_RATIO = 2.0                   # Fixed 2R take profit
M15_ATR_PERIOD = 14
M5_ATR_PERIOD = 14

# ── Instrument: XAUUSD (Gold) ────────────────────────────────────────
# Rationale: ICT liquidity sweep on Asian range is most reliable on Gold.
# London open (~70% of days) aggressively sweeps Asian H/L before reversing.
# 1 standard lot = 100 oz; $1 price move = $100 PnL per lot.
SYMBOL = "XAUUSD"
TIMEFRAME = "M5"
POINT_VALUE = 100.0              # 1 lot = 100 oz → $1 price move = $100/lot


class RiskStatus(Enum):
    GREEN = "GREEN"
    CAUTION = "CAUTION"
    STOP = "STOP"
