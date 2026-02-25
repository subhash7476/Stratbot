"""
Paper Trading Blueprint
-----------------------
Serves the Stock Day-Type paper trading dashboard.

Endpoints
---------
GET  /paper-trading/                   render index page
GET  /paper-trading/api/status         runner status + selected broker
GET  /paper-trading/api/brokers        available broker / cost models
POST /paper-trading/api/config         set broker or stop_pct
GET  /paper-trading/api/signals        today's per-symbol classification
GET  /paper-trading/api/positions      currently open paper positions
GET  /paper-trading/api/trades         today's trades (+ optional history)
GET  /paper-trading/api/summary        aggregate stats for a given date
"""
from flask import Blueprint, render_template, jsonify, request, current_app
from pathlib import Path
from datetime import datetime, date
import sqlite3
import logging

from flask_app.middleware import login_required
from core.database.manager import DatabaseManager
from core.strategies.stock_daytype_paper import BROKERS

logger = logging.getLogger("paper_trading_bp")

paper_trading_bp = Blueprint(
    "paper_trading",
    __name__,
    url_prefix="/paper-trading",
)


# -- Helpers -----------------------------------------------------------------

def _db() -> DatabaseManager:
    return getattr(current_app, "db_manager", None) or DatabaseManager(Path("data"))


def _runner():
    """Return the StockDaytypeRunner attached to the app (if running)."""
    return getattr(current_app, "paper_runner", None)


def _today() -> str:
    return date.today().isoformat()


def _row_to_dict(cursor: sqlite3.Cursor, row: tuple) -> dict:
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


# -- Page --------------------------------------------------------------------

@paper_trading_bp.route("/")
@login_required
def index():
    return render_template("paper_trading/index.html")


# -- Status ------------------------------------------------------------------

@paper_trading_bp.route("/api/status")
@login_required
def api_status():
    """Return runner status and current broker setting."""
    runner = _runner()
    if runner:
        status = "running"
        broker = runner.broker
        stop_pct = runner.stop_pct
        min_confidence = runner.min_confidence
        symbols_count = len(runner.symbols)
    else:
        status = "stopped"
        broker = "paper"
        stop_pct = 0.01
        min_confidence = 0.50
        symbols_count = 0

    # Count today's signals and trades from DB
    signals_count = 0
    trades_count  = 0
    try:
        with _db().trading_reader() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM stock_paper_signals WHERE session_date = ?",
                [_today()]
            ).fetchone()
            signals_count = row[0] if row else 0

            row2 = conn.execute(
                "SELECT COUNT(*) FROM stock_paper_trades WHERE session_date = ?",
                [_today()]
            ).fetchone()
            trades_count = row2[0] if row2 else 0
    except Exception:
        pass

    return jsonify({
        "success": True,
        "status": status,
        "broker": broker,
        "broker_label": BROKERS.get(broker, {}).get("label", broker),
        "stop_pct": stop_pct,
        "min_confidence": min_confidence,
        "symbols_count": symbols_count,
        "signals_today": signals_count,
        "trades_today":  trades_count,
        "today": _today(),
    })


# -- Brokers -----------------------------------------------------------------

@paper_trading_bp.route("/api/brokers")
@login_required
def api_brokers():
    """Return the list of available broker / cost-model options."""
    return jsonify({
        "success": True,
        "brokers": [
            {"id": k, "label": v["label"], "cost_pct": v["cost_pct"]}
            for k, v in BROKERS.items()
        ],
    })


# -- Config ------------------------------------------------------------------

@paper_trading_bp.route("/api/config", methods=["POST"])
@login_required
def api_config():
    """Update broker or stop_pct on the running runner."""
    data = request.get_json() or {}
    runner = _runner()
    if not runner:
        return jsonify({"success": False, "error": "Runner not active"}), 400

    changed = []
    if "broker" in data:
        ok = runner.set_broker(data["broker"])
        if ok:
            changed.append(f"broker={data['broker']}")
        else:
            return jsonify({"success": False, "error": f"Unknown broker: {data['broker']}"}), 400

    if "stop_pct" in data:
        try:
            pct = float(data["stop_pct"])
            if 0 < pct < 0.1:
                runner.stop_pct = pct
                runner.strategy.stop_pct = pct
                changed.append(f"stop_pct={pct}")
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Invalid stop_pct"}), 400

    return jsonify({"success": True, "changed": changed})


# -- Signals -----------------------------------------------------------------

