"""Live MT5 trader for FTMO XAUUSD challenge.

Polls M5 bars every 5 minutes, runs scan_session() at each session open,
places and manages trades via MetaTrader5 API.

Usage:
    python -m ftmo.cli live --login 1512742557 --password <PASS> --server FTMO-Demo
"""

import csv
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import requests

from ftmo.config import (
    SYMBOL, ACCOUNT_SIZE,
    PRE_NY_START, PRE_NY_END, NY_START, NY_END,
    PRE_NY2_START, PRE_NY2_END, NY2_START, NY2_END,
    RISK_PER_TRADE_PCT, POINT_VALUE, MAX_TRADES_PER_DAY,
    MAX_OVERALL_LOSS, DAILY_MAX_LOSS,
    RR_RATIO, SL_BUFFER_ATR_MULT,
)
from ftmo.indicators import enrich_with_indicators
from ftmo.detector import scan_session
from ftmo.risk import RiskEngine, AccountState

logger = logging.getLogger(__name__)

IST = "Asia/Kolkata"
POLL_INTERVAL_SEC = 60          # Check every 60 seconds
MIN_BARS_REQUIRED = 100         # Minimum bars needed before scanning
SESSION_OPEN_BUFFER_MIN = 2     # Scan this many minutes after session opens
DATA_FRESHNESS_MAX_MIN = 15     # Reject scan if latest bar is older than this
NEWS_BLACKOUT_MIN = 30          # Skip scan if high-impact USD news within this window
TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "trade_log.csv")
CACHE_PATH = os.path.join(os.path.dirname(__file__), "cache_m5.parquet")
FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_CALENDAR_CACHE = os.path.join(os.path.dirname(__file__), ".calendar_cache.json")
_TRADE_LOG_HEADERS = [
    "date", "session", "direction", "entry", "sl", "tp",
    "lots", "outcome", "pnl", "equity_after", "ticket"
]


