"""
NiftyShield Blueprint
---------------------
Serves the NiftyShield weekly options selling dashboard.

Endpoints
---------
GET  /nifty-shield/                  render index page
GET  /nifty-shield/api/status        runner state, regime, position, VIX
GET  /nifty-shield/api/trades        completed straddle trades
GET  /nifty-shield/api/summary       aggregate win/PnL stats
"""
from flask import Blueprint, render_template, jsonify, request, current_app
from pathlib import Path
from datetime import datetime, date
import logging

from flask_app.middleware import login_required
from core.database.manager import DatabaseManager

logger = logging.getLogger("niftyshield_bp")

niftyshield_bp = Blueprint(
    "niftyshield",
    __name__,
    url_prefix="/nifty-shield",
)


# -- Helpers -----------------------------------------------------------------

def _db() -> DatabaseManager:
    return getattr(current_app, "db_manager", None) or DatabaseManager(Path("data"))


def _runner():
    return getattr(current_app, "nifty_shield_runner", None)


def _today() -> str:
    return date.today().isoformat()


def _row_to_dict(cursor, row: tuple) -> dict:
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def _serialise(obj):
    if isinstance(obj, list):
        return [_serialise(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _serialise(v) for k, v in obj.items()}
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj


# -- Page --------------------------------------------------------------------

@niftyshield_bp.route("/")
@login_required
def index():
    return render_template("niftyshield/index.html")


# -- Status ------------------------------------------------------------------

@niftyshield_bp.route("/api/status")
@login_required
def api_status():
    """Return live strategy state + today's signal + open position."""
    runner = _runner()

    # Live in-memory state (preferred)
    live = {}
    if runner and hasattr(runner, "strategy"):
        try:
            live = runner.strategy.get_status()
        except Exception as exc:
            logger.warning(f"get_status failed: {exc}")

    # DB counts for today
    signals_today = 0
    trades_today  = 0
    open_trade    = None
    try:
        with _db().trading_reader() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM ns_paper_signals WHERE session_date=?",
                [_today()]
            ).fetchone()
            signals_today = row[0] if row else 0

            row2 = conn.execute(
                "SELECT COUNT(*) FROM ns_paper_trades WHERE session_date=?",
                [_today()]
            ).fetchone()
            trades_today = row2[0] if row2 else 0

            # Fetch open trade (exit_time IS NULL)
            cur = conn.execute(
                """
                SELECT session_date, underlying, structure, entry_time, entry_price,
                       ce_symbol, pe_symbol, ce_strike, pe_strike,
                       ce_entry_premium, pe_entry_premium, total_premium,
                       lots, entry_delta, entry_theta,
                       predicted_state, confidence, vix_close
                FROM ns_paper_trades
                WHERE session_date=? AND exit_time IS NULL
                ORDER BY entry_time DESC LIMIT 1
                """,
                [_today()]
            )
            row3 = cur.fetchone()
            if row3:
                open_trade = _row_to_dict(cur, row3)
    except Exception as exc:
        logger.warning(f"status DB read: {exc}")

    # Last regime signal from DB (for display before today's 13pm fires)
    last_signal = None
    try:
        with _db().trading_reader() as conn:
            cur = conn.execute(
                """
                SELECT session_date, predicted_state, confidence, vix_close, signal_time
                FROM ns_paper_signals
                WHERE session_date < ?
                ORDER BY session_date DESC, signal_time DESC LIMIT 1
                """,
                [_today()],
            )
            row = cur.fetchone()
            if row:
                last_signal = _row_to_dict(cur, row)
    except Exception as exc:
        logger.warning(f"last_signal DB read: {exc}")

    # Live buffer: count NF 1m bars today
    live_bars = 0
    try:
        from datetime import time as _t
        today_open = datetime.combine(date.today(), _t(9, 0, 0))
        with _db().live_buffer_reader() as conns:
            if "candles" in conns:
                row = conns["candles"].execute(
                    "SELECT COUNT(*) FROM candles WHERE symbol='NSE_INDEX|Nifty 50' "
                    "AND timeframe='1m' AND timestamp >= ?",
                    [today_open],
                ).fetchone()
                live_bars = row[0] if row else 0
    except Exception:
        pass

    # Prefer in-memory live position fields when strategy is positioned.
    # This avoids stale DB open rows showing wrong entry premiums/symbols.
    if live and live.get("state") == "POSITIONED":
        try:
            live_open = {
                "session_date": live.get("session_date"),
                "underlying": "NSE_INDEX|Nifty 50",
                "structure": "SHORT_STRADDLE",
                "entry_time": live.get("entry_time"),
                "ce_symbol": live.get("ce_symbol"),
                "pe_symbol": live.get("pe_symbol"),
                "ce_strike": live.get("ce_strike"),
                "pe_strike": live.get("pe_strike"),
                "ce_entry_premium": live.get("ce_entry_premium"),
                "pe_entry_premium": live.get("pe_entry_premium"),
                "total_premium": live.get("total_premium"),
                "lots": live.get("lots"),
                "entry_delta": live.get("entry_delta"),
                "entry_theta": live.get("entry_theta"),
                "expiry": live.get("expiry"),
                "ce_now": live.get("ce_now"),
                "pe_now": live.get("pe_now"),
                "total_now": live.get("total_now"),
                "mtm_gross_rs": live.get("mtm_gross_rs"),
                "mtm_net_rs": live.get("mtm_net_rs"),
            }
            open_trade = {**(open_trade or {}), **{k: v for k, v in live_open.items() if v is not None}}
        except Exception:
            pass
    # If not positioned, still enrich any DB open trade with live MTM fields.
    elif open_trade and live:
        try:
            open_trade["ce_now"] = live.get("ce_now")
            open_trade["pe_now"] = live.get("pe_now")
            open_trade["total_now"] = live.get("total_now")
            open_trade["mtm_gross_rs"] = live.get("mtm_gross_rs")
            open_trade["mtm_net_rs"] = live.get("mtm_net_rs")
        except Exception:
            pass

    return jsonify(_serialise({
        "success":       True,
        "runner_active": runner is not None,
        "live":          live,
        "signals_today": signals_today,
        "trades_today":  trades_today,
        "open_trade":    open_trade,
        "live_bars_nf":  live_bars,
        "last_signal":   last_signal,
        "today":         _today(),
    }))