@paper_trading_bp.route("/api/signals")
@login_required
def api_signals():
    """
    Returns today's per-symbol classifications.
    Priority: live in-memory state (if runner active), else DB.
    Symbols not yet at checkpoint show as PENDING.
    """
    session_date = request.args.get("date", _today())
    runner = _runner()

    # -- Live in-memory signals (preferred when checkpoint has fired) ----------
    mem_signals = None
    if runner and session_date == _today():
        mem_signals = runner.strategy.get_signals()
        if any(s.get("predicted_state") != "PENDING" for s in mem_signals):
            # Checkpoint fired in memory — return live state immediately
            return jsonify({"success": True, "signals": _serialise(mem_signals)})
        # Checkpoint hasn't fired yet (pre-10 AM or post-restart) — fall through to DB

    # -- DB fallback (historical dates, runner stopped, or pre-checkpoint) ----
    signals = []
    try:
        with _db().trading_reader() as conn:
            cur = conn.execute(
                """
                SELECT symbol, trading_symbol, predicted_state, confidence,
                       p_bull, p_bear, p_choppy,
                       signal_time, c_ret, c_range, c_close_loc, broker, session_date
                FROM stock_paper_signals
                WHERE session_date = ?
                ORDER BY confidence DESC
                """,
                [session_date],
            )
            for row in cur.fetchall():
                signals.append(_row_to_dict(cur, row))
    except Exception as exc:
        logger.warning(f"signals DB read: {exc}")

    # DB has today's signals (post-restart scenario) → use them
    if signals:
        return jsonify({"success": True, "signals": _serialise(signals), "source": "db"})

    # DB is empty AND runner is active → genuine pre-10 AM, show PENDING symbol list
    if mem_signals is not None:
        return jsonify({"success": True, "signals": _serialise(mem_signals), "source": "pending"})

    return jsonify({"success": True, "signals": [], "source": "db"})


# -- Positions ---------------------------------------------------------------

@paper_trading_bp.route("/api/positions")
@login_required
def api_positions():
    """Return currently open paper positions with live unrealised P&L."""
    runner = _runner()
    if not runner:
        # Try to read open trades from DB (exit_time IS NULL)
        positions = []
        try:
            with _db().trading_reader() as conn:
                cur = conn.execute(
                    """
                    SELECT symbol, trading_symbol, direction, entry_time,
                           entry_price, stop_price, target_price, qty, capital,
                           confidence, predicted_state, session_date, broker
                    FROM stock_paper_trades
                    WHERE exit_time IS NULL AND session_date = ?
                    ORDER BY entry_time ASC
                    """,
                    [_today()],
                )
                for row in cur.fetchall():
                    pos = _row_to_dict(cur, row)
                    pos["current_price"] = None
                    pos["unrealised_pct"] = None
                    pos["unrealised_rs"]  = None
                    positions.append(pos)
        except Exception as exc:
            logger.warning(f"positions DB read: {exc}")
        return jsonify({"success": True, "positions": _serialise(positions)})

    positions = runner.strategy.get_positions()

    # Enrich with live current price from the buffer
    symbol_prices = _fetch_latest_prices([p["symbol"] for p in positions])
    for pos in positions:
        price = symbol_prices.get(pos["symbol"])
        pos["current_price"] = price
        if price and pos.get("entry_price"):
            ep = pos["entry_price"]
            qty = pos.get("qty", 1)
            if pos["direction"] == "long":
                pos["unrealised_pct"] = round((price - ep) / ep * 100, 4)
                pos["unrealised_rs"]  = round(qty * (price - ep), 2)
            else:
                pos["unrealised_pct"] = round((ep - price) / ep * 100, 4)
                pos["unrealised_rs"]  = round(qty * (ep - price), 2)
        else:
            pos["unrealised_pct"] = None
            pos["unrealised_rs"]  = None

    return jsonify({"success": True, "positions": _serialise(positions)})


# -- Trades ------------------------------------------------------------------

@paper_trading_bp.route("/api/trades")
@login_required
def api_trades():
    """Return completed trades, most recent first."""
    session_date = request.args.get("date", _today())
    limit        = int(request.args.get("limit", 200))
    all_dates    = request.args.get("all", "false").lower() == "true"

    trades = []
    try:
        with _db().trading_reader() as conn:
            if all_dates:
                cur = conn.execute(
                    """
                    SELECT id, session_date, symbol, trading_symbol,
                           direction, broker, confidence, predicted_state,
                           qty, capital,
                           entry_time, entry_price, stop_price, target_price,
                           exit_time, exit_price, exit_reason,
                           pnl_gross_pct, pnl_net_pct, pnl_rs, cost_pct, created_at
                    FROM stock_paper_trades
                    WHERE exit_time IS NOT NULL
                    ORDER BY entry_time DESC
                    LIMIT ?
                    """,
                    [limit],
                )
            else:
                cur = conn.execute(
                    """
                    SELECT id, session_date, symbol, trading_symbol,
                           direction, broker, confidence, predicted_state,
                           qty, capital,
                           entry_time, entry_price, stop_price, target_price,
                           exit_time, exit_price, exit_reason,
                           pnl_gross_pct, pnl_net_pct, pnl_rs, cost_pct, created_at
                    FROM stock_paper_trades
                    WHERE exit_time IS NOT NULL AND session_date = ?
                    ORDER BY entry_time DESC
                    LIMIT ?
                    """,
                    [session_date, limit],
                )
            for row in cur.fetchall():
                trades.append(_row_to_dict(cur, row))
    except Exception as exc:
        logger.warning(f"trades DB read: {exc}")

    return jsonify({"success": True, "trades": _serialise(trades)})