def _fetch_himpact_usd_events(date_ist: datetime) -> list[dict]:
    """Fetch high-impact USD events for today from ForexFactory CDN JSON feed.

    Caches the weekly JSON to disk — only re-fetches if cache is from a prior day.
    Returns list of dicts with keys: title, dt (datetime in IST).
    Falls back to empty list on any error — caller should fail open.
    """
    import json
    today_str = date_ist.strftime("%Y-%m-%d")

    # Use disk cache if it was written today
    if os.path.isfile(FF_CALENDAR_CACHE):
        try:
            with open(FF_CALENDAR_CACHE) as f:
                cached = json.load(f)
            if cached.get("fetched_date") == today_str:
                data = cached["data"]
                logger.debug("Calendar loaded from disk cache")
            else:
                data = None  # stale — re-fetch
        except Exception:
            data = None
    else:
        data = None

    if data is None:
        try:
            resp = requests.get(
                FF_CALENDAR_URL,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            with open(FF_CALENDAR_CACHE, "w") as f:
                json.dump({"fetched_date": today_str, "data": data}, f)
        except Exception as e:
            logger.warning(f"Calendar fetch failed: {e}")
            return []

    from zoneinfo import ZoneInfo
    ist_tz = ZoneInfo(IST)
    eastern_tz = ZoneInfo("America/New_York")   # FF times are US Eastern
    today_ist = date_ist.date()
    events = []

    for ev in data:
        if ev.get("country", "") != "USD":
            continue
        if ev.get("impact", "") != "High":
            continue
        date_str = ev.get("date", "")
        title = ev.get("title", "").strip()
        if not date_str:
            continue
        try:
            # JSON format: ISO 8601 with offset e.g. "2026-03-11T08:30:00-04:00"
            dt = datetime.fromisoformat(date_str).astimezone(ist_tz)
            if dt.date() == today_ist:
                events.append({"title": title, "dt": dt})
        except ValueError:
            continue

    if events:
        names = ", ".join(e["title"] for e in events)
        logger.info(f"High-impact USD events today: {names}")
    return events


def _is_news_blackout(scan_time_ist: datetime, events: list[dict]) -> tuple[bool, str]:
    """Return (True, reason) if scan_time is within NEWS_BLACKOUT_MIN of any event."""
    window = timedelta(minutes=NEWS_BLACKOUT_MIN)
    for ev in events:
        delta = abs(scan_time_ist - ev["dt"])
        if delta <= window:
            mins = int(delta.total_seconds() / 60)
            return True, f"{ev['title']} in {mins}min"
    return False, ""


def _append_trade_log(row: dict):
    file_exists = os.path.isfile(TRADE_LOG_PATH)
    with open(TRADE_LOG_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_TRADE_LOG_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def _get_mt5():
    try:
        import MetaTrader5 as mt5
        return mt5
    except ImportError:
        raise ImportError("pip install MetaTrader5 required")


class MT5LiveTrader:
    def __init__(self, login: int, password: str, server: str):
        self.login = login
        self.password = password
        self.server = server
        self._connected = False
        self.risk = RiskEngine()
        self.state: Optional[AccountState] = None
        self._open_ticket: Optional[int] = None  # one trade at a time
        self._pending_log: Optional[dict] = None  # trade row waiting for close
        self._session1_scanned_today = False
        self._session2_scanned_today = False
        self._last_date = None
        self._last_cached_ts: Optional[pd.Timestamp] = None  # last bar written to cache
        self._himpact_events: list[dict] = []                # today's high-impact USD events

    # ── Connection ──────────────────────────────────────────────────────────

    def connect(self):
        mt5 = _get_mt5()
        if not mt5.initialize():
            raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
        if not mt5.login(self.login, password=self.password, server=self.server):
            mt5.shutdown()
            raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")

        info = mt5.account_info()
        balance = info.balance
        logger.info(f"Connected: {info.login} @ {info.server} | Balance: {balance:,.2f}")
        print(f"[MT5] Connected: login={info.login} server={info.server} balance={balance:,.2f} {info.currency}")

        self.state = AccountState.fresh(balance)
        self._connected = True

        # Seed last cached timestamp from existing parquet so we don't re-download old bars
        if os.path.isfile(CACHE_PATH):
            try:
                existing = pd.read_parquet(CACHE_PATH, columns=["timestamp"])
                self._last_cached_ts = existing["timestamp"].max()
                print(f"[CACHE] Resuming from {self._last_cached_ts}")
            except Exception as e:
                logger.warning(f"Could not read cache: {e}")

    def disconnect(self):
        try:
            _get_mt5().shutdown()
        except Exception:
            pass
        self._connected = False
        logger.info("MT5 disconnected")

    # ── Main Loop ────────────────────────────────────────────────────────────

    def run(self):
        if not self._connected:
            raise RuntimeError("Call connect() first")

        print(f"[LIVE] Starting XAUUSD challenge trader. Ctrl+C to stop.")
        print(f"[LIVE] Session 1: {NY_START}–{NY_END} IST  |  Session 2: {NY2_START}–{NY2_END} IST")
        print(f"[LIVE] Trade log: {TRADE_LOG_PATH}")

        now_ist = datetime.now(tz=timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo(IST))
        self._himpact_events = _fetch_himpact_usd_events(now_ist)
        if self._himpact_events:
            for ev in self._himpact_events:
                print(f"[NEWS] High-impact today: {ev['title']} @ {ev['dt'].strftime('%H:%M')} IST")
        else:
            print("[NEWS] No high-impact USD events today (or calendar unavailable)")

        try:
            while True:
                try:
                    self._tick()
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    logger.error(f"Tick error: {e}", exc_info=True)
                    print(f"[ERROR] {e}")
                time.sleep(POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            print("\n[LIVE] Stopped by user.")
        finally:
            self.disconnect()

    def _tick(self):
        mt5 = _get_mt5()
        now_ist = datetime.now(tz=timezone.utc).astimezone(
            __import__("zoneinfo").ZoneInfo(IST)
        )
        today = now_ist.strftime("%Y-%m-%d")
        now_t = now_ist.time()

        # Reset daily state on new calendar day
        if today != self._last_date:
            self._on_new_day(today)

        # Update account state from MT5
        self._sync_account_state()

        # Append any new closed bars to local cache
        self._update_data_cache()

        # Monitor any open position
        if self._open_ticket is not None:
            self._monitor_position(now_ist)
            return  # Don't enter new trades while one is open

        # Session 1: scan ~2 min after London open
        if NY_START <= now_t < NY_END and not self._session1_scanned_today:
            mins_into_session = (
                now_ist.hour * 60 + now_ist.minute
            ) - (NY_START.hour * 60 + NY_START.minute)
            if mins_into_session >= SESSION_OPEN_BUFFER_MIN:
                self._scan_and_trade(session=1, cutoff=NY_END)
                self._session1_scanned_today = True

        # Session 2: scan ~2 min after NY open
        if NY2_START <= now_t < NY2_END and not self._session2_scanned_today:
            mins_into_session = (
                now_ist.hour * 60 + now_ist.minute
            ) - (NY2_START.hour * 60 + NY2_START.minute)
            if mins_into_session >= SESSION_OPEN_BUFFER_MIN:
                self._scan_and_trade(session=2, cutoff=NY2_END)
                self._session2_scanned_today = True

    # ── Session Scan ────────────────────────────────────────────────────────

    def _scan_and_trade(self, session: int, cutoff):
        mt5 = _get_mt5()
        now_ist = datetime.now(tz=timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo(IST))
        print(f"[S{session}] Scanning at {now_ist.strftime('%H:%M')} IST")

        # News blackout check
        blocked, reason = _is_news_blackout(now_ist, self._himpact_events)
        if blocked:
            print(f"[S{session}] NEWS_BLACKOUT — {reason} (±{NEWS_BLACKOUT_MIN}min window)")
            logger.info(f"S{session}: news blackout: {reason}")
            return

        # Fetch enough M5 bars to cover pre-session range + session bars
        bars_needed = 300  # ~25 hours of M5 data
        rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, bars_needed)
        if rates is None or len(rates) < MIN_BARS_REQUIRED:
            logger.warning(f"Insufficient bars: {len(rates) if rates else 0}")
            return

        df = pd.DataFrame(rates)
        df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(IST)
        df = df.rename(columns={"tick_volume": "volume"})[
            ["timestamp", "open", "high", "low", "close", "volume"]
        ].sort_values("timestamp").reset_index(drop=True)

        # Enrich with ATR
        df_m5, _ = enrich_with_indicators(df)

        # Data freshness guard — reject if MT5 feed is stale
        latest_bar_time = df_m5["timestamp"].iloc[-1]
        now_utc = datetime.now(tz=timezone.utc)
        bar_age_min = (now_utc - latest_bar_time.to_pydatetime()).total_seconds() / 60
        if bar_age_min > DATA_FRESHNESS_MAX_MIN:
            print(f"[S{session}] REJECTED — data stale ({bar_age_min:.1f} min since last bar)")
            logger.warning(f"S{session}: stale feed, last bar {bar_age_min:.1f} min ago")
            return

        # Extract pre-session range and session bars
        if session == 1:
            pre_s, pre_e = PRE_NY_START, PRE_NY_END
            ny_s, ny_e = NY_START, NY_END
        else:
            pre_s, pre_e = PRE_NY2_START, PRE_NY2_END
            ny_s, ny_e = NY2_START, NY2_END

        t = df_m5["timestamp"].dt.time
        pre_bars = df_m5[(t >= pre_s) & (t < pre_e)]
        ny_bars = df_m5[(t >= ny_s) & (t < ny_e)].reset_index(drop=True)

        if len(pre_bars) < 2:
            logger.warning(f"S{session}: not enough pre-session bars ({len(pre_bars)})")
            return
        if len(ny_bars) < 3:
            logger.warning(f"S{session}: not enough session bars ({len(ny_bars)})")
            return

        pre_high = pre_bars["high"].max()
        pre_low = pre_bars["low"].min()
        m15_atr = ny_bars.iloc[0]["m15_atr"] if "m15_atr" in ny_bars.columns else 0
        if not m15_atr or pd.isna(m15_atr):
            logger.warning(f"S{session}: m15_atr unavailable")
            return

        setups = scan_session(ny_bars, pre_high, pre_low, m15_atr, cutoff=cutoff)

        if not setups:
            print(f"[S{session}] NO_SETUP (pre-range: {pre_high:.2f}–{pre_low:.2f}, ATR: {m15_atr:.2f})")
            return

        # Risk gate
        setup = setups[0]
        risk_dollar = self.risk.calculate_risk_per_trade(self.state)
        allowed, reason, status = self.risk.check_pre_trade(
            self.state, risk_dollar, setup.timestamp
        )
        if not allowed:
            print(f"[S{session}] BLOCKED — {reason}")
            logger.info(f"S{session}: trade blocked: {reason}")
            return

        lot_size = self.risk.calculate_lot_size(self.state, setup.risk_points)
        lot_size = round(max(0.01, lot_size), 2)

        print(f"[S{session}] EXECUTING {setup.direction} | Entry: {setup.entry_price:.2f} "
              f"SL: {setup.stop_loss:.2f} TP: {setup.take_profit:.2f} "
              f"Risk: {setup.risk_points:.2f}pts | Lots: {lot_size}")

        self._place_order(setup, lot_size, session)

    # ── Order Management ────────────────────────────────────────────────────

    def _place_order(self, setup, lot_size: float, session: int = 0):
        mt5 = _get_mt5()
        symbol_info = mt5.symbol_info(SYMBOL)
        if symbol_info is None:
            logger.error(f"Symbol {SYMBOL} not found")
            return

        # Ensure symbol is in Market Watch
        if not symbol_info.visible:
            mt5.symbol_select(SYMBOL, True)

        order_type = mt5.ORDER_TYPE_BUY if setup.direction == "LONG" else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(SYMBOL)
        price = tick.ask if setup.direction == "LONG" else tick.bid

        # Entry price validation — reject if market has moved too far from setup entry.
        # The setup entry is a limit-entry zone from historical bars. If price has blown
        # past it by >50% of the risk distance, the structural premise is invalid.
        max_adverse = 0.5 * setup.risk_points
        if setup.direction == "LONG" and price < setup.entry_price - max_adverse:
            print(f"[ORDER] ENTRY_STALE — price {price:.2f} is {setup.entry_price - price:.2f}pts "
                  f"below entry {setup.entry_price:.2f} (max allowed: {max_adverse:.2f}pts)")
            logger.warning(f"Entry stale: price={price:.2f} entry={setup.entry_price:.2f} max_adverse={max_adverse:.2f}")
            _append_trade_log({
                "date": datetime.now(tz=timezone.utc).astimezone(
                    __import__("zoneinfo").ZoneInfo(IST)).strftime("%Y-%m-%d %H:%M"),
                "session": session, "direction": setup.direction,
                "entry": setup.entry_price, "sl": setup.stop_loss, "tp": setup.take_profit,
                "lots": lot_size, "outcome": "ENTRY_STALE",
                "pnl": 0, "equity_after": self.state.equity, "ticket": "",
            })
            return
        if setup.direction == "SHORT" and price > setup.entry_price + max_adverse:
            print(f"[ORDER] ENTRY_STALE — price {price:.2f} is {price - setup.entry_price:.2f}pts "
                  f"above entry {setup.entry_price:.2f} (max allowed: {max_adverse:.2f}pts)")
            logger.warning(f"Entry stale: price={price:.2f} entry={setup.entry_price:.2f} max_adverse={max_adverse:.2f}")
            _append_trade_log({
                "date": datetime.now(tz=timezone.utc).astimezone(
                    __import__("zoneinfo").ZoneInfo(IST)).strftime("%Y-%m-%d %H:%M"),
                "session": session, "direction": setup.direction,
                "entry": setup.entry_price, "sl": setup.stop_loss, "tp": setup.take_profit,
                "lots": lot_size, "outcome": "ENTRY_STALE",
                "pnl": 0, "equity_after": self.state.equity, "ticket": "",
            })
            return

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": lot_size,
            "type": order_type,
            "price": price,
            "sl": setup.stop_loss,
            "tp": setup.take_profit,
            "deviation": 20,        # max 20 points slippage
            "magic": 20260311,      # strategy identifier
            "comment": f"FTMO_{setup.direction}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = result.retcode if result else "None"
            comment = result.comment if result else mt5.last_error()
            logger.error(f"Order failed: retcode={code} comment={comment}")
            if code == 10027:
                print(f"[ORDER] FAILED: AutoTrading is disabled in MT5 terminal.")
                print(f"[ORDER] Fix: click the 'AutoTrading' button in the MT5 toolbar (green play icon),")
                print(f"[ORDER]      or go to Tools → Options → Expert Advisors → Allow automated trading.")
            else:
                print(f"[ORDER] FAILED: {code} — {comment}")
            _append_trade_log({
                "date": datetime.now(tz=timezone.utc).astimezone(
                    __import__("zoneinfo").ZoneInfo(IST)).strftime("%Y-%m-%d %H:%M"),
                "session": session, "direction": setup.direction,
                "entry": setup.entry_price, "sl": setup.stop_loss, "tp": setup.take_profit,
                "lots": lot_size, "outcome": f"ORDER_FAILED_{code}",
                "pnl": 0, "equity_after": self.state.equity, "ticket": "",
            })
            return

        self._open_ticket = result.order
        self._pending_log = {
            "date": datetime.now(tz=timezone.utc).astimezone(
                __import__("zoneinfo").ZoneInfo(IST)).strftime("%Y-%m-%d %H:%M"),
            "session": session, "direction": setup.direction,
            "entry": price, "sl": setup.stop_loss, "tp": setup.take_profit,
            "lots": lot_size, "ticket": result.order,
        }
        print(f"[ORDER] EXECUTED #{result.order}: {setup.direction} {lot_size} lots @ {price:.2f} "
              f"SL={setup.stop_loss:.2f} TP={setup.take_profit:.2f}")
        logger.info(f"Order #{result.order} placed: {setup.direction} {lot_size}L @ {price:.2f}")

    def _monitor_position(self, now_ist):
        mt5 = _get_mt5()
        now_t = now_ist.time()

        # Check if position still open
        positions = mt5.positions_get(symbol=SYMBOL)
        ticket_open = any(p.ticket == self._open_ticket for p in (positions or []))

        if not ticket_open:
            # Position closed (TP or SL hit by broker)
            self._on_position_closed()
            return

        # Time cutoff — close manually if session ended
        session_ended = now_t >= NY2_END or (NY_END <= now_t < NY2_START)
        if session_ended:
            self._close_position()

    def _close_position(self):
        mt5 = _get_mt5()
        positions = mt5.positions_get(symbol=SYMBOL)
        if not positions:
            self._open_ticket = None
            return

        for pos in positions:
            if pos.ticket != self._open_ticket:
                continue
            close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(SYMBOL).bid if pos.type == 0 else mt5.symbol_info_tick(SYMBOL).ask
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": SYMBOL,
                "volume": pos.volume,
                "type": close_type,
                "position": pos.ticket,
                "price": price,
                "deviation": 20,
                "magic": 20260311,
                "comment": "FTMO_TIME_CUTOFF",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"[ORDER] Closed #{pos.ticket} at {price:.2f} (TIME_CUTOFF)")
                logger.info(f"Closed #{pos.ticket} at {price:.2f}")
                if self._pending_log:
                    self._pending_log["outcome"] = "TIME_CUTOFF"
            else:
                logger.error(f"Close failed: {result.retcode if result else mt5.last_error()}")

        self._on_position_closed()

    def _on_position_closed(self):
        mt5 = _get_mt5()
        # Pull closed deal P&L from MT5 history
        deals = mt5.history_deals_get(
            position=self._open_ticket
        )
        pnl = sum(d.profit for d in deals) if deals else 0.0
        self.state = self.risk.update_post_trade(self.state, pnl)
        outcome = "TP" if pnl > 0 else ("SL" if pnl < 0 else "FLAT")
        print(f"[POS] Closed ({outcome}). P&L: {pnl:+.2f} | Equity: {self.state.equity:,.2f}")
        logger.info(f"Position {self._open_ticket} closed. PnL={pnl:.2f} Equity={self.state.equity:.2f}")

        if self._pending_log:
            _append_trade_log({**self._pending_log, "outcome": outcome,
                                "pnl": round(pnl, 2), "equity_after": round(self.state.equity, 2)})
            self._pending_log = None
        self._open_ticket = None

    # ── Data Cache ───────────────────────────────────────────────────────────

    def _update_data_cache(self):
        """Append any new closed M5 bars to cache_m5.parquet."""
        mt5 = _get_mt5()
        # Fetch last 20 bars (covers any missed bars since last poll)
        rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 1, 20)
        # Start from bar index 1 to skip the currently open (incomplete) bar
        if rates is None or len(rates) == 0:
            return

        df = pd.DataFrame(rates)
        df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(IST)
        df = df.rename(columns={"tick_volume": "volume"})[
            ["timestamp", "open", "high", "low", "close", "volume"]
        ]

        # Filter to only bars newer than last cached
        if self._last_cached_ts is not None:
            df = df[df["timestamp"] > self._last_cached_ts]

        if df.empty:
            return

        # Append to parquet (read → concat → write)
        if os.path.isfile(CACHE_PATH):
            existing = pd.read_parquet(CACHE_PATH)
            combined = pd.concat([existing, df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        else:
            combined = df.sort_values("timestamp").reset_index(drop=True)

        combined.to_parquet(CACHE_PATH, index=False)
        self._last_cached_ts = df["timestamp"].max()
        logger.debug(f"Cache updated: +{len(df)} bars, last={self._last_cached_ts}")

    # ── Daily Reset ──────────────────────────────────────────────────────────

    def _on_new_day(self, today: str):
        if self._last_date is not None:
            self.state = self.risk.new_day(self.state)
            logger.info(f"New day: {today} | Equity: {self.state.equity:,.2f}")
        self._session1_scanned_today = False
        self._session2_scanned_today = False
        self._last_date = today

        now_ist = datetime.now(tz=timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo(IST))
        self._himpact_events = _fetch_himpact_usd_events(now_ist)
        if self._himpact_events:
            for ev in self._himpact_events:
                print(f"[NEWS] High-impact today: {ev['title']} @ {ev['dt'].strftime('%H:%M')} IST")
        else:
            print("[NEWS] No high-impact USD events today")
        print(f"[DAY] {today} | Equity: {self.state.equity:,.2f}")

    def _sync_account_state(self):
        """Sync equity from MT5 account info (truth source)."""
        mt5 = _get_mt5()
        info = mt5.account_info()
        if info is not None:
            # Update equity to actual MT5 value; preserve other state fields
            import dataclasses
            self.state = dataclasses.replace(
                self.state,
                equity=info.equity,
                max_equity=max(self.state.max_equity, info.equity),
            )