# -- Trades ------------------------------------------------------------------

@niftyshield_bp.route("/api/trades")
@login_required
def api_trades():
    """Return completed straddle trades, most recent first."""
    session_date = request.args.get("date", _today())
    limit        = int(request.args.get("limit", 200))
    all_dates    = request.args.get("all", "false").lower() == "true"

    trades = []
    try:
        with _db().trading_reader() as conn:
            if all_dates or not session_date:
                cur = conn.execute(
                    """
                    SELECT id, session_date, underlying, structure,
                           entry_time, entry_price,
                           ce_symbol, pe_symbol, ce_strike, pe_strike,
                           ce_entry_premium, pe_entry_premium, total_premium,
                           lots, entry_delta, entry_theta,
                           exit_time, exit_price,
                           ce_exit_premium, pe_exit_premium,
                           exit_reason, pnl_gross_rs, pnl_net_rs, costs_rs,
                           max_loss_rs, adjustments,
                           predicted_state, confidence, vix_close, created_at
                    FROM ns_paper_trades
                    WHERE exit_time IS NOT NULL
                    ORDER BY entry_time DESC
                    LIMIT ?
                    """,
                    [limit],
                )
            else:
                cur = conn.execute(
                    """
                    SELECT id, session_date, underlying, structure,
                           entry_time, entry_price,
                           ce_symbol, pe_symbol, ce_strike, pe_strike,
                           ce_entry_premium, pe_entry_premium, total_premium,
                           lots, entry_delta, entry_theta,
                           exit_time, exit_price,
                           ce_exit_premium, pe_exit_premium,
                           exit_reason, pnl_gross_rs, pnl_net_rs, costs_rs,
                           max_loss_rs, adjustments,
                           predicted_state, confidence, vix_close, created_at
                    FROM ns_paper_trades
                    WHERE exit_time IS NOT NULL AND session_date=?
                    ORDER BY entry_time DESC
                    LIMIT ?
                    """,
                    [session_date, limit],
                )
            for row in cur.fetchall():
                trades.append(_row_to_dict(cur, row))
    except Exception as exc:
        logger.warning(f"trades DB read: {exc}")

    return jsonify(_serialise({"success": True, "trades": trades}))


