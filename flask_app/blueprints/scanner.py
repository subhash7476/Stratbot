"""
Nifty PM Strategy (V9 Scalper) — Flask monitoring blueprint.

Replaces the old scanner page.  All data is read from:
  - logs/v9_paper_trades.csv   — completed trade log (written by run_v9_paper.py)
  - data/live_buffer/candles_today.duckdb  — live Nifty bars (or today's archive)

Session state is derived from the CSV + current IST time; no background thread needed.
"""

import csv
import logging
import time as _time_mod
from datetime import date, datetime, time as dt_time
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, current_app
from flask_app.middleware import login_required
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

scanner_bp = Blueprint("scanner", __name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
IST        = ZoneInfo("Asia/Kolkata")
ROOT       = Path(__file__).resolve().parent.parent.parent
# PAPER_CSV is no longer the primary source, but kept for migration
PAPER_CSV  = ROOT / "logs" / "v9_paper_trades.csv"
LIVE_DB    = ROOT / "data" / "live_buffer" / "candles_today.duckdb"
CANDLE_DIR = ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
SYMBOL     = "NSE_INDEX|Nifty 50"

# ── Strategy constants ─────────────────────────────────────────────────────────
MIN_CONF        = 0.75
STOP_PCT        = 0.30
COST_PCT        = 0.04
OPEN_TIME       = dt_time(9, 15)
CHECKPOINT_TIME = dt_time(13, 0)
ENTRY_TIME      = dt_time(13, 2)
EXIT_TIME       = dt_time(14, 45)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _today() -> str:
    return str(date.today())


def _now_ist() -> datetime:
    return datetime.now(IST)


def _read_trades() -> list:
    """Read all trades from SQLite trading.db, newest first."""
    db_manager = getattr(current_app, "db_manager", None)
    if not db_manager:
        return []
    
    try:
        with db_manager.trading_reader() as conn:
            rows = conn.execute(
                """
                SELECT session_date, entry_time, entry_price, stop_level,
                       exit_time, exit_price, exit_reason, confidence,
                       predicted_state, pnl_gross_pct, pnl_net_pct, model_version
                FROM v9_paper_trades
                ORDER BY session_date DESC, entry_time DESC
                """
            ).fetchall()
            
            trades = []
            for r in rows:
                trades.append({
                    "session_date":    r[0],
                    "entry_time":      r[1],
                    "entry_price":     r[2],
                    "stop_level":      r[3],
                    "exit_time":       r[4],
                    "exit_price":      r[5],
                    "exit_reason":     r[6],
                    "confidence":      r[7],
                    "predicted_state": r[8],
                    "pnl_gross_pct":   r[9],
                    "pnl_net_pct":     r[10],
                    "model_version":   r[11]
                })
            return trades
    except Exception as exc:
        logger.warning(f"[NiftyPM] DB read error: {exc}")
        return []


def _get_live_bar() -> dict:
    """Return latest 1m Nifty bar from live buffer (or today's archive)."""
    candidates = [LIVE_DB, CANDLE_DIR / f"{_today()}.duckdb"]
    for db_path in candidates:
        if not db_path.exists():
            continue
        is_live = db_path == LIVE_DB
        for _ in range(3):
            try:
                import duckdb
                con = duckdb.connect(str(db_path), read_only=True)
                tf  = "AND timeframe = '1m'" if is_live else ""
                row = con.execute(
                    f"SELECT timestamp, open, high, low, close, volume "
                    f"FROM candles WHERE symbol = ? {tf} "
                    f"ORDER BY timestamp DESC LIMIT 1",
                    [SYMBOL],
                ).fetchone()
                con.close()
                if row:
                    return {
                        "timestamp": str(row[0]),
                        "close":     float(row[4]),
                        "high":      float(row[2]),
                        "low":       float(row[3]),
                    }
            except Exception:
                _time_mod.sleep(0.3)
    return {}


def _derive_state(trades: list) -> dict:
    """Build today's session-state dict from CSV + current IST time + live bar."""
    today_str = _today()
    now       = _now_ist()
    now_t     = now.time()

    live    = _get_live_bar()
    last_px = live.get("close")
    last_ts = live.get("timestamp")

    today_trade = next(
        (t for t in trades if t.get("session_date") == today_str), None
    )

    if today_trade:
        has_exit   = bool(today_trade.get("exit_time"))
        entry_px   = today_trade.get("entry_price")
        stop_level = today_trade.get("stop_level")

        # Unrealised P&L while still open
        unreal_pct = unreal_pts = None
        if not has_exit and entry_px and last_px:
            unreal_pts = round(last_px - entry_px, 2)
            unreal_pct = round((unreal_pts / entry_px) * 100, 4)

        return {
            "state":         "DONE" if has_exit else "IN_POSITION",
            "session_date":  today_str,
            "day_type":      today_trade.get("predicted_state") or "Unknown",
            "confidence":    today_trade.get("confidence"),
            "entry_time":    today_trade.get("entry_time"),
            "entry_price":   entry_px,
            "stop_level":    stop_level,
            "exit_time":     today_trade.get("exit_time")      if has_exit else None,
            "exit_price":    today_trade.get("exit_price")     if has_exit else None,
            "exit_reason":   today_trade.get("exit_reason")    if has_exit else None,
            "pnl_gross_pct": today_trade.get("pnl_gross_pct") if has_exit else None,
            "pnl_net_pct":   today_trade.get("pnl_net_pct")   if has_exit else None,
            "unreal_pct":    unreal_pct,
            "unreal_pts":    unreal_pts,
            "last_price":    last_px,
            "last_ts":       last_ts,
            "last_high":     live.get("high"),
            "last_low":      live.get("low"),
            "model_version": today_trade.get("model_version") or "",
        }

    # No trade today — infer phase from time
    if   now_t < OPEN_TIME:        phase, note = "PRE_MARKET",  "Market opens at 09:15 IST"
    elif now_t < CHECKPOINT_TIME:  phase, note = "IDLE",        "AM session · checkpoint fires at 13:00"
    elif now_t < ENTRY_TIME:       phase, note = "CHECKPOINT",  "13:00 PM checkpoint computing…"
    elif now_t < EXIT_TIME:        phase, note = "NO_TRADE",    f"No BullTrend \u2265 {MIN_CONF:.0%} today \u2014 sitting out"
    else:                          phase, note = "CLOSED",      "Session closed \u2014 no trade taken today"

    return {
        "state":        phase,
        "note":         note,
        "session_date": today_str,
        "last_price":   last_px,
        "last_ts":      last_ts,
        "last_high":    live.get("high"),
        "last_low":     live.get("low"),
    }


def _compute_summary(trades: list) -> dict:
    """Aggregate performance stats from all closed trades."""
    closed = [
        t for t in trades
        if t.get("exit_time") and t.get("pnl_net_pct") is not None
    ]
    if not closed:
        return {"total_trades": 0}

    # Reverse to chronological for cumulative P&L
    chrono = [float(t["pnl_net_pct"]) for t in reversed(closed)]
    wins   = [p for p in chrono if p > 0]
    losses = [p for p in chrono if p <= 0]

    # Max drawdown
    peak = cum = max_dd = 0.0
    for p in chrono:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    avg_win  = sum(wins)   / len(wins)   if wins   else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    wr       = len(wins) / len(closed)
    exp      = wr * avg_win + (1 - wr) * avg_loss

    return {
        "total_trades":      len(closed),
        "wins":              len(wins),
        "losses":            len(losses),
        "win_rate":          round(wr * 100, 1),
        "avg_pnl_net_pct":   round(sum(chrono) / len(chrono), 4),
        "total_pnl_net_pct": round(sum(chrono), 4),
        "max_dd_pct":        round(max_dd, 4),
        "avg_win_pct":       round(avg_win,  4),
        "avg_loss_pct":      round(avg_loss, 4),
        "expectancy":        round(exp, 4),
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@scanner_bp.route("/")
@login_required
def index():
    return render_template("scanner/index.html")


@scanner_bp.route("/api/session")
@login_required
def api_session():
    trades = _read_trades()
    return jsonify({"success": True, "session": _derive_state(trades)})


@scanner_bp.route("/api/trades")
@login_required
def api_trades():
    trades  = _read_trades()
    all_d   = request.args.get("all") == "true"
    if not all_d:
        today   = _today()
        today_t = [t for t in trades if t.get("session_date") == today]
        other   = [t for t in trades if t.get("session_date") != today][:24]
        trades  = today_t + other
    return jsonify({"success": True, "trades": trades, "count": len(trades)})


@scanner_bp.route("/api/summary")
@login_required
def api_summary():
    trades = _read_trades()
    return jsonify({"success": True, "summary": _compute_summary(trades)})
