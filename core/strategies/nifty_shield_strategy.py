"""
NiftyShield — Systematic Weekly Options Selling Strategy
---------------------------------------------------------
Sells short straddles on Nifty weekly options, gated by DayType regime.

Strategy logic:
  1. At 13:00 PM checkpoint, DayTypeEngine classifies the day
  2. Regime-based sizing: Choppy -> full size, Trend -> half size
  3. VIX gate: skip if VIX > 20, reduce if VIX > 16
  4. Sell ATM call + ATM put (short straddle) using Black-76 synthetic pricing
  5. Manage: profit target 50%, stop loss 2×, time exit 15:15, delta adjustment

State machine: IDLE -> AWAITING_ENTRY -> POSITIONED -> DONE
"""
import json
import math
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from core.state.daytype_engine import DayTypeEngine, DayTypeState
from core.execution.options.selector import OptionsContractSelector
from core.risk.greeks.black76_engine import Black76Engine
from core.events import SignalType
from core.logging import setup_logger

logger = setup_logger("nifty_shield_strategy")

IST = ZoneInfo("Asia/Kolkata")
NF_SYMBOL  = "NSE_INDEX|Nifty 50"
VIX_SYMBOL = "NSE_INDEX|India VIX"

_DEFAULT_CONFIG = Path("core/models/nifty_shield_config.json")