# -- Summary -----------------------------------------------------------------

@paper_trading_bp.route("/api/summary")
@login_required
def api_summary():
    """Return aggregate stats for a given session date."""
    session_date = request.args.get("date", _today())
    stats = {
        "session_date":  session_date,
        "signals":       0,
        "bull_signals":  0,
        "bear_signals":  0,
        "choppy_signals": 0,
        "trades":        0,
        "wins":          0,
        "losses":        0,
        "win_rate":      None,
        "total_pnl_net": 0.0,
        "total_pnl_rs":  0.0,
        "avg_confidence": None,
        "open_positions": 0,
    }
    try:
        with _db().trading_reader() as conn:
            # Signals breakdown
            sig_row = conn.execute(
                """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN predicted_state='BullTrend' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN predicted_state='BearTrend' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN predicted_state='Choppy'    THEN 1 ELSE 0 END),
                    AVG(confidence)
                FROM stock_paper_signals
                WHERE session_date = ?
                """,
                [session_date],
            ).fetchone()
            if sig_row and sig_row[0]:
                stats["signals"]        = sig_row[0] or 0
                stats["bull_signals"]   = sig_row[1] or 0
                stats["bear_signals"]   = sig_row[2] or 0
                stats["choppy_signals"] = sig_row[3] or 0
                stats["avg_confidence"] = round(sig_row[4] * 100, 1) if sig_row[4] else None

            # Trades breakdown (closed only)
            trade_row = conn.execute(
                """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl_net_pct > 0 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN pnl_net_pct <= 0 THEN 1 ELSE 0 END),
                    SUM(pnl_net_pct),
                    SUM(pnl_rs)
                FROM stock_paper_trades
                WHERE session_date = ? AND exit_time IS NOT NULL
                """,
                [session_date],
            ).fetchone()
            if trade_row and trade_row[0]:
                n = trade_row[0] or 0
                w = trade_row[1] or 0
                stats["trades"]        = n
                stats["wins"]          = w
                stats["losses"]        = trade_row[2] or 0
                stats["win_rate"]      = round(w / n * 100, 1) if n else None
                stats["total_pnl_net"] = round((trade_row[3] or 0.0) * 100, 4)
                stats["total_pnl_rs"]  = round(trade_row[4] or 0.0, 2)

            # Open positions count (DB fallback; overridden below if runner active)
            open_row = conn.execute(
                "SELECT COUNT(*) FROM stock_paper_trades WHERE session_date=? AND exit_time IS NULL",
                [session_date],
            ).fetchone()
            stats["open_positions"] = open_row[0] if open_row else 0

    except Exception as exc:
        logger.warning(f"summary DB read: {exc}")

    # Use in-memory position count when runner is active (avoids DB catchup mismatch)
    runner = _runner()
    if runner and session_date == _today():
        stats["open_positions"] = len(runner.strategy.get_positions())

    return jsonify({"success": True, "summary": stats})


# -- Helpers -----------------------------------------------------------------

def _fetch_latest_prices(symbols):
    """Look up the most recent candle close for each symbol from the live buffer."""
    prices = {}
    if not symbols:
        return prices
    try:
        with _db().live_buffer_reader() as conns:
            if "candles" not in conns:
                return prices
            conn = conns["candles"]
            for sym in symbols:
                row = conn.execute(
                    """
                    SELECT close FROM candles
                    WHERE symbol = ? AND timeframe = '1m'
                    ORDER BY timestamp DESC LIMIT 1
                    """,
                    [sym],
                ).fetchone()
                if row:
                    prices[sym] = float(row[0])
    except Exception:
        pass
    return prices


def _serialise(obj):
    """Recursively convert non-JSON-serialisable objects for JSON output."""
    if isinstance(obj, list):
        return [_serialise(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _serialise(v) for k, v in obj.items()}
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj
