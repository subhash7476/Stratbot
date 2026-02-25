"""
V9 PM Scalper Strategy
======================
BullTrend day-type PM impulse strategy for Nifty 50 Futures.

Signal logic:
  - At the 13:00 checkpoint, the 13pm day-type model fires
  - If predicted_state == BullTrend AND confidence >= min_conf:
      enter LONG at the 13:02 bar open
  - Hard stop: 0.30% below entry, checked every 1m bar
  - Target: None — hold to time exit
  - Time exit: 14:45 IST (105 minutes after 13:00)
  - Cost assumed: 0.04% round-trip (Nifty futures, all-in)

Walk-forward results (v2 model, 2023–2025):
  2023: Sharpe=5.60  E=+0.057%/trade
  2024: Sharpe=2.91  E=+0.052%/trade
  2025: Sharpe=0.88  E=+0.017%/trade
  Overall: Sharpe=2.04, E=+0.035%/trade, MaxDD=-2.25%

Integration (TradingRunner):
  1. Instantiate with a shared DayTypeEngine:
       engine = DayTypeEngine()
       strategy = V9PmScalperStrategy("v9_pm", engine=engine)
  2. Register in STRATEGY_MAP (registry.py)
  3. Runner calls strategy.process_bar(bar, context) each 1m bar
  4. Strategy returns SignalEvent(BUY) on entry and SignalEvent(EXIT) on exit
  5. Runner passes EXIT signals to ExecutionHandler for flat-fill

Standalone paper trading:
  Use scripts/run_v9_paper.py — does not require the full Runner stack.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from core.events import OHLCVBar, SignalEvent, SignalType
from core.state.daytype_engine import DayTypeEngine, DayTypeState
from core.strategies.base import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

IST = ZoneInfo("Asia/Kolkata")

ENTRY_TIME   = dtime(13, 2)    # Enter at 13:02 open
EXIT_TIME    = dtime(14, 45)   # Time exit at 14:45
STOP_PCT     = 0.30            # Hard stop: 0.30% below entry
ROUND_TRIP   = 0.04            # Assumed futures round-trip cost (%)
DEFAULT_CONF = 0.75            # Minimum BullTrend confidence to trade
TRADE_DIR    = "BullTrend"     # Only trade bull side (BearTrend excluded)

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
PAPER_CSV = LOG_DIR / "v9_paper_trades.csv"

# CSV header
_CSV_HEADER = [
    "session_date", "entry_time", "entry_price", "stop_level",
    "exit_time", "exit_price", "exit_reason",
    "confidence", "predicted_state",
    "pnl_gross_pct", "pnl_net_pct",
    "model_version",
]


# ── State machine ─────────────────────────────────────────────────────────────

class _State:
    IDLE            = "IDLE"            # pre-13:00, feeding bars to engine
    AWAITING_ENTRY  = "AWAITING_ENTRY"  # BullTrend confirmed, waiting for 13:02 bar
    IN_POSITION     = "IN_POSITION"     # long open, monitoring stop + time exit
    DONE            = "DONE"            # exited or skipped for the day


# ── Strategy ──────────────────────────────────────────────────────────────────

class V9PmScalperStrategy(BaseStrategy):
    """
    V9 BullTrend PM Scalper — plugs into TradingRunner via BaseStrategy interface.

    Constructor args:
        strategy_id : str            — unique id for this strategy instance
        engine      : DayTypeEngine  — MUST be created with lock_threshold=1.01 so all
                                       three checkpoints (10am / 11am / 13pm) are allowed
                                       to fire.  With the default threshold (0.70) the
                                       engine locks at 10am on high-confidence days and
                                       the 13pm prediction — V9's only signal — is never
                                       emitted.
                                       Example:
                                         engine = DayTypeEngine(lock_threshold=1.01)
        config      : dict           — optional overrides:
            min_conf     (float)  : minimum BullTrend confidence, default 0.75
            stop_pct     (float)  : hard stop %, default 0.30
            paper_csv    (str)    : path for trade log CSV, default logs/v9_paper_trades.csv
            log_trades   (bool)   : write CSV on every exit, default True

    The caller (TradingRunner or standalone runner) is responsible for feeding
    bars to `engine.on_bar()` BEFORE calling `process_bar()`.  This keeps the
    engine stateless from the strategy's perspective and allows it to be shared
    with other strategies.
    """

    def __init__(
        self,
        strategy_id: str,
        engine: DayTypeEngine,
        config: Optional[dict] = None,
    ):
        super().__init__(strategy_id, config)
        self._engine      = engine

        # Guard: warn if engine may lock before the 13pm checkpoint fires
        if hasattr(engine, 'lock_threshold') and engine.lock_threshold < 1.0:
            logger.warning(
                "V9PmScalperStrategy: engine.lock_threshold=%.2f (<1.0) -- "
                "the engine may lock at 10am and never emit the 13pm checkpoint. "
                "Create the engine with DayTypeEngine(lock_threshold=1.01).",
                engine.lock_threshold,
            )
        self._min_conf    = float(self.config.get("min_conf",   DEFAULT_CONF))
        self._stop_pct    = float(self.config.get("stop_pct",   STOP_PCT))
        self._log_trades  = bool(self.config.get("log_trades",  True))
        self._paper_csv   = Path(self.config.get("paper_csv",   PAPER_CSV))

        # Per-day state
        self._sm_state: str              = _State.IDLE
        self._session_date: Optional[date] = None
        self._day_type_state: Optional[DayTypeState] = None

        # Position tracking
        self._entry_price: Optional[float]    = None
        self._stop_level:  Optional[float]    = None
        self._entry_time:  Optional[datetime] = None

        self._ensure_csv_header()
        logger.info(
            f"V9PmScalperStrategy '{strategy_id}' ready — "
            f"min_conf={self._min_conf}  stop={self._stop_pct}%  exit=14:45 IST"
        )

    # ── Public interface ──────────────────────────────────────────────────────

    def process_bar(self, bar: OHLCVBar, context: StrategyContext) -> Optional[SignalEvent]:
        """
        Called by TradingRunner for every 1m Nifty bar.

        Caller must have already fed the bar to DayTypeEngine via engine.on_bar()
        BEFORE calling this method.
        """
        bar_ts  = _to_ist(bar.timestamp)
        bar_date = bar_ts.date()
        bar_time = bar_ts.time()

        # Daily reset
        if bar_date != self._session_date:
            self._reset_day(bar_date)

        if self._sm_state == _State.DONE:
            return None

        # ── IDLE: watch for the 13pm checkpoint ───────────────────────────────
        if self._sm_state == _State.IDLE:
            state = self._engine.get_state()
            if state and state.checkpoint == "13pm":
                self._day_type_state = state
                if (state.predicted_state == TRADE_DIR
                        and state.confidence >= self._min_conf):
                    logger.info(
                        f"[{bar_date}] 13pm checkpoint: {state.predicted_state} "
                        f"conf={state.confidence:.0%} >= {self._min_conf:.0%} "
                        f"→ AWAITING_ENTRY"
                    )
                    self._sm_state = _State.AWAITING_ENTRY
                else:
                    logger.info(
                        f"[{bar_date}] 13pm checkpoint: {state.predicted_state} "
                        f"conf={state.confidence:.0%} — no trade today"
                    )
                    self._sm_state = _State.DONE
            return None

        # ── AWAITING_ENTRY: enter at 13:02 open ───────────────────────────────
        if self._sm_state == _State.AWAITING_ENTRY:
            if bar_time >= ENTRY_TIME:
                self._enter(bar)
                return SignalEvent(
                    strategy_id = self.strategy_id,
                    symbol      = bar.symbol,
                    timestamp   = bar.timestamp,
                    signal_type = SignalType.BUY,
                    confidence  = self._day_type_state.confidence,
                    metadata    = {
                        "entry_reason": "v9_bull_pm",
                        "stop_level":   round(self._stop_level, 4),
                        "exit_time":    "14:45 IST",
                        "day_type":     self._day_type_state.predicted_state,
                        "day_conf":     self._day_type_state.confidence,
                    },
                )
            return None

        # ── IN_POSITION: monitor stop and time exit ───────────────────────────
        if self._sm_state == _State.IN_POSITION:
            # 1. Hard stop (checked first — highest priority)
            if bar.low <= self._stop_level:
                exit_price = self._stop_level          # assume worst fill = stop level
                return self._build_exit(bar, exit_price, "stop_hit")

            # 2. Time exit at 14:45
            if bar_time >= EXIT_TIME:
                exit_price = bar.open                  # exit on open of 14:45 bar
                return self._build_exit(bar, exit_price, "time_exit")

        return None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _reset_day(self, session_date: date) -> None:
        self._session_date   = session_date
        self._sm_state       = _State.IDLE
        self._day_type_state = None
        self._entry_price    = None
        self._stop_level     = None
        self._entry_time     = None
        logger.debug(f"V9PmScalper reset for {session_date}")

    def _enter(self, bar: OHLCVBar) -> None:
        self._entry_price = bar.open
        self._stop_level  = round(self._entry_price * (1 - self._stop_pct / 100), 4)
        self._entry_time  = _to_ist(bar.timestamp)
        self._sm_state    = _State.IN_POSITION
        logger.info(
            f"[{self._session_date}] ENTER LONG @ {self._entry_price:.2f}  "
            f"stop={self._stop_level:.2f}  ({self._stop_pct}% risk)"
        )

    def _build_exit(
        self, bar: OHLCVBar, exit_price: float, reason: str
    ) -> SignalEvent:
        pnl_gross = (exit_price - self._entry_price) / self._entry_price * 100
        pnl_net   = pnl_gross - ROUND_TRIP
        exit_time = _to_ist(bar.timestamp)

        logger.info(
            f"[{self._session_date}] EXIT {reason} @ {exit_price:.2f}  "
            f"pnl_net={pnl_net:+.3f}%  "
            f"hold={_fmt_hold(self._entry_time, exit_time)}"
        )

        if self._log_trades:
            self._write_csv(exit_price, exit_time, reason, pnl_gross, pnl_net)

        self._sm_state = _State.DONE

        return SignalEvent(
            strategy_id = self.strategy_id,
            symbol      = bar.symbol,
            timestamp   = bar.timestamp,
            signal_type = SignalType.EXIT,
            confidence  = 1.0,
            metadata    = {
                "exit_reason":   reason,
                "close_all":     True,
                "pnl_gross_pct": round(pnl_gross, 4),
                "pnl_net_pct":   round(pnl_net, 4),
            },
        )

    # ── CSV logging ───────────────────────────────────────────────────────────

    def _ensure_csv_header(self) -> None:
        if not self._paper_csv.exists():
            with open(self._paper_csv, "w", newline="") as f:
                csv.writer(f).writerow(_CSV_HEADER)

    def _write_csv(
        self,
        exit_price: float,
        exit_time:  datetime,
        reason:     str,
        pnl_gross:  float,
        pnl_net:    float,
    ) -> None:
        state = self._day_type_state
        row = [
            str(self._session_date),
            str(self._entry_time.time()) if self._entry_time else "",
            round(self._entry_price, 4),
            round(self._stop_level,  4),
            str(exit_time.time()),
            round(exit_price, 4),
            reason,
            round(state.confidence,      4) if state else "",
            state.predicted_state             if state else "",
            round(pnl_gross, 4),
            round(pnl_net,   4),
            state.model_version               if state else "",
        ]
        with open(self._paper_csv, "a", newline="") as f:
            csv.writer(f).writerow(row)

    # ── State access (for dashboard / monitoring) ─────────────────────────────

    @property
    def sm_state(self) -> str:
        return self._sm_state

    @property
    def entry_price(self) -> Optional[float]:
        return self._entry_price

    @property
    def stop_level(self) -> Optional[float]:
        return self._stop_level

    @property
    def day_type_state(self) -> Optional[DayTypeState]:
        return self._day_type_state


# ── Utilities ─────────────────────────────────────────────────────────────────

def _to_ist(ts) -> datetime:
    """Convert any timestamp to IST-aware datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=IST)
        return ts.astimezone(IST)
    return datetime.fromisoformat(str(ts)).replace(tzinfo=IST)


def _fmt_hold(entry: Optional[datetime], exit_: datetime) -> str:
    if entry is None:
        return "?"
    mins = int((exit_ - entry).total_seconds() / 60)
    return f"{mins}m"