class NiftyShieldStrategy:
    """
    Systematic weekly options premium seller on Nifty.
    Self-contained paper strategy — follows V9PMScalperStrategy pattern.
    Persists to ns_paper_signals + ns_paper_trades in trading.db.
    """

    def __init__(self, db_manager, config_path=_DEFAULT_CONFIG, backtest_mode=False):
        self.db = db_manager
        self.backtest_mode = backtest_mode

        with open(config_path) as f:
            self.cfg = json.load(f)

        self.engine   = DayTypeEngine(lock_threshold=1.01)
        self.selector = OptionsContractSelector()

        # Live market data (only used when not in backtest mode)
        if not backtest_mode:
            try:
                from core.brokers.upstox_market_data import UpstoxMarketData
                self._mkt = UpstoxMarketData()
            except Exception:
                self._mkt = None
            try:
                from core.instruments.instrument_db import InstrumentMaster
                self._instrument_db = InstrumentMaster()
            except Exception:
                self._instrument_db = None
        else:
            self._mkt = None
            self._instrument_db = None

        # ── Session state ──────────────────────────────────────────
        self._session_date: Optional[date] = None
        self._sm = "IDLE"  # IDLE / AWAITING_ENTRY / POSITIONED / DONE

        # Regime
        self._day_type   = "Unknown"
        self._confidence = 0.0
        self._model_ver  = ""
        self._vix_close: Optional[float] = None

        # Position — both legs
        self._ce_option  = None
        self._pe_option  = None
        self._ce_entry   = None   # entry premium
        self._pe_entry   = None
        self._ce_exit    = None   # exit premium
        self._pe_exit    = None
        self._total_prem = None   # ce + pe entry sum
        self._lots       = 0
        self._entry_time: Optional[datetime] = None
        self._entry_price: Optional[float]  = None
        self._exit_time: Optional[datetime] = None
        self._exit_price: Optional[float]   = None
        self._exit_reason = ""

        # Greeks at entry
        self._entry_delta = None
        self._entry_theta = None
        self._ce_now: Optional[float] = None
        self._pe_now: Optional[float] = None
        self._mtm_gross_rs: Optional[float] = None
        self._mtm_net_rs: Optional[float] = None

        # Cached instrument keys for live LTP (resolved from instrument master)
        self._ce_ikey: Optional[str] = None
        self._pe_ikey: Optional[str] = None

        # Risk tracking
        self._max_loss_rs  = 0.0
        self._adjustments  = 0
        self._closing      = False

        # Bar counter (reset each session)
        self._bars_fed = 0

        self._init_db()

    # ── DB init ────────────────────────────────────────────────────

    def _init_db(self):
        from core.database.schema import NS_PAPER_SIGNALS_SCHEMA, NS_PAPER_TRADES_SCHEMA
        try:
            with self.db.trading_writer() as conn:
                conn.execute(NS_PAPER_SIGNALS_SCHEMA)
                conn.execute(NS_PAPER_TRADES_SCHEMA)
                # Migration: add source column to existing tables
                try:
                    conn.execute("ALTER TABLE ns_paper_trades ADD COLUMN source TEXT DEFAULT 'live'")
                except Exception:
                    pass  # Column already exists
        except Exception as exc:
            logger.error(f"[NiftyShield] DB init failed: {exc}")

    # ── Public interface ───────────────────────────────────────────

    def on_session_start(self, session_date: date):
        """Call once at the start of each trading day (before first bar)."""
        self._reset_session(session_date)
        self._vix_close = self._fetch_vix_close(session_date)
        logger.info(
            f"[NiftyShield] Session {session_date} | VIX={self._vix_close:.2f}"
            if self._vix_close else
            f"[NiftyShield] Session {session_date} | VIX=N/A"
        )

    def on_bn_bar(self, bar: dict):
        """Feed BankNifty 1m bar for Block H intermarket features.
        Must be called BEFORE on_bar() for the same timestamp."""
        ts = bar["timestamp"]
        ts_ist = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
        # Skip stale bars from previous sessions
        if self._session_date and ts_ist.date() < self._session_date:
            return
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
        ts    = bar["timestamp"]
        ts_ist = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
        bar_date = ts_ist.date()

        # Auto session reset if on_session_start not called externally
        if self._session_date is None:
            self.on_session_start(bar_date)
        elif bar_date < self._session_date:
            return  # Ignore stale bars from previous sessions (live buffer not rolled)
        elif bar_date > self._session_date:
            self.on_session_start(bar_date)

        price = float(bar["close"])
        self._bars_fed += 1

        # Feed bar to engine, get optional new checkpoint state
        new_state: Optional[DayTypeState] = self.engine.on_bar({
            "timestamp": ts_ist,
            "open":   float(bar["open"]),
            "high":   float(bar["high"]),
            "low":    float(bar["low"]),
            "close":  price,
            "volume": float(bar.get("volume", 0)),
        })

        # ── Checkpoint fired ──────────────────────────────────────
        if new_state is not None and new_state.checkpoint == self.cfg["entry_checkpoint"]:
            self._day_type   = new_state.predicted_state
            self._confidence = new_state.confidence
            self._model_ver  = new_state.model_version
            self._persist_signal(ts_ist)

            if self._sm == "IDLE":
                vix_skip = self.cfg["vix_skip_above"]
                if self._vix_close and self._vix_close > vix_skip:
                    logger.info(
                        f"[NiftyShield] SKIP: VIX {self._vix_close:.1f} > {vix_skip}"
                    )
                    self._sm = "DONE"
                else:
                    self._sm = "AWAITING_ENTRY"
                    logger.info(
                        f"[NiftyShield] SIGNAL: {self._day_type} "
                        f"conf={self._confidence:.0%} | awaiting entry bar"
                    )

        # ── State: AWAITING_ENTRY ─────────────────────────────────
        if self._sm == "AWAITING_ENTRY":
            entry_minute = self.cfg["entry_after_minutes"]
            checkpoint_h, checkpoint_m = 13, 0   # 13pm checkpoint
            entry_time = dt_time(checkpoint_h, (checkpoint_m + entry_minute) % 60)
            if ts_ist.time() >= entry_time:
                self._enter(ts_ist, price)

        # ── State: POSITIONED ────────────────────────────────────
        elif self._sm == "POSITIONED":
            self._manage(ts_ist, price)

    def get_status(self) -> dict:
        """Current state snapshot for Flask UI / logging."""
        return {
            "session_date":    str(self._session_date),
            "state":           self._sm,
            "day_type":        self._day_type,
            "confidence":      round(self._confidence, 4),
            "vix_close":       self._vix_close,
            "lots":            self._lots,
            "ce_symbol":       self._ce_option.symbol if self._ce_option else None,
            "pe_symbol":       self._pe_option.symbol if self._pe_option else None,
            "ce_strike":       self._ce_option.strike if self._ce_option else None,
            "pe_strike":       self._pe_option.strike if self._pe_option else None,
            "expiry":          self._ce_option.expiry if self._ce_option else None,
            "entry_time":      self._entry_time,
            "total_premium":   self._total_prem,
            "ce_entry_premium": self._ce_entry,
            "pe_entry_premium": self._pe_entry,
            "entry_delta":     self._entry_delta,
            "entry_theta":     self._entry_theta,
            "ce_now":          self._ce_now,
            "pe_now":          self._pe_now,
            "total_now":       (self._ce_now + self._pe_now) if (self._ce_now is not None and self._pe_now is not None) else None,
            "mtm_gross_rs":    self._mtm_gross_rs,
            "mtm_net_rs":      self._mtm_net_rs,
            "adjustments":     self._adjustments,
            "bars_fed":        self._bars_fed,
            "pricing_mode":    "LIVE" if (self._ce_ikey and self._pe_ikey) else "SYNTHETIC",
            "ce_ikey":         self._ce_ikey,
            "pe_ikey":         self._pe_ikey,
        }

    def get_session_result(self) -> Optional[dict]:
        """Return trade result dict for backtest aggregation (None if no trade)."""
        if self._ce_exit is None and self._pe_exit is None:
            return None
        lot_size = self.cfg["lot_size"]
        costs    = self.cfg["cost_per_lot_rs"] * self._lots
        pnl_gross = (
            (self._ce_entry - self._ce_exit) +
            (self._pe_entry - self._pe_exit)
        ) * lot_size * self._lots
        pnl_net = pnl_gross - costs
        return {
            "session_date":    str(self._session_date),
            "day_type":        self._day_type,
            "confidence":      self._confidence,
            "vix_close":       self._vix_close,
            "lots":            self._lots,
            "ce_strike":       self._ce_option.strike if self._ce_option else None,
            "pe_strike":       self._pe_option.strike if self._pe_option else None,
            "total_premium":   self._total_prem,
            "entry_delta":     self._entry_delta,
            "entry_theta":     self._entry_theta,
            "exit_reason":     self._exit_reason,
            "pnl_gross_rs":    round(pnl_gross, 2),
            "pnl_net_rs":      round(pnl_net, 2),
            "costs_rs":        costs,
            "max_loss_rs":     round(self._max_loss_rs, 2),
            "adjustments":     self._adjustments,
        }

    # ── Entry ──────────────────────────────────────────────────────

    def _has_open_trade_today(self) -> bool:
        """Return True if an open (not-exited) trade already exists in DB for today's session."""
        try:
            with self.db.trading_reader() as conn:
                row = conn.execute(
                    "SELECT 1 FROM ns_paper_trades WHERE session_date=? AND exit_time IS NULL LIMIT 1",
                    [str(self._session_date)],
                ).fetchone()
                return row is not None
        except Exception:
            return False

    def _enter(self, ts_ist: datetime, price: float):
        # Guard: prevent re-entry after runner restart (open DB row already present)
        if self._has_open_trade_today():
            logger.info(
                f"[NiftyShield] SKIP entry: open trade already exists for {self._session_date}"
            )
            self._sm = "DONE"
            return

        # Regime-based lot sizing
        sizing_map = self.cfg["regime_sizing"]
        size_mult  = sizing_map.get(self._day_type, 0.5)
        lots = max(1, round(self.cfg["max_lots"] * size_mult))

        # VIX-based reduction
        if self._vix_close and self._vix_close > self.cfg["vix_reduce_above"]:
            lots = max(1, lots - 1)

        # Select ATM call + put (same strike — ATM straddle)
        policy = {"expiry_days_min": self.cfg["expiry_days_min"]}
        ce_opt = self.selector.select(NF_SYMBOL, price, SignalType.BUY,  ts_ist, policy)
        pe_opt = self.selector.select(NF_SYMBOL, price, SignalType.SELL, ts_ist, policy)

        # Resolve live instrument keys before pricing
        self._ce_option = ce_opt
        self._pe_option = pe_opt
        if not self.backtest_mode:
            self._ce_ikey = self._resolve_ikey(ce_opt)
            self._pe_ikey = self._resolve_ikey(pe_opt)

        # Compute option prices (uses live LTP if keys resolved, else Black-76)
        tte  = self._time_to_expiry(ts_ist, ce_opt.expiry)
        iv   = self._iv(ts_ist)
        r    = self.cfg["risk_free_rate"]

        ce_prem = self._option_price(price, ce_opt.strike, tte, r, iv, 'CE')
        pe_prem = self._option_price(price, pe_opt.strike, tte, r, iv, 'PE')

        # Compute entry Greeks (short position = negative quantity)
        lot_size = self.cfg["lot_size"]
        ce_g = Black76Engine.calculate_greeks(price, ce_opt.strike, tte, r, iv, 'CE')
        pe_g = Black76Engine.calculate_greeks(price, pe_opt.strike, tte, r, iv, 'PE')
        # Short straddle: sold -lots × lot_size of each leg
        qty = lots * lot_size
        net_delta = (-qty * ce_g.delta) + (-qty * pe_g.delta)   # ~0 for ATM
        net_theta = (-qty * ce_g.theta) + (-qty * pe_g.theta)   # positive (earn time decay)

        self._ce_entry   = ce_prem
        self._pe_entry   = pe_prem
        self._total_prem = ce_prem + pe_prem
        self._lots       = lots
        self._entry_time  = ts_ist
        self._entry_price = price
        self._entry_delta = round(net_delta, 3)
        self._entry_theta = round(net_theta, 3)
        self._max_loss_rs = 0.0
        self._adjustments = 0
        self._ce_now = ce_prem
        self._pe_now = pe_prem
        self._mtm_gross_rs = 0.0
        self._mtm_net_rs = -float(self.cfg["cost_per_lot_rs"] * self._lots)
        self._sm = "POSITIONED"

        self._persist_trade_entry()

        pricing_mode = "LIVE" if (self._ce_ikey and self._pe_ikey) else "SYNTHETIC"
        logger.info(
            f"[NiftyShield] ENTER [{pricing_mode}] | {self._day_type} conf={self._confidence:.0%} | "
            f"CE {ce_opt.symbol} @{ce_prem:.1f} + PE {pe_opt.symbol} @{pe_prem:.1f} | "
            f"Total premium: {self._total_prem:.1f} pts | Lots: {lots} | "
            f"delta={net_delta:.2f} theta={net_theta:.2f}/day | expiry={ce_opt.expiry}"
        )

    # ── Position management ────────────────────────────────────────

    def _manage(self, ts_ist: datetime, price: float):
        tte  = self._time_to_expiry(ts_ist, self._ce_option.expiry)
        iv   = self._iv(ts_ist)
        r    = self.cfg["risk_free_rate"]
        lot_size = self.cfg["lot_size"]

        # Current premiums
        ce_now = self._option_price(price, self._ce_option.strike, tte, r, iv, 'CE')
        pe_now = self._option_price(price, self._pe_option.strike, tte, r, iv, 'PE')
        total_now = ce_now + pe_now

        # P&L (we sold, so profit = collected - current)
        pnl_per_lot = (self._total_prem - total_now) * lot_size
        pnl_total   = pnl_per_lot * self._lots
        costs_total = float(self.cfg["cost_per_lot_rs"] * self._lots)
        self._ce_now = ce_now
        self._pe_now = pe_now
        self._mtm_gross_rs = round(pnl_total, 2)
        self._mtm_net_rs = round(pnl_total - costs_total, 2)

        # Track worst-case loss (max adverse excursion)
        if pnl_total < -self._max_loss_rs:
            self._max_loss_rs = -pnl_total

        # PnL as fraction of premium collected
        pnl_pct = (self._total_prem - total_now) / self._total_prem if self._total_prem else 0

        # 1. Profit target: close when 50% of premium decayed
        if pnl_pct >= self.cfg["profit_target_pct"]:
            self._close(ts_ist, price, ce_now, pe_now, "profit_target")
            return

        # 2. Stop loss: close when unrealised loss = 2× premium collected
        if pnl_pct <= -self.cfg["stop_loss_multiplier"]:
            self._close(ts_ist, price, ce_now, pe_now, "stop_loss")
            return

        # 3. Time exit: Friday 15:15 or configured exit time
        exit_h = self.cfg["exit_time"]["hour"]
        exit_m = self.cfg["exit_time"]["minute"]
        if ts_ist.time() >= dt_time(exit_h, exit_m):
            self._close(ts_ist, price, ce_now, pe_now, "time_exit")
            return

        # 4. Delta adjustment check
        ce_g = Black76Engine.calculate_greeks(price, self._ce_option.strike, tte, r, iv, 'CE')
        pe_g = Black76Engine.calculate_greeks(price, self._pe_option.strike, tte, r, iv, 'PE')
        adj_thr = self.cfg["delta_adjustment_threshold"]

        if abs(ce_g.delta) > adj_thr:
            self._adjust_leg("CE", ts_ist, price, ce_now, tte, iv, r)
        elif abs(pe_g.delta) > adj_thr:
            self._adjust_leg("PE", ts_ist, price, pe_now, tte, iv, r)

    # ── Adjustment ────────────────────────────────────────────────

    def _adjust_leg(self, leg: str, ts_ist: datetime, price: float,
                    old_premium: float, tte: float, iv: float, r: float):
        """Roll threatened leg to new ATM strike, staying on the same weekly expiry."""
        if leg == "CE":
            old_symbol   = self._ce_option.symbol
            old_strike   = self._ce_option.strike
            policy       = {"expiry_date": self._ce_option.expiry}  # pin to original weekly expiry
            new_opt      = self.selector.select(NF_SYMBOL, price, SignalType.BUY, ts_ist, policy)
            self._ce_option = new_opt
            if not self.backtest_mode:
                self._ce_ikey = self._resolve_ikey(new_opt)
            new_prem     = self._option_price(price, new_opt.strike, tte, r, iv, 'CE')
            roll_credit  = new_prem - old_premium
            self._ce_entry  = new_prem
            self._total_prem = (self._total_prem - old_premium) + new_prem
        else:
            old_symbol   = self._pe_option.symbol
            old_strike   = self._pe_option.strike
            policy       = {"expiry_date": self._pe_option.expiry}  # pin to original weekly expiry
            new_opt      = self.selector.select(NF_SYMBOL, price, SignalType.SELL, ts_ist, policy)
            self._pe_option = new_opt
            if not self.backtest_mode:
                self._pe_ikey = self._resolve_ikey(new_opt)
            new_prem     = self._option_price(price, new_opt.strike, tte, r, iv, 'PE')
            roll_credit  = new_prem - old_premium
            self._pe_entry  = new_prem
            self._total_prem = (self._total_prem - old_premium) + new_prem

        self._adjustments += 1
        logger.info(
            f"[NiftyShield] ADJUST {leg}: {old_symbol}({old_strike:.0f}) -> "
            f"{new_opt.symbol}({new_opt.strike:.0f}) | "
            f"roll credit={roll_credit:.1f} pts | adjustments={self._adjustments}"
        )

    # ── Close ─────────────────────────────────────────────────────

    def _close(self, ts_ist: datetime, price: float,
               ce_now: float, pe_now: float, reason: str):
        lot_size = self.cfg["lot_size"]
        costs    = self.cfg["cost_per_lot_rs"] * self._lots
        pnl_gross = (
            (self._ce_entry - ce_now) + (self._pe_entry - pe_now)
        ) * lot_size * self._lots
        pnl_net = pnl_gross - costs

        self._ce_exit    = ce_now
        self._pe_exit    = pe_now
        self._ce_now     = ce_now
        self._pe_now     = pe_now
        self._mtm_gross_rs = round(pnl_gross, 2)
        self._mtm_net_rs   = round(pnl_net, 2)
        self._exit_time  = ts_ist
        self._exit_price = price
        self._exit_reason = reason
        self._sm = "DONE"

        self._persist_trade_exit(pnl_gross, pnl_net, costs)

        logger.info(
            f"[NiftyShield] EXIT [{reason}] | "
            f"CE {self._ce_entry:.1f}->{ce_now:.1f}  "
            f"PE {self._pe_entry:.1f}->{pe_now:.1f} | "
            f"Gross Rs {pnl_gross:+,.0f} | Net Rs {pnl_net:+,.0f} | "
            f"Max loss Rs {self._max_loss_rs:,.0f} | adj={self._adjustments}"
        )

    # ── Instrument key resolution ────────────────────────────────────

    def _resolve_ikey(self, opt) -> Optional[str]:
        """Resolve Upstox instrument_key for an Option using structured field lookup."""
        if self._instrument_db is None or not self._instrument_db.is_loaded():
            return None
        ot = opt.option_type.value if hasattr(opt.option_type, 'value') else str(opt.option_type)
        name = "NIFTY" if "Nifty 50" in (opt.underlying or "") else opt.underlying.split("|")[-1].upper().replace(" ", "")
        ikey = self._instrument_db.resolve_option(name, opt.expiry, opt.strike, ot)
        if ikey:
            logger.info(f"[NiftyShield] Resolved {ot} {opt.strike:.0f} exp={opt.expiry} -> {ikey}")
        else:
            logger.warning(f"[NiftyShield] Could not resolve instrument key for {ot} {opt.strike:.0f} exp={opt.expiry}")
        return ikey

    # ── Pricing helpers ────────────────────────────────────────────

    def _option_price(self, F: float, K: float, T: float, r: float,
                      iv: float, opt_type: str) -> float:
        """Black-76 price in backtest; live LTP in paper mode (falls back to B76)."""
        if not self.backtest_mode and self._mkt is not None:
            ikey = self._ce_ikey if opt_type == 'CE' else self._pe_ikey
            if ikey:
                ltp = self._mkt.fetch_ltp(ikey)
                if ltp and ltp > 0:
                    logger.debug(f"[NiftyShield] LIVE LTP {opt_type} {ikey}: Rs {ltp:.2f}")
                    return ltp
                logger.debug(f"[NiftyShield] LTP fetch failed for {ikey}, using synthetic")
        synthetic = Black76Engine.calculate_price(F, K, T, r, iv, opt_type)
        if not self.backtest_mode:
            logger.debug(f"[NiftyShield] SYNTHETIC {opt_type} K={K:.0f}: Rs {synthetic:.2f}")
        return synthetic

    def _iv(self, ts_ist: datetime) -> float:
        """IV estimate: daily VIX / 100 if available, else config default."""
        if self._vix_close and self._vix_close > 0:
            return self._vix_close / 100.0
        return self.cfg["iv_default"]

    @staticmethod
    def _time_to_expiry(now: datetime, expiry: date) -> float:
        """Compute T in years from now to 15:30 on expiry day."""
        expiry_dt = datetime.combine(expiry, dt_time(15, 30), tzinfo=IST)
        now_aware = now if now.tzinfo else now.replace(tzinfo=IST)
        delta_secs = (expiry_dt - now_aware).total_seconds()
        return max(delta_secs / (365.25 * 24 * 3600), 0.0)

    # ── VIX data ───────────────────────────────────────────────────

    def _fetch_vix_close(self, today: date) -> Optional[float]:
        """Get most recent India VIX daily close prior to today."""
        try:
            from datetime import timedelta
            from core.database.queries import MarketDataQuery
            q = MarketDataQuery(self.db)
            df = q.get_ohlcv(
                VIX_SYMBOL,
                start_time=datetime.combine(today - timedelta(days=10), dt_time(0, 0)),
                end_time=datetime.combine(today, dt_time(0, 0)),
                timeframe="1m",
            )
            if not df.empty:
                return float(df.iloc[-1]["close"])
        except Exception as exc:
            logger.warning(f"[NiftyShield] VIX fetch failed: {exc}")
        return None

    # ── Session reset ──────────────────────────────────────────────

    def _reset_session(self, new_date: date):
        self._session_date  = new_date
        self._sm            = "IDLE"
        self._day_type      = "Unknown"
        self._confidence    = 0.0
        self._model_ver     = ""
        self._ce_option     = None
        self._pe_option     = None
        self._ce_entry      = None
        self._pe_entry      = None
        self._ce_exit       = None
        self._pe_exit       = None
        self._total_prem    = None
        self._lots          = 0
        self._entry_time    = None
        self._entry_price   = None
        self._exit_time     = None
        self._exit_price    = None
        self._exit_reason   = ""
        self._entry_delta   = None
        self._entry_theta   = None
        self._ce_now        = None
        self._pe_now        = None
        self._mtm_gross_rs  = None
        self._mtm_net_rs    = None
        self._ce_ikey       = None
        self._pe_ikey       = None
        self._max_loss_rs   = 0.0
        self._adjustments   = 0
        self._bars_fed      = 0
        self.engine.reset(new_date)

    # ── Persistence ────────────────────────────────────────────────

    def _persist_signal(self, ts_ist: datetime):
        sizing_map = self.cfg["regime_sizing"]
        size_mult  = sizing_map.get(self._day_type, 0.5)
        lots = max(1, round(self.cfg["max_lots"] * size_mult))
        try:
            with self.db.trading_writer() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO ns_paper_signals
                    (session_date, underlying, predicted_state, confidence,
                     vix_close, regime_sizing, lots, structure, signal_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(self._session_date), NF_SYMBOL, self._day_type,
                        self._confidence, self._vix_close, size_mult, lots,
                        self.cfg["structure"], self._to_str(ts_ist),
                    ]
                )
        except Exception as exc:
            logger.error(f"[NiftyShield] Signal persist failed: {exc}")

    def _persist_trade_entry(self):
        try:
            with self.db.trading_writer() as conn:
                conn.execute(
                    """
                    INSERT INTO ns_paper_trades
                    (session_date, underlying, structure, entry_time, entry_price,
                     ce_symbol, pe_symbol, ce_strike, pe_strike,
                     ce_entry_premium, pe_entry_premium, total_premium,
                     lots, entry_delta, entry_theta,
                     predicted_state, confidence, vix_close, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(self._session_date), NF_SYMBOL, self.cfg["structure"],
                        self._to_str(self._entry_time), self._entry_price,
                        self._ce_option.symbol, self._pe_option.symbol,
                        self._ce_option.strike, self._pe_option.strike,
                        self._ce_entry, self._pe_entry, self._total_prem,
                        self._lots, self._entry_delta, self._entry_theta,
                        self._day_type, self._confidence, self._vix_close,
                        "backtest" if self.backtest_mode else "live",
                    ]
                )
        except Exception as exc:
            logger.error(f"[NiftyShield] Trade entry persist failed: {exc}")

    def _persist_trade_exit(self, pnl_gross: float, pnl_net: float, costs: float):
        try:
            with self.db.trading_writer() as conn:
                conn.execute(
                    """
                    UPDATE ns_paper_trades
                    SET exit_time=?, exit_price=?,
                        ce_exit_premium=?, pe_exit_premium=?,
                        exit_reason=?, pnl_gross_rs=?, pnl_net_rs=?,
                        costs_rs=?, max_loss_rs=?, adjustments=?,
                        ce_symbol=?, ce_strike=?, pe_symbol=?, pe_strike=?,
                        ce_entry_premium=?, pe_entry_premium=?, total_premium=?
                    WHERE session_date=? AND entry_time=?
                    """,
                    [
                        self._to_str(self._exit_time), self._exit_price,
                        self._ce_exit, self._pe_exit,
                        self._exit_reason, round(pnl_gross, 2),
                        round(pnl_net, 2), costs,
                        round(self._max_loss_rs, 2), self._adjustments,
                        # Final adjusted symbols/strikes (updated from entry values if legs were rolled)
                        self._ce_option.symbol, self._ce_option.strike,
                        self._pe_option.symbol, self._pe_option.strike,
                        self._ce_entry, self._pe_entry, self._total_prem,
                        str(self._session_date), self._to_str(self._entry_time),
                    ]
                )
        except Exception as exc:
            logger.error(f"[NiftyShield] Trade exit persist failed: {exc}")

    def emergency_close(self, current_spot: float = None) -> dict:
        """Force-close current position at Black-76 mark prices. Used by kill switch."""
        if self._sm != "POSITIONED":
            return {"status": "no_position", "state": self._sm}
        if self._closing:
            return {"status": "already_closing"}
        self._closing = True
        try:
            now   = datetime.now(IST)
            price = current_spot or self._entry_price
            tte   = self._time_to_expiry(now, self._ce_option.expiry)
            iv    = self._iv(now)
            r     = self.cfg["risk_free_rate"]
            ce_now = self._option_price(price, self._ce_option.strike, tte, r, iv, "CE")
            pe_now = self._option_price(price, self._pe_option.strike, tte, r, iv, "PE")
            self._close(now, price, ce_now, pe_now, "emergency_exit")
            return {"status": "closed", "spot": price,
                    "ce_exit": round(ce_now, 2), "pe_exit": round(pe_now, 2)}
        finally:
            self._closing = False

    @staticmethod
    def _to_str(ts) -> Optional[str]:
        if ts is None:
            return None
        return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