# -- Summary -----------------------------------------------------------------

@niftyshield_bp.route("/api/summary")
@login_required
def api_summary():
    """Aggregate stats across all completed trades."""
    stats = {
        "total_trades":   0,
        "wins":           0,
        "losses":         0,
        "win_rate":       None,
        "net_pnl_rs":     0.0,
        "gross_pnl_rs":   0.0,
        "avg_premium":    None,
        "avg_pnl_rs":     None,
        "max_dd_rs":      0.0,
        "profit_target":  0,
        "stop_loss":      0,
        "time_exit":      0,
        "choppy_trades":  0,
        "choppy_wr":      None,
        "trend_trades":   0,
        "trend_wr":       None,
    }
    try:
        with _db().trading_reader() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as n,
                    SUM(CASE WHEN pnl_net_rs > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl_net_rs <= 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pnl_net_rs) as net_pnl,
                    SUM(pnl_gross_rs) as gross_pnl,
                    AVG(total_premium) as avg_prem,
                    MAX(max_loss_rs) as max_dd,
                    SUM(CASE WHEN exit_reason='profit_target' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN exit_reason='stop_loss'     THEN 1 ELSE 0 END),
                    SUM(CASE WHEN exit_reason='time_exit'     THEN 1 ELSE 0 END)
                FROM ns_paper_trades
                WHERE exit_time IS NOT NULL
                  AND (source IS NULL OR source = 'live')
                """
            ).fetchone()
            if row and row[0]:
                n = row[0]
                w = row[1] or 0
                stats["total_trades"]  = n
                stats["wins"]          = w
                stats["losses"]        = row[2] or 0
                stats["win_rate"]      = round(w / n * 100, 1) if n else None
                stats["net_pnl_rs"]    = round(row[3] or 0.0, 0)
                stats["gross_pnl_rs"]  = round(row[4] or 0.0, 0)
                stats["avg_premium"]   = round(row[5] or 0.0, 1) if row[5] else None
                stats["avg_pnl_rs"]    = round((row[3] or 0.0) / n, 0) if n else None
                stats["max_dd_rs"]     = round(row[6] or 0.0, 0)
                stats["profit_target"] = row[7] or 0
                stats["stop_loss"]     = row[8] or 0
                stats["time_exit"]     = row[9] or 0

            # Regime breakdown
            reg = conn.execute(
                """
                SELECT predicted_state,
                       COUNT(*) as n,
                       SUM(CASE WHEN pnl_net_rs > 0 THEN 1 ELSE 0 END) as w
                FROM ns_paper_trades
                WHERE exit_time IS NOT NULL
                  AND (source IS NULL OR source = 'live')
                GROUP BY predicted_state
                """
            ).fetchall()
            for r in reg:
                state, n, w = r[0], r[1] or 0, r[2] or 0
                if state == "Choppy":
                    stats["choppy_trades"] = n
                    stats["choppy_wr"] = round(w / n * 100, 1) if n else None
                else:
                    stats["trend_trades"] += n

            # Trend win rate
            tr_row = conn.execute(
                """
                SELECT COUNT(*) as n,
                       SUM(CASE WHEN pnl_net_rs > 0 THEN 1 ELSE 0 END) as w
                FROM ns_paper_trades
                WHERE exit_time IS NOT NULL
                  AND (source IS NULL OR source = 'live')
                  AND predicted_state IN ('BullTrend','BearTrend')
                """
            ).fetchone()
            if tr_row and tr_row[0]:
                tn, tw = tr_row[0] or 0, tr_row[1] or 0
                stats["trend_wr"] = round(tw / tn * 100, 1) if tn else None

    except Exception as exc:
        logger.warning(f"summary DB read: {exc}")

    return jsonify({"success": True, "summary": stats})
