"""
Stock Day-Type Paper Trading Strategy
--------------------------------------
Classifies all Nifty 50 equity stocks at the 10:00 AM checkpoint (bar 45)
using a trained LogisticRegression model on three lightweight features:
  e_ret       - return from session open to 10:00 close
  e_range     - H-L range from open to 10:00 / session open
  e_close_loc - 10:00 close location in the 9:15-10:00 H-L range (0=low, 1=high)

Trade logic:
  Signal : BullTrend -> LONG  |  BearTrend -> SHORT
  Entry  : bar 47 open (~10:01 IST) -- catches the full 10:00-15:28 trend
  SL     : 1% from entry (configurable)
  TP     : 2% from entry (configurable) -- hard exit at target
  Trail  : SL trails once price moves 1x SL distance in favour
  Exit   : bar 374 close (~15:28 IST), or SL/TP/trail hit

Position sizing:
  max_capital        = Rs 50,000 per trade
  max_open_positions = 10 simultaneous positions (hard cap)
  qty = floor(max_capital / entry_price), minimum 1 share

All signals and trades are persisted to trading.db (SQLite).
TLP V1 context columns captured per trade (regime, dispersion, MAE/MFE).
"""
import logging
import numpy as np
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# -- Timing constants (0-indexed bar count from session open 9:15) -----------
CHECKPOINT_BARS  = 45    # bar index 44 inclusive (9:59/10:00 AM)
ENTRY_BARS       = 47    # bar index 46 -- enter at open of this bar (~10:01 AM)
DISPERSION_BARS  = 105   # bar index 104 -- 11:00 AM dispersion snapshot
EXIT_BARS        = 374   # bar index 373 -- exit at close (~15:28)

# -- TLP context: data path constants ----------------------------------------
_ROOT       = Path(__file__).resolve().parent.parent.parent
_CANDLE_1M  = _ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
_CANDLE_1D  = _ROOT / "data" / "market_data" / "nse" / "candles" / "1d"
INDEX_SYMBOL = "NSE_INDEX|Nifty 50"

# -- Broker cost models -------------------------------------------------------
BROKERS = {
    "paper":           {"label": "Paper (No Costs)",      "cost_pct": 0.0},
    "upstox_eq":       {"label": "Upstox Equity",         "cost_pct": 0.0004},
    "zerodha_eq":      {"label": "Zerodha Equity",        "cost_pct": 0.00035},
    "upstox_futures":  {"label": "Upstox Futures",        "cost_pct": 0.00020},
    "zerodha_futures": {"label": "Zerodha Futures",       "cost_pct": 0.00020},
}

# -- Cluster label mapping (model predicts int, strategy uses strings) --------
CLUSTER_NAMES = {0: "BearTrend", 1: "BullTrend", 2: "Choppy"}


