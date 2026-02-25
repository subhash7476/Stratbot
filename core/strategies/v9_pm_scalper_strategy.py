"""
V9 PM Scalper Strategy
----------------------
Integrated version of the standalone run_v9_paper.py script.
Uses DayTypeEngine for 13:00 PM checkpoint signal generation.
"""
import logging
from datetime import date, datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from core.state.daytype_engine import DayTypeEngine, DayTypeState

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
SYMBOL = "NSE_INDEX|Nifty 50"

# -- Strategy constants -------------------------------------------------------
MIN_CONF     = 0.75   # BullTrend confidence threshold
STOP_PCT     = 0.30   # Hard stop % below entry
ROUND_TRIP   = 0.04   # Futures round-trip cost %
ENTRY_HOUR   = 13
ENTRY_MINUTE = 2      # Enter at 13:02
EXIT_HOUR    = 14
EXIT_MINUTE  = 45     # Exit at 14:45

class V9PMScalperStrategy:
    """
    V9 PM Scalper strategy driven by the 13:00 PM Day-Type checkpoint.
    """

    def __init__(self, db_manager):
        self.db = db_manager
        self.engine = DayTypeEngine(lock_threshold=1.01)
        
        # Session state
        self._session_date: Optional[date] = None
        self._sm: str = "IDLE"  # IDLE / AWAITING_ENTRY / IN_POSITION / DONE
        self._day_type: str = "Unknown"
        self._confidence: float = 0.0
        self._model_version: str = ""
        self._entry_time: Optional[datetime] = None
        self._entry_price: Optional[float] = None
        self._stop_level: Optional[float] = None
        self._exit_time: Optional[datetime] = None
        self._exit_price: Optional[float] = None
        self._exit_reason: str = ""
        self._pnl_gross: Optional[float] = None
        self._pnl_net: Optional[float] = None
        
        self._init_db()

    def _init_db(self):
        from core.database.schema import V9_PAPER_SIGNALS_SCHEMA, V9_PAPER_TRADES_SCHEMA
        try:
            with self.db.trading_writer() as conn:
                conn.execute(V9_PAPER_SIGNALS_SCHEMA)
                conn.execute(V9_PAPER_TRADES_SCHEMA)
        except Exception as exc:
            logger.error(f"[V9Strategy] DB init failed: {exc}")

    def on_bn_bar(self, bar: dict):
        """Feed one 1-minute BankNifty bar to the engine for Block H features.
        Must be called BEFORE on_bar() for the same timestamp."""
        ts = bar["timestamp"]
        ts_ist = ts.tz_localize(IST) if ts.tzinfo is None else ts.astimezone(IST)
        self.engine.on_bn_bar({
            "timestamp": ts_ist,
            "open":   float(bar["open"]),
            "high":   float(bar["high"]),
            "low":    float(bar["low"]),
            "close":  float(bar["close"]),
            "volume": float(bar.get("volume", 0)),
        })

    def on_bar(self, bar: dict):
        """Process one 1-minute Nifty bar."""
        ts = bar["timestamp"]
        ts_ist = ts.tz_localize(IST) if ts.tzinfo is None else ts.astimezone(IST)
        bar_date = ts_ist.date()

        # Session reset
        if self._session_date is None:
            self._session_date = bar_date
            self.engine.reset(bar_date)
        elif bar_date != self._session_date:
            self._reset_session(bar_date)

        # Feed bar to engine
        new_state: Optional[DayTypeState] = self.engine.on_bar({
            "timestamp": ts_ist,
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar.get("volume", 0)),
        })

        # Check for 13:00 checkpoint
        if new_state is not None and new_state.checkpoint == "13pm":
            self._day_type = new_state.predicted_state
            self._confidence = new_state.confidence
            self._model_version = new_state.model_version
            self._persist_signal()

            if self._sm == "IDLE":
                if self._day_type == "BullTrend" and self._confidence >= MIN_CONF:
                    self._sm = "AWAITING_ENTRY"
                    logger.info(f"[V9Strategy] SIGNAL: BullTrend conf={self._confidence:.0%}")
                else:
                    self._sm = "DONE"
                    logger.info(f"[V9Strategy] SKIP: {self._day_type} conf={self._confidence:.0%}")

        # AWAITING_ENTRY: enter at 13:02
        if self._sm == "AWAITING_ENTRY":
            bar_h, bar_m = ts_ist.hour, ts_ist.minute
            if (bar_h, bar_m) >= (ENTRY_HOUR, ENTRY_MINUTE):
                self._entry_price = float(bar["open"])
                self._stop_level = round(self._entry_price * (1 - STOP_PCT / 100), 4)
                self._entry_time = ts_ist
                self._sm = "IN_POSITION"
                self._persist_trade_entry()
                logger.info(f"[V9Strategy] ENTER LONG @ {self._entry_price:.2f}")

        # IN_POSITION: manage exit
        if self._sm == "IN_POSITION":
            bar_h, bar_m = ts_ist.hour, ts_ist.minute
            lo = float(bar["low"])

            # 1. Stop loss
            if lo <= self._stop_level:
                self._exit_price = self._stop_level
                self._exit_time = ts_ist
                self._exit_reason = "stop_hit"
                self._close_position()
                logger.info(f"[V9Strategy] STOP HIT @ {self._exit_price:.2f}")
                return

            # 2. Time exit
            if (bar_h, bar_m) >= (EXIT_HOUR, EXIT_MINUTE):
                self._exit_price = float(bar["open"])
                self._exit_time = ts_ist
                self._exit_reason = "time_exit"
                self._close_position()
                logger.info(f"[V9Strategy] TIME EXIT @ {self._exit_price:.2f}")
                return

    def _reset_session(self, new_date: date):
        self._session_date = new_date
        self.engine.reset(new_date)
        self._sm = "IDLE"
        self._day_type = "Unknown"
        self._confidence = 0.0
        self._model_version = ""
        self._entry_time = None
        self._entry_price = None
        self._stop_level = None
        self._exit_time = None
        self._exit_price = None
        self._exit_reason = ""
        self._pnl_gross = None
        self._pnl_net = None

    def _close_position(self):
        self._pnl_gross = (self._exit_price - self._entry_price) / self._entry_price * 100
        self._pnl_net = self._pnl_gross - ROUND_TRIP
        self._sm = "DONE"
        self._persist_trade_exit()

    def _persist_signal(self):
        try:
            with self.db.trading_writer() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO v9_paper_signals
                    (session_date, symbol, predicted_state, confidence, model_version, signal_time)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(self._session_date), SYMBOL, self._day_type,
                        self._confidence, self._model_version, self._to_str(datetime.now(IST))
                    ]
                )
        except Exception as exc:
            logger.error(f"[V9Strategy] Signal persistence failed: {exc}")

    def _persist_trade_entry(self):
        try:
            with self.db.trading_writer() as conn:
                conn.execute(
                    """
                    INSERT INTO v9_paper_trades
                    (session_date, entry_time, entry_price, stop_level, confidence, predicted_state, model_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(self._session_date), self._to_str(self._entry_time),
                        self._entry_price, self._stop_level, self._confidence,
                        self._day_type, self._model_version
                    ]
                )
        except Exception as exc:
            logger.error(f"[V9Strategy] Trade entry persistence failed: {exc}")

    def _persist_trade_exit(self):
        try:
            with self.db.trading_writer() as conn:
                conn.execute(
                    """
                    UPDATE v9_paper_trades
                    SET exit_time = ?, exit_price = ?, exit_reason = ?,
                        pnl_gross_pct = ?, pnl_net_pct = ?
                    WHERE session_date = ? AND exit_time IS NULL
                    """,
                    [
                        self._to_str(self._exit_time), self._exit_price, self._exit_reason,
                        self._pnl_gross, self._pnl_net, str(self._session_date)
                    ]
                )
        except Exception as exc:
            logger.error(f"[V9Strategy] Trade exit persistence failed: {exc}")

    def _to_str(self, ts) -> Optional[str]:
        if ts is None: return None
        if isinstance(ts, str): return ts
        return ts.isoformat()