class StockDaytypePaperStrategy:
    """
    Multi-symbol paper trading strategy driven by the Stock-Level
    Day-Type Classifier (10:00 AM checkpoint, LogisticRegression).
    """

    def __init__(
        self,
        db_manager,
        model_dir: Path,
        symbols: List[str],
        symbol_names: Dict[str, str] = None,
        broker: str = "paper",
        stop_pct: float = 0.01,
        target_pct: float = 0.02,
        max_capital: float = 50000.0,
        min_confidence: float = 0.50,
        max_open_positions: int = 10,
    ):
        """
        Parameters
        ----------
        db_manager         : DatabaseManager instance
        model_dir          : path to models/daytype/stock_1000am/
        symbols            : list of instrument keys (e.g. NSE_EQ|INE002A01018)
        symbol_names       : optional dict mapping instrument_key -> trading_symbol
        broker             : fee model key (see BROKERS dict)
        stop_pct           : stop distance as fraction (0.01 = 1%)
        target_pct         : target profit as fraction (0.02 = 2%)
        max_capital        : max Rs per trade for position sizing
        min_confidence     : minimum model confidence to enter (0.50 = 50%)
        max_open_positions : max simultaneous open positions across all symbols
        """
        import joblib

        self.db                 = db_manager
        self.broker             = broker if broker in BROKERS else "paper"
        self.stop_pct           = stop_pct
        self.target_pct         = target_pct
        self.max_capital        = max_capital
        self.min_confidence     = min_confidence
        self.max_open_positions = max_open_positions
        self.symbols            = list(symbols)
        self.symbol_names       = symbol_names or {}

        # Load model, optional scaler, and feature list
        self.model          = joblib.load(model_dir / "model.joblib")
        self._feature_names = joblib.load(model_dir / "features.joblib")
        scaler_path         = model_dir / "scaler.joblib"
        self._scaler        = joblib.load(scaler_path) if scaler_path.exists() else None

        # -- Per-symbol session state -----------------------------------------
        self._bars:      Dict[str, List[dict]] = {s: [] for s in symbols}
        self._signals:   Dict[str, dict]       = {}   # symbol -> signal dict
        self._positions: Dict[str, dict]       = {}   # symbol -> open position
        self._session_date: Optional[date]     = None
        self._signals_restored_from_db: bool   = False  # one-shot per session

        # -- TLP V1 session state ---------------------------------------------
        self._regime_state:   Optional[str]  = None   # EXPANSION|CONTRACTION|SHOCK
        self._regime_loaded:  bool           = False
        self._dispersion_done: bool          = False

        # -- Initialise DB tables & close stale positions ---------------------
        self._init_db()
        self._close_stale_positions()
        self._restore_open_positions_from_db()
        logger.info(
            f"[PaperTrading] Strategy ready | {len(symbols)} symbols | "
            f"broker={broker} | SL={stop_pct*100:.1f}% | TP={target_pct*100:.1f}% | "
            f"max_capital=Rs {max_capital:,.0f} | max_pos={max_open_positions} | "
            f"min_conf={min_confidence:.0%}"
        )

    # -----------------------------------------------------------------------
    #  Public interface
    # -----------------------------------------------------------------------

    def set_broker(self, broker: str) -> bool:
        """Change the broker / cost model at runtime."""
        if broker not in BROKERS:
            return False
        self.broker = broker
        logger.info(f"[PaperTrading] Broker changed to {broker}")
        return True

    def on_bar(self, symbol: str, bar: dict) -> None:
        """
        Process one 1-minute bar for the given symbol.
        Bar dict must have keys: timestamp, open, high, low, close, volume.
        """
        if symbol not in self._bars:
            return

        bars = self._bars[symbol]

        # -- Session date management -----------------------------------------
        bar_date = bar["timestamp"].date() if hasattr(bar["timestamp"], "date") else bar["timestamp"]
        if self._session_date is None:
            self._session_date = bar_date
        elif bar_date != self._session_date:
            self._reset_session(bar_date)
            bars = self._bars[symbol]

        bars.append(bar)
        bar_idx = len(bars) - 1   # 0-indexed

        # -- One-shot DB restore: repopulate _signals if empty mid-session ---
        # Handles post-restart / post-reset states where in-memory _signals
        # was cleared but the DB already has today's classifications.
        if (not self._signals
                and self._session_date is not None
                and not self._signals_restored_from_db):
            self._signals_restored_from_db = True
            self._restore_signals_from_db()

        # -- TLP: Load regime once at bar 43 (one bar before checkpoint) -----
        if bar_idx == CHECKPOINT_BARS - 2 and not self._regime_loaded:
            self._load_regime_snapshot()

        # -- Step 1: Checkpoint classify at bar 45 (index 44) ---------------
        if bar_idx == CHECKPOINT_BARS - 1 and symbol not in self._signals:
            self._classify(symbol, bars)

        # -- Step 2: Enter position at bar 47 (index 46) --------------------
        if bar_idx == ENTRY_BARS - 1 and symbol in self._signals:
            sig = self._signals[symbol]
            if sig["predicted_state"] in ("BullTrend", "BearTrend"):
                if sig["confidence"] >= self.min_confidence:
                    if symbol not in self._positions:
                        self._enter(symbol, bar, sig)
                else:
                    logger.debug(
                        f"[PaperTrading] SKIP {sig['trading_symbol']}: "
                        f"conf={sig['confidence']:.2f} < min_conf={self.min_confidence:.2f}"
                    )

        # -- TLP: Dispersion snapshot at bar 105 (11:00 AM, once per session)
        if bar_idx == DISPERSION_BARS - 1 and not self._dispersion_done:
            self._dispersion_done = True
            self._update_dispersion_context()

        # -- Step 3: Manage open position (trailing SL, SL/TP, time exit) ---
        if symbol in self._positions:
            self._manage_position(symbol, bar, bar_idx)

    def get_signals(self) -> List[dict]:
        """Return today's signal dict for all tracked symbols."""
        out = []
        for symbol in self.symbols:
            sig = self._signals.get(symbol)
            if sig:
                out.append(sig)
            else:
                out.append({
                    "symbol":          symbol,
                    "trading_symbol":  self.symbol_names.get(symbol, symbol.split("|")[-1]),
                    "predicted_state": "PENDING",
                    "confidence":      None,
                    "session_date":    str(self._session_date) if self._session_date else None,
                })
        return out

    def get_positions(self) -> List[dict]:
        """Return currently open positions (shallow copy)."""
        return [dict(p) for p in self._positions.values()]

    # -----------------------------------------------------------------------
    #  Private helpers
    # -----------------------------------------------------------------------

    def _reset_session(self, new_date: date) -> None:
        """
        Bug fix: persist exits for all open positions before clearing state.
        Previously this silently discarded open positions (exit_time = NULL forever).
        """
        # Close any open positions with session_reset reason
        for symbol in list(self._positions.keys()):
            last_bars = self._bars.get(symbol, [])
            if last_bars:
                last_bar = last_bars[-1]
                self._exit(symbol, last_bar, "session_reset",
                           exit_price=float(last_bar["close"]))
            else:
                # No bars available — exit at entry price (zero PnL)
                pos = self._positions.get(symbol)
                if pos:
                    fake_bar = {
                        "timestamp": pos["entry_time"],
                        "open":      pos["entry_price"],
                        "high":      pos["entry_price"],
                        "low":       pos["entry_price"],
                        "close":     pos["entry_price"],
                        "volume":    0,
                    }
                    self._exit(symbol, fake_bar, "session_reset",
                               exit_price=pos["entry_price"])

        logger.info(f"[PaperTrading] New session: {new_date}")
        self._session_date             = new_date
        self._bars                     = {s: [] for s in self.symbols}
        self._signals                  = {}
        self._positions                = {}
        self._signals_restored_from_db = False
        # TLP resets
        self._regime_state    = None
        self._regime_loaded   = False
        self._dispersion_done = False
        # NOTE: do NOT call _restore_signals_from_db() here.
        # The one-shot check in on_bar handles restore safely.
        # Calling it inside _reset_session triggered cascade re-entries:
        # each reset restored signals → bar 46 re-entered → next reset closed & re-entered.

    def _classify(self, symbol: str, bars: List[dict]) -> None:
        """Run the LogisticRegression at checkpoint and persist the signal."""
        if len(bars) < CHECKPOINT_BARS:
            return
        try:
            session_open = float(bars[0]["open"])
            last_close   = float(bars[CHECKPOINT_BARS - 1]["close"])
            highs = [float(b["high"]) for b in bars[:CHECKPOINT_BARS]]
            lows  = [float(b["low"])  for b in bars[:CHECKPOINT_BARS]]
            max_h = max(highs)
            min_l = min(lows)

            e_ret       = (last_close - session_open) / session_open if session_open else 0.0
            e_range     = (max_h - min_l) / session_open if session_open else 0.0
            hl_span     = max_h - min_l
            e_close_loc = (last_close - min_l) / hl_span if hl_span > 1e-9 else 0.5

            X = np.array([[e_ret, e_range, e_close_loc]])
            if self._scaler is not None:
                X = self._scaler.transform(X)
            raw_pred = self.model.predict(X)[0]
            proba    = self.model.predict_proba(X)[0]

            pred = CLUSTER_NAMES.get(int(raw_pred), str(raw_pred))
            class_proba = {}
            for cls_id, p in zip(self.model.classes_, proba.tolist()):
                name = CLUSTER_NAMES.get(int(cls_id), str(cls_id))
                class_proba[name] = p

            confidence  = float(max(proba))
            signal_time = bars[CHECKPOINT_BARS - 1]["timestamp"]
            trading_sym = self.symbol_names.get(symbol, symbol.split("|")[-1])

            signal = {
                "symbol":          symbol,
                "trading_symbol":  trading_sym,
                "predicted_state": pred,
                "confidence":      confidence,
                "p_bull":          class_proba.get("BullTrend", 0.0),
                "p_bear":          class_proba.get("BearTrend", 0.0),
                "p_choppy":        class_proba.get("Choppy",    0.0),
                "signal_time":     signal_time,
                "c_ret":           round(e_ret,       6),
                "c_range":         round(e_range,     6),
                "c_close_loc":     round(e_close_loc, 4),
                "session_date":    str(self._session_date),
                "broker":          self.broker,
            }
            self._signals[symbol] = signal
            self._persist_signal(signal)
            self._rank_signals()   # Re-rank after each new signal (idempotent)

            emoji = "+" if pred == "BullTrend" else ("-" if pred == "BearTrend" else "~")
            logger.info(
                f"[PaperTrading] {trading_sym:12s} {emoji}{pred:10s} "
                f"conf={confidence:.2f} cloc={e_close_loc:.3f} ret={e_ret*100:+.2f}%"
            )
        except Exception as exc:
            logger.error(f"[PaperTrading] Classify failed for {symbol}: {exc}")

    def _rank_signals(self) -> None:
        """
        Rank all signals by confidence (descending).
        Assigns signal_rank (1=best) and signal_percentile (0-100) to each signal dict.
        Called after every _classify() — idempotent re-ranking.
        """
        if not self._signals:
            return
        sorted_sigs = sorted(
            self._signals.items(),
            key=lambda x: x[1].get("confidence", 0),
            reverse=True,
        )
        n = len(sorted_sigs)
        for rank_0, (sym, sig) in enumerate(sorted_sigs):
            sig["signal_rank"]       = rank_0 + 1
            sig["signal_percentile"] = round((n - rank_0) / n * 100, 1)

    def _restore_signals_from_db(self) -> None:
        """
        Re-populate _signals from the DB for today's session.
        Called after a mid-session reset (stale-data cascade) or on startup
        when the checkpoint has already fired but in-memory state was cleared.
        Skips symbols that already have a freshly-computed in-memory signal.
        """
        if self._session_date is None:
            return
        try:
            with self.db.trading_writer() as conn:
                rows = conn.execute(
                    """
                    SELECT symbol, trading_symbol, predicted_state, confidence,
                           p_bull, p_bear, p_choppy, signal_time,
                           c_ret, c_range, c_close_loc, broker
                    FROM stock_paper_signals
                    WHERE session_date = ?
                    """,
                    [str(self._session_date)],
                ).fetchall()
            restored = 0
            for row in rows:
                sym = row[0]
                if sym in self._signals:
                    continue  # don't overwrite a freshly-computed signal
                self._signals[sym] = {
                    "symbol":          sym,
                    "trading_symbol":  row[1],
                    "predicted_state": row[2],
                    "confidence":      float(row[3]) if row[3] is not None else None,
                    "p_bull":          float(row[4]) if row[4] is not None else None,
                    "p_bear":          float(row[5]) if row[5] is not None else None,
                    "p_choppy":        float(row[6]) if row[6] is not None else None,
                    "signal_time":     row[7],
                    "c_ret":           float(row[8]) if row[8] is not None else None,
                    "c_range":         float(row[9]) if row[9] is not None else None,
                    "c_close_loc":     float(row[10]) if row[10] is not None else None,
                    "session_date":    str(self._session_date),
                    "broker":          row[11] or self.broker,
                }
                restored += 1
            if restored:
                self._signals_restored_from_db = True
                self._rank_signals()
                logger.info(
                    f"[PaperTrading] Restored {restored} signals from DB for {self._session_date}"
                )
        except Exception as exc:
            logger.warning(f"[PaperTrading] Signal restore from DB failed: {exc}")

    def _enter(self, symbol: str, bar: dict, signal: dict) -> None:
        # Idempotency guard: one trade per symbol per session date.
        # _positions is cleared by _reset_session, so the in-memory check alone
        # is insufficient when the stale-data cascade fires multiple resets.
        # We check the DB as the source of truth.
        try:
            with self.db.trading_writer() as conn:
                existing = conn.execute(
                    "SELECT COUNT(*) FROM stock_paper_trades WHERE symbol=? AND session_date=?",
                    [symbol, str(self._session_date)],
                ).fetchone()[0]
                if existing > 0:
                    logger.debug(
                        f"[PaperTrading] SKIP {signal['trading_symbol']}: "
                        f"already has {existing} trade(s) today"
                    )
                    return
        except Exception as exc:
            logger.warning(f"[PaperTrading] idempotency check failed for {symbol}: {exc}")

        # Hard cap on simultaneous positions
        if len(self._positions) >= self.max_open_positions:
            logger.info(
                f"[PaperTrading] SKIP {signal['trading_symbol']:12s}: "
                f"max positions reached ({self.max_open_positions})"
            )
            return

        direction   = "long" if signal["predicted_state"] == "BullTrend" else "short"
        entry_price = float(bar["open"])

        # Position sizing: max Rs max_capital per trade, integer qty
        qty = int(self.max_capital // entry_price)
        if qty < 1:
            logger.info(
                f"[PaperTrading] SKIP {signal['trading_symbol']:12s}: "
                f"price={entry_price:.2f} exceeds max_capital=Rs {self.max_capital:,.0f}"
            )
            return
        capital = round(qty * entry_price, 2)

        # SL & TP
        stop_dist = entry_price * self.stop_pct
        if direction == "long":
            stop_price   = round(entry_price - stop_dist, 2)
            target_price = round(entry_price * (1 + self.target_pct), 2)
        else:
            stop_price   = round(entry_price + stop_dist, 2)
            target_price = round(entry_price * (1 - self.target_pct), 2)

        # TLP: compute entry-time context
        all_sigs      = list(self._signals.values())
        pos_ret_count = sum(1 for s in all_sigs if s.get("c_ret", 0) > 0)
        breadth_ratio = round(pos_ret_count / len(all_sigs), 4) if all_sigs else None

        position = {
            "symbol":              symbol,
            "trading_symbol":      signal["trading_symbol"],
            "direction":           direction,
            "entry_time":          bar["timestamp"],
            "entry_price":         entry_price,
            "qty":                 qty,
            "capital":             capital,
            "stop_price":          stop_price,
            "target_price":        target_price,
            "initial_stop_dist":   stop_dist,
            "high_water":          entry_price,
            "low_water":           entry_price,
            "confidence":          signal["confidence"],
            "predicted_state":     signal["predicted_state"],
            "session_date":        str(self._session_date),
            # TLP V1 context
            "regime_state":        self._regime_state,
            "session_type":        "AM",
            "index_return_entry":  self._load_index_return_at_entry(),
            "breadth_ratio":       breadth_ratio,
            "signal_rank":         signal.get("signal_rank"),
            "signal_percentile":   signal.get("signal_percentile"),
            "intended_entry":      entry_price,
            "slippage_bps":        0.0,
        }
        self._positions[symbol] = position
        self._persist_trade_entry(position)
        logger.info(
            f"[PaperTrading] ENTER {direction.upper():5s} {signal['trading_symbol']:12s} "
            f"qty={qty} @ {entry_price:.2f}  SL={stop_price:.2f}  TP={target_price:.2f}  "
            f"capital=Rs {capital:,.0f}  "
            f"rank={signal.get('signal_rank')}/{len(all_sigs)}  "
            f"regime={self._regime_state}"
        )

    def _manage_position(self, symbol: str, bar: dict, bar_idx: int) -> None:
        """Trailing SL, SL/TP checks, and time exit for an open position."""
        pos       = self._positions[symbol]
        # Replay protection: on restart, bars before entry_time are replayed from
        # the live buffer. Skip management until we reach the entry bar.
        if bar["timestamp"] < pos["entry_time"]:
            return
        direction = pos["direction"]

        # -- Update water marks for trailing SL ------------------------------
        if direction == "long":
            if float(bar["high"]) > pos["high_water"]:
                pos["high_water"] = float(bar["high"])
        else:
            if float(bar["low"]) < pos["low_water"]:
                pos["low_water"] = float(bar["low"])

        # -- Trailing SL: activate once price moves 1x SL distance in favour
        stop_dist = pos["initial_stop_dist"]
        if direction == "long":
            if pos["high_water"] >= pos["entry_price"] + stop_dist:
                new_sl = round(pos["high_water"] - stop_dist, 2)
                if new_sl > pos["stop_price"]:
                    pos["stop_price"] = new_sl
        else:
            if pos["low_water"] <= pos["entry_price"] - stop_dist:
                new_sl = round(pos["low_water"] + stop_dist, 2)
                if new_sl < pos["stop_price"]:
                    pos["stop_price"] = new_sl

        # -- Check SL hit (conservative: check before TP) -------------------
        if direction == "long" and float(bar["low"]) <= pos["stop_price"]:
            is_trailing = pos["stop_price"] > (pos["entry_price"] - stop_dist + 0.01)
            reason = "trailing_stop" if is_trailing else "stop_hit"
            self._exit(symbol, bar, reason, exit_price=pos["stop_price"])
            return

        if direction == "short" and float(bar["high"]) >= pos["stop_price"]:
            is_trailing = pos["stop_price"] < (pos["entry_price"] + stop_dist - 0.01)
            reason = "trailing_stop" if is_trailing else "stop_hit"
            self._exit(symbol, bar, reason, exit_price=pos["stop_price"])
            return

        # -- Check TP hit ----------------------------------------------------
        if direction == "long" and float(bar["high"]) >= pos["target_price"]:
            self._exit(symbol, bar, "target_hit", exit_price=pos["target_price"])
            return

        if direction == "short" and float(bar["low"]) <= pos["target_price"]:
            self._exit(symbol, bar, "target_hit", exit_price=pos["target_price"])
            return

        # -- Time exit at bar 374 (index 373) --------------------------------
        if bar_idx >= EXIT_BARS - 1:
            self._exit(symbol, bar, "time_exit", exit_price=float(bar["close"]))

    def _exit(self, symbol: str, bar: dict, reason: str, exit_price: float = None) -> None:
        if symbol not in self._positions:
            return
        pos        = self._positions.pop(symbol)
        exit_price = exit_price if exit_price is not None else float(bar["close"])
        exit_time  = bar["timestamp"]

        # P&L calculation
        if pos["direction"] == "long":
            pnl_per_share = exit_price - pos["entry_price"]
        else:
            pnl_per_share = pos["entry_price"] - exit_price

        pnl_gross_pct = pnl_per_share / pos["entry_price"] if pos["entry_price"] else 0.0
        cost_pct      = BROKERS.get(self.broker, {}).get("cost_pct", 0.0)
        pnl_net_pct   = pnl_gross_pct - cost_pct
        pnl_rs        = round(pos["qty"] * pnl_per_share - pos["capital"] * cost_pct, 2)

        # TLP: MAE/MFE
        mae_mfe = self._compute_mae_mfe(pos, exit_time)

        result = {
            **pos,
            "exit_time":      exit_time,
            "exit_price":     exit_price,
            "exit_reason":    reason,
            "pnl_gross_pct":  round(pnl_gross_pct, 6),
            "pnl_net_pct":    round(pnl_net_pct,   6),
            "pnl_rs":         pnl_rs,
            "cost_pct":       cost_pct,
            **mae_mfe,
        }
        self._persist_trade_exit(result)
        sign = "+" if pnl_rs >= 0 else ""
        logger.info(
            f"[PaperTrading] EXIT  {pos['direction'].upper():5s} {pos['trading_symbol']:12s} "
            f"qty={pos['qty']} @ {exit_price:.2f}  {reason:14s}  "
            f"Rs {sign}{pnl_rs:.2f}  ({sign}{pnl_net_pct*100:.3f}%)  "
            f"MFE-R={mae_mfe.get('mfe_r')}"
        )

    # -----------------------------------------------------------------------
    #  TLP V1: Context Capture Methods
    # -----------------------------------------------------------------------

    def _load_regime_snapshot(self) -> None:
        """
        Load VIX-based regime state once per session (called at bar 43).
        Maps India VIX level to EXPANSION / CONTRACTION / SHOCK.
        Gracefully falls back to None if data unavailable.
        """
        if self._regime_loaded:
            return
        self._regime_loaded = True
        try:
            import duckdb as _duckdb

            date_str = str(self._session_date)
            # Find most recent 1d file on or before session date
            available = sorted(
                [p for p in _CANDLE_1D.glob("*.duckdb") if p.stem <= date_str],
                reverse=True,
            )
            if not available:
                return

            conn = _duckdb.connect(str(available[0]), read_only=True)
            rows = conn.execute(
                "SELECT close FROM candles WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
                ["NSE_INDEX|India VIX"],
            ).fetchall()
            conn.close()

            if not rows:
                return

            vix = float(rows[0][0])
            if vix < 15.0:
                self._regime_state = "EXPANSION"
            elif vix <= 20.0:
                self._regime_state = "CONTRACTION"
            else:
                self._regime_state = "SHOCK"

            logger.info(f"[PaperTrading] Regime: VIX={vix:.2f} → {self._regime_state}")

        except Exception as exc:
            logger.debug(f"[PaperTrading] Regime load failed: {exc}")

    def _load_index_return_at_entry(self) -> Optional[float]:
        """
        Compute Nifty 50 return from session open to bar 47 open.
        Returns None gracefully if data unavailable.
        """
        try:
            import duckdb as _duckdb

            date_str  = str(self._session_date)
            hist_file = _CANDLE_1M / f"{date_str}.duckdb"
            if not hist_file.exists():
                return None

            conn = _duckdb.connect(str(hist_file), read_only=True)
            rows = conn.execute(
                """
                SELECT open FROM candles
                WHERE symbol = ? AND timeframe = '1m'
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                [INDEX_SYMBOL, ENTRY_BARS],
            ).fetchall()
            conn.close()

            if len(rows) < ENTRY_BARS:
                return None

            session_open   = float(rows[0][0])
            entry_bar_open = float(rows[ENTRY_BARS - 1][0])
            if session_open == 0:
                return None
            return round((entry_bar_open - session_open) / session_open, 6)

        except Exception as exc:
            logger.debug(f"[PaperTrading] Index return failed: {exc}")
            return None

    def _update_dispersion_context(self) -> None:
        """
        Called once at bar 105 (~11:00 AM). Runs the dispersion engine and
        updates dispersion_csad / dispersion_pct for all currently open trades.
        """
        if not self._positions:
            return
        try:
            from core.analytics.dispersion import DispersionEngine

            date_str = str(self._session_date)
            engine   = DispersionEngine(db_path=_CANDLE_1M)
            snap     = engine.get_snapshot_signals(date_str, self.symbols)

            if not snap or "metrics" not in snap:
                logger.debug("[PaperTrading] Dispersion snapshot empty — skipping")
                return

            csad = snap["metrics"].get("CSAD")
            if csad is None:
                return

            csad_pct      = self._compute_csad_percentile(csad, date_str)
            open_symbols  = list(self._positions.keys())

            with self.db.trading_writer() as conn:
                for symbol in open_symbols:
                    conn.execute(
                        """
                        UPDATE stock_paper_trades
                        SET dispersion_csad=?, dispersion_pct=?
                        WHERE symbol=? AND session_date=? AND exit_time IS NULL
                        """,
                        [round(csad, 6), csad_pct, symbol, date_str],
                    )

            logger.info(
                f"[PaperTrading] Dispersion: CSAD={csad:.4f} "
                f"pct={csad_pct}  symbols={len(open_symbols)}"
            )
        except Exception as exc:
            logger.warning(f"[PaperTrading] Dispersion update failed: {exc}")

    def _compute_csad_percentile(self, csad_today: float, date_str: str) -> Optional[float]:
        """Rolling 60-day CSAD percentile, using past trades as the historical buffer."""
        try:
            with self.db.trading_writer() as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT session_date, dispersion_csad
                    FROM stock_paper_trades
                    WHERE dispersion_csad IS NOT NULL
                      AND session_date < ?
                    ORDER BY session_date DESC
                    LIMIT 60
                    """,
                    [date_str],
                ).fetchall()
            if not rows or len(rows) < 5:
                return None
            historical = [r[1] for r in rows]
            rank       = sum(1 for h in historical if h < csad_today)
            return round(rank / len(historical) * 100, 1)
        except Exception:
            return None

    def _compute_mae_mfe(self, pos: dict, exit_time) -> dict:
        """
        Load 1m candles between entry and exit and compute MAE/MFE.
        Returns dict with mae_pct, mfe_pct, mae_r, mfe_r (all None on failure).
        """
        null_result = {"mae_pct": None, "mfe_pct": None, "mae_r": None, "mfe_r": None}
        try:
            import duckdb as _duckdb

            session_date_str = pos.get("session_date", str(self._session_date))
            entry_price      = pos["entry_price"]
            direction        = pos["direction"]
            stop_distance    = abs(entry_price - pos["stop_price"])

            if stop_distance == 0 or entry_price == 0:
                return null_result

            hist_file = _CANDLE_1M / f"{session_date_str}.duckdb"
            if not hist_file.exists():
                return null_result

            entry_str = self._to_str(pos["entry_time"])
            exit_str  = self._to_str(exit_time)

            conn = _duckdb.connect(str(hist_file), read_only=True)
            rows = conn.execute(
                """
                SELECT high, low FROM candles
                WHERE symbol = ? AND timeframe = '1m'
                  AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
                """,
                [pos["symbol"], entry_str, exit_str],
            ).fetchall()
            conn.close()

            if not rows:
                return null_result

            max_h = max(float(r[0]) for r in rows)
            min_l = min(float(r[1]) for r in rows)

            if direction == "long":
                mae_abs = max(entry_price - min_l, 0.0)
                mfe_abs = max(max_h - entry_price, 0.0)
            else:
                mae_abs = max(max_h - entry_price, 0.0)
                mfe_abs = max(entry_price - min_l, 0.0)

            return {
                "mae_pct": round(mae_abs / entry_price, 6),
                "mfe_pct": round(mfe_abs / entry_price, 6),
                "mae_r":   round(mae_abs / stop_distance, 4),
                "mfe_r":   round(mfe_abs / stop_distance, 4),
            }
        except Exception as exc:
            logger.debug(f"[PaperTrading] MAE/MFE failed for {pos.get('symbol')}: {exc}")
            return null_result

    # -----------------------------------------------------------------------
    #  Persistence
    # -----------------------------------------------------------------------

    def _init_db(self) -> None:
        from core.database.schema import PAPER_SIGNALS_SCHEMA, PAPER_TRADES_SCHEMA
        try:
            with self.db.trading_writer() as conn:
                conn.execute(PAPER_SIGNALS_SCHEMA)
                conn.execute(PAPER_TRADES_SCHEMA)
                self._migrate_schema(conn)
        except Exception as exc:
            logger.error(f"[PaperTrading] DB init failed: {exc}")

    def _migrate_schema(self, conn) -> None:
        """Add new columns to existing tables (safe — catches duplicate column errors)."""
        migrations = [
            # Existing columns (idempotent)
            ("stock_paper_trades", "qty",                  "INTEGER NOT NULL DEFAULT 1"),
            ("stock_paper_trades", "capital",              "REAL NOT NULL DEFAULT 0"),
            ("stock_paper_trades", "target_price",         "REAL"),
            ("stock_paper_trades", "pnl_rs",               "REAL"),
            # TLP V1 — entry context
            ("stock_paper_trades", "regime_state",         "TEXT"),
            ("stock_paper_trades", "session_type",         "TEXT"),
            ("stock_paper_trades", "index_return_entry",   "REAL"),
            ("stock_paper_trades", "breadth_ratio",        "REAL"),
            ("stock_paper_trades", "signal_rank",          "INTEGER"),
            ("stock_paper_trades", "signal_percentile",    "REAL"),
            ("stock_paper_trades", "intended_entry",       "REAL"),
            ("stock_paper_trades", "slippage_bps",         "REAL"),
            # TLP V1 — 11am dispersion update
            ("stock_paper_trades", "dispersion_csad",      "REAL"),
            ("stock_paper_trades", "dispersion_pct",       "REAL"),
            # TLP V1 — exit outcome
            ("stock_paper_trades", "mae_pct",              "REAL"),
            ("stock_paper_trades", "mfe_pct",              "REAL"),
            ("stock_paper_trades", "mae_r",                "REAL"),
            ("stock_paper_trades", "mfe_r",                "REAL"),
            ("stock_paper_trades", "exit_efficiency",      "REAL"),
        ]
        for table, col, col_type in migrations:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                logger.info(f"[PaperTrading] Added column {table}.{col}")
            except Exception:
                pass  # Column already exists

    def _close_stale_positions(self) -> None:
        """Close any unclosed trades from previous sessions on startup."""
        try:
            today = str(date.today())
            with self.db.trading_writer() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM stock_paper_trades WHERE exit_time IS NULL AND session_date < ?",
                    [today],
                ).fetchone()[0]
                if count > 0:
                    conn.execute(
                        """
                        UPDATE stock_paper_trades
                        SET exit_time = entry_time,
                            exit_price = entry_price,
                            exit_reason = 'session_reset',
                            pnl_gross_pct = 0.0,
                            pnl_net_pct = 0.0,
                            pnl_rs = 0.0
                        WHERE exit_time IS NULL AND session_date < ?
                        """,
                        [today],
                    )
                    logger.info(f"[PaperTrading] Closed {count} stale position(s) from previous session(s).")
        except Exception as exc:
            logger.warning(f"[PaperTrading] Stale position cleanup: {exc}")

    def _restore_open_positions_from_db(self) -> None:
        """Reload today's open positions into memory so SL/TP/trail management continues after restart."""
        today = str(date.today())
        try:
            with self.db.trading_reader() as conn:
                rows = conn.execute(
                    """
                    SELECT symbol, trading_symbol, direction, entry_time, entry_price,
                           stop_price, target_price, qty, capital, confidence,
                           predicted_state, broker
                    FROM stock_paper_trades
                    WHERE session_date = ? AND exit_time IS NULL
                    """,
                    [today],
                ).fetchall()
            if not rows:
                return
            restored = 0
            for row in rows:
                sym = row[0]
                if sym in self._positions:
                    continue
                entry_price = float(row[4])
                stop_price  = float(row[5])
                # Parse entry_time string back to datetime for pre-entry bar guard
                raw_et = row[3]
                if isinstance(raw_et, str):
                    entry_time = datetime.fromisoformat(raw_et)
                else:
                    entry_time = raw_et
                self._positions[sym] = {
                    "symbol":            sym,
                    "trading_symbol":    row[1],
                    "direction":         row[2],
                    "entry_time":        entry_time,
                    "entry_price":       entry_price,
                    "stop_price":        stop_price,
                    "target_price":      float(row[6]),
                    "initial_stop_dist": abs(entry_price - stop_price),
                    "high_water":        entry_price,
                    "low_water":         entry_price,
                    "qty":               int(row[7]),
                    "capital":           float(row[8]),
                    "confidence":        float(row[9]) if row[9] is not None else None,
                    "predicted_state":   row[10],
                    "session_date":      today,
                    "broker":            row[11] or self.broker,
                    "mae_pct":           0.0,
                    "mfe_pct":           0.0,
                }
                restored += 1
            if restored:
                self._session_date = date.today()
                logger.info(f"[PaperTrading] Restored {restored} open position(s) from DB for {today}"  )
        except Exception as exc:
            logger.warning(f"[PaperTrading] Failed to restore open positions: {exc}")

    def _to_str(self, ts) -> Optional[str]:
        """Convert datetime/date to ISO string for SQLite storage."""
        if ts is None:
            return None
        if isinstance(ts, str):
            return ts
        return ts.isoformat()

    def _persist_signal(self, sig: dict) -> None:
        try:
            with self.db.trading_writer() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO stock_paper_signals
                    (session_date, symbol, trading_symbol, predicted_state,
                     confidence, p_bull, p_bear, p_choppy,
                     signal_time, c_ret, c_range, c_close_loc, broker)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        sig["session_date"], sig["symbol"], sig["trading_symbol"],
                        sig["predicted_state"], sig["confidence"],
                        sig["p_bull"], sig["p_bear"], sig["p_choppy"],
                        self._to_str(sig["signal_time"]),
                        sig["c_ret"], sig["c_range"], sig["c_close_loc"],
                        sig["broker"],
                    ],
                )
        except Exception as exc:
            logger.error(f"[PaperTrading] persist_signal failed for {sig['symbol']}: {exc}")

    def _persist_trade_entry(self, pos: dict) -> None:
        try:
            with self.db.trading_writer() as conn:
                conn.execute(
                    """
                    INSERT INTO stock_paper_trades
                    (session_date, symbol, trading_symbol, direction, broker,
                     confidence, predicted_state, qty, capital,
                     entry_time, entry_price, stop_price, target_price,
                     regime_state, session_type, index_return_entry, breadth_ratio,
                     signal_rank, signal_percentile, intended_entry, slippage_bps)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        pos["session_date"], pos["symbol"], pos["trading_symbol"],
                        pos["direction"], self.broker,
                        pos["confidence"], pos["predicted_state"],
                        pos["qty"], pos["capital"],
                        self._to_str(pos["entry_time"]),
                        pos["entry_price"], pos["stop_price"], pos["target_price"],
                        pos.get("regime_state"),
                        pos.get("session_type"),
                        pos.get("index_return_entry"),
                        pos.get("breadth_ratio"),
                        pos.get("signal_rank"),
                        pos.get("signal_percentile"),
                        pos.get("intended_entry"),
                        pos.get("slippage_bps"),
                    ],
                )
        except Exception as exc:
            logger.error(f"[PaperTrading] persist_entry failed for {pos['symbol']}: {exc}")

    def _persist_trade_exit(self, trade: dict) -> None:
        try:
            # Compute exit efficiency here where both pnl_gross_pct and mfe_pct are known
            mfe_pct         = trade.get("mfe_pct")
            pnl_gross       = trade.get("pnl_gross_pct", 0.0)
            exit_efficiency = None
            if mfe_pct is not None and mfe_pct > 1e-9:
                exit_efficiency = round(pnl_gross / mfe_pct, 4)

            with self.db.trading_writer() as conn:
                conn.execute(
                    """
                    UPDATE stock_paper_trades
                    SET exit_time=?, exit_price=?, exit_reason=?,
                        pnl_gross_pct=?, pnl_net_pct=?, pnl_rs=?, cost_pct=?,
                        mae_pct=?, mfe_pct=?, mae_r=?, mfe_r=?, exit_efficiency=?
                    WHERE symbol=? AND session_date=? AND exit_time IS NULL
                      AND direction=?
                    """,
                    [
                        self._to_str(trade["exit_time"]),
                        trade["exit_price"], trade["exit_reason"],
                        trade["pnl_gross_pct"], trade["pnl_net_pct"],
                        trade["pnl_rs"], trade["cost_pct"],
                        trade.get("mae_pct"),
                        trade.get("mfe_pct"),
                        trade.get("mae_r"),
                        trade.get("mfe_r"),
                        exit_efficiency,
                        trade["symbol"], trade["session_date"],
                        trade["direction"],
                    ],
                )
        except Exception as exc:
            logger.error(f"[PaperTrading] persist_exit failed for {trade['symbol']}: {exc}")
