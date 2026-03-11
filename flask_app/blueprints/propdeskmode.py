"""
Prop Desk Mode Blueprint
------------------------
Multi-underlying volatility control board.

Endpoints
---------
GET  /prop-desk/                  render index page
GET  /prop-desk/api/portfolio     global risk + per-underlying tiles
GET  /prop-desk/api/correlation   rolling 5-day NF/BN Pearson correlation (5-min cache)
POST /prop-desk/api/kill-switch   emergency close (confirm=true required)
"""
from flask import Blueprint, render_template, jsonify, request, current_app
from pathlib import Path
from datetime import datetime, date, time as _time, timezone, timedelta
import logging
import math

from flask_app.middleware import login_required
from core.database.manager import DatabaseManager
from core.risk.greeks.black76_engine import Black76Engine

logger = logging.getLogger("propdeskmode_bp")

IST = timezone(timedelta(hours=5, minutes=30))

propdeskmode_bp = Blueprint(
    "propdeskmode",
    __name__,
    url_prefix="/prop-desk",
)

# ── Module-level correlation cache ──────────────────────────────────────────
_corr_cache: dict = {}   # {"ts": float, "result": dict}
_CORR_CACHE_SECS = 300   # 5 minutes


# ── Helpers ──────────────────────────────────────────────────────────────────

def _db() -> DatabaseManager:
    return getattr(current_app, "db_manager", None) or DatabaseManager(Path("data"))


def _runner():
    return getattr(current_app, "nifty_shield_runner", None)


def _today() -> str:
    return date.today().isoformat()


def _ist_now() -> datetime:
    return datetime.now(IST)


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


def _get_live_spot(db: DatabaseManager, symbol: str) -> float | None:
    """Return last 1m close from live buffer for given symbol."""
    try:
        with db.live_buffer_reader() as conns:
            if "candles" not in conns:
                return None
            row = conns["candles"].execute(
                "SELECT close FROM candles WHERE symbol=? AND timeframe='1m' "
                "ORDER BY timestamp DESC LIMIT 1",
                [symbol]
            ).fetchone()
            return float(row[0]) if row else None
    except Exception as exc:
        logger.warning(f"_get_live_spot({symbol}): {exc}")
        return None


def _get_open_trade(db: DatabaseManager, underlying: str) -> dict | None:
    """Return current open straddle from ns_paper_trades (exit_time IS NULL)."""
    try:
        with db.trading_reader() as conn:
            cur = conn.execute(
                """
                SELECT ce_strike, pe_strike,
                       ce_entry_premium, pe_entry_premium, total_premium,
                       lots, entry_price, entry_time
                FROM ns_paper_trades
                WHERE underlying=? AND exit_time IS NULL
                ORDER BY entry_time DESC LIMIT 1
                """,
                [underlying]
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
    except Exception as exc:
        logger.warning(f"_get_open_trade: {exc}")
        return None


def _get_today_pnl(db: DatabaseManager) -> float:
    """Sum of pnl_net_rs for today's closed trades."""
    try:
        with db.trading_reader() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl_net_rs), 0) FROM ns_paper_trades "
                "WHERE session_date=? AND exit_time IS NOT NULL",
                [_today()]
            ).fetchone()
            return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


# ── Correlation ───────────────────────────────────────────────────────────────

def _load_closes_from_files(symbol: str, days: int = 5) -> list[float]:
    """Load last `days` trading sessions of 1m closes from daily DuckDB files."""
    import duckdb
    data_dir = Path("data/market_data/nse/candles/1m")
    files = sorted(data_dir.glob("*.duckdb"), reverse=True)
    closes: list[float] = []
    days_used = 0
    for f in files:
        if days_used >= days:
            break
        try:
            con = duckdb.connect(str(f), read_only=True)
            rows = con.execute(
                "SELECT close FROM candles WHERE symbol=? ORDER BY timestamp ASC",
                [symbol]
            ).fetchall()
            con.close()
            if rows:
                closes.extend(float(r[0]) for r in rows)
                days_used += 1
        except Exception:
            continue
    return closes


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 10:
        return None
    xs, ys = xs[:n], ys[:n]
    # log returns
    lx = [math.log(xs[i] / xs[i-1]) for i in range(1, n) if xs[i-1] > 0 and xs[i] > 0]
    ly = [math.log(ys[i] / ys[i-1]) for i in range(1, n) if ys[i-1] > 0 and ys[i] > 0]
    n2 = min(len(lx), len(ly))
    if n2 < 5:
        return None
    lx, ly = lx[:n2], ly[:n2]
    mx = sum(lx) / n2
    my = sum(ly) / n2
    cov = sum((lx[i] - mx) * (ly[i] - my) for i in range(n2))
    sx  = math.sqrt(sum((v - mx) ** 2 for v in lx))
    sy  = math.sqrt(sum((v - my) ** 2 for v in ly))
    if sx == 0 or sy == 0:
        return None
    return round(cov / (sx * sy), 4)


def _compute_correlation() -> dict:
    nf_closes = _load_closes_from_files("NSE_INDEX|Nifty 50",   days=5)
    bn_closes = _load_closes_from_files("NSE_INDEX|Nifty Bank", days=5)
    corr = _pearson(nf_closes, bn_closes)
    abs_c = abs(corr) if corr is not None else 0.0
    return {
        "nf_bn":    corr,
        "warning":  abs_c >= 0.85,
        "amber":    0.75 <= abs_c < 0.85,
        "days_used": min(5, len(nf_closes) // 375 + 1),
    }


def _correlation_cached() -> dict:
    import time as _time_mod
    now_ts = _time_mod.time()
    cached = _corr_cache.get("result")
    cached_ts = _corr_cache.get("ts", 0.0)
    if cached and (now_ts - cached_ts) < _CORR_CACHE_SECS:
        return cached
    result = _compute_correlation()
    _corr_cache["result"] = result
    _corr_cache["ts"] = now_ts
    return result


# ── Portfolio tile builders ───────────────────────────────────────────────────

def _clustered_risk(underlyings: list[dict], corr_nf_bn: float | None, dte: int) -> str:
    positioned = [u for u in underlyings if u.get("state") == "POSITIONED"]
    abs_c = abs(corr_nf_bn or 0.0)
    if len(positioned) >= 2 and abs_c >= 0.85 and dte <= 2:
        return "HIGH"
    if len(positioned) >= 2 and abs_c >= 0.75:
        return "MODERATE"
    return "LOW"


def _build_nifty_tile(runner, db: DatabaseManager, now_ist: datetime) -> dict:
    base = {"id": "nifty", "name": "NIFTY", "underlying": "NSE_INDEX|Nifty 50"}

    status = {}
    if runner and hasattr(runner, "strategy"):
        try:
            status = runner.strategy.get_status()
        except Exception as exc:
            logger.warning(f"get_status: {exc}")

    state     = status.get("state", "IDLE")
    day_type  = status.get("day_type", "Unknown")
    vix_close = status.get("vix_close")
    lots      = status.get("lots", 0)
    base.update({"state": state, "day_type": day_type, "vix_close": vix_close,
                 "confidence": status.get("confidence"), "adjustments": status.get("adjustments", 0)})

    if state != "POSITIONED":
        return base

    spot       = _get_live_spot(db, "NSE_INDEX|Nifty 50")
    open_trade = _get_open_trade(db, "NSE_INDEX|Nifty 50")

    if not open_trade or not spot:
        return base

    live_ce_strike = status.get("ce_strike")
    live_pe_strike = status.get("pe_strike")
    live_ce_entry = status.get("ce_entry_premium")
    live_pe_entry = status.get("pe_entry_premium")

    # Expiry: authoritative source — always from the live strategy object
    try:
        expiry = runner.strategy._ce_option.expiry          # date object
    except Exception:
        return base

    # Continuous TTM (seconds-based, IST-aware)
    expiry_dt = datetime.combine(expiry, _time(15, 30), tzinfo=IST)
    tte = max((expiry_dt - now_ist).total_seconds() / (365.25 * 24 * 3600), 1e-6)

    K      = live_ce_strike if live_ce_strike is not None else open_trade["ce_strike"]
    ce_ent = live_ce_entry if live_ce_entry is not None else open_trade["ce_entry_premium"]
    pe_ent = live_pe_entry if live_pe_entry is not None else open_trade["pe_entry_premium"]
    iv     = (vix_close or 14.0) / 100.0
    r      = 0.065
    lot_sz = 75

    # Prefer live option leg LTP from strategy status/instrument keys.
    ce_now = status.get("ce_now")
    pe_now = status.get("pe_now")
    if ce_now is None or pe_now is None:
        try:
            mkt = getattr(runner.strategy, "_mkt", None) if runner and hasattr(runner, "strategy") else None
            ce_ikey = status.get("ce_ikey")
            pe_ikey = status.get("pe_ikey")
            if mkt and ce_ikey and pe_ikey:
                if ce_now is None:
                    ce_now = mkt.fetch_ltp(ce_ikey)
                if pe_now is None:
                    pe_now = mkt.fetch_ltp(pe_ikey)
        except Exception as exc:
            logger.warning(f"Live LTP fetch failed in prop desk tile: {exc}")

    # Final fallback: synthetic pricing from Black-76.
    if ce_now is None or pe_now is None:
        try:
            ce_now = Black76Engine.calculate_price(spot, K, tte, r, iv, "CE")
            pe_now = Black76Engine.calculate_price(spot, K, tte, r, iv, "PE")
        except Exception as exc:
            logger.warning(f"Black76 error: {exc}")
            return base

    try:
        ce_now = float(ce_now)
        pe_now = float(pe_now)
        ce_g   = Black76Engine.calculate_greeks(spot, K, tte, r, iv, "CE")
        pe_g   = Black76Engine.calculate_greeks(spot, K, tte, r, iv, "PE")
    except Exception as exc:
        logger.warning(f"Greeks calc error: {exc}")
        return base

    qty          = lots * lot_sz
    net_delta    = -qty * (ce_g.delta + pe_g.delta)
    net_theta    = -qty * (ce_g.theta + pe_g.theta)
    net_vega     = -qty * (ce_g.vega  + pe_g.vega)
    net_gamma    = -qty * (ce_g.gamma + pe_g.gamma)

    total_entry   = ce_ent + pe_ent
    total_current = ce_now + pe_now
    pnl_gross     = (total_entry - total_current) * lot_sz * lots
    pnl_pct       = (total_entry - total_current) / total_entry if total_entry else 0.0
    dte           = max((expiry - date.today()).days, 0)

    base.update({
        "lots": lots, "lot_size": lot_sz,
        "ce_strike": K, "pe_strike": (live_pe_strike if live_pe_strike is not None else K),
        "ce_entry": ce_ent, "pe_entry": pe_ent, "total_premium_entry": total_entry,
        "ce_current": round(ce_now, 2), "pe_current": round(pe_now, 2),
        "total_premium_current": round(total_current, 2),
        "net_delta":  round(net_delta,  2),
        "net_theta":  round(net_theta,  2),
        "net_vega":   round(net_vega,   2),
        "net_gamma":  round(net_gamma,  4),
        "delta_notional": round(net_delta * spot, 0),
        "pnl_gross_rs": round(pnl_gross, 0),
        "pnl_pct":      round(pnl_pct, 4),
        "dte": dte, "expiry": str(expiry),
        "current_spot": spot, "ttm": round(tte, 6), "iv": round(iv, 4),
        "profit_target_pct": 0.50, "stop_loss_mult": 2.0, "exit_time": "15:15",
        "delta_adj_threshold": 0.55,
    })
    return base


def _inactive_tile(id_: str, name: str, underlying: str) -> dict:
    return {"id": id_, "name": name, "underlying": underlying, "state": "INACTIVE"}


# ── Pages ─────────────────────────────────────────────────────────────────────

@propdeskmode_bp.route("/")
@login_required
def index():
    return render_template("propdeskmode/index.html")


# ── API: portfolio ────────────────────────────────────────────────────────────

@propdeskmode_bp.route("/api/portfolio")
@login_required
def api_portfolio():
    db       = _db()
    runner   = _runner()
    now_ist  = _ist_now()
    corr     = _correlation_cached()

    nifty_tile    = _build_nifty_tile(runner, db, now_ist)
    banknifty_tile = _inactive_tile("banknifty", "BANKNIFTY", "NSE_INDEX|Nifty Bank")
    finnifty_tile  = _inactive_tile("finnifty",  "FINNIFTY",  "NSE_INDEX|Nifty Fin Service")

    underlyings = [nifty_tile, banknifty_tile, finnifty_tile]

    # Global Greeks — sum across positioned underlyings
    total_delta = sum(u.get("net_delta", 0) for u in underlyings if u.get("state") == "POSITIONED")
    total_theta = sum(u.get("net_theta", 0) for u in underlyings if u.get("state") == "POSITIONED")
    total_vega  = sum(u.get("net_vega",  0) for u in underlyings if u.get("state") == "POSITIONED")
    total_gamma = sum(u.get("net_gamma", 0) for u in underlyings if u.get("state") == "POSITIONED")

    positioned = [u for u in underlyings if u.get("state") == "POSITIONED"]
    avg_spot = (
        sum(u.get("current_spot", 0) for u in positioned) / len(positioned)
        if positioned else 0.0
    )

    total_pnl = _get_today_pnl(db)
    dte_now   = min((u.get("dte", 99) for u in positioned), default=99)

    payload = {
        "global": {
            "total_delta":    round(total_delta, 2),
            "total_theta":    round(total_theta, 2),
            "total_vega":     round(total_vega,  2),
            "total_gamma":    round(total_gamma, 4),
            "total_pnl_today": round(total_pnl, 0),
            "stress_1pct":    round(total_delta * avg_spot * 0.01, 0),
            "stress_2pct":    round(total_delta * avg_spot * 0.02, 0),
            "clustered_risk": _clustered_risk(underlyings, corr.get("nf_bn"), dte_now),
        },
        "underlyings": underlyings,
        "timestamp":   now_ist.isoformat(),
    }
    return jsonify(_serialise(payload))


# ── API: correlation ──────────────────────────────────────────────────────────

@propdeskmode_bp.route("/api/correlation")
@login_required
def api_correlation():
    result = _correlation_cached()
    return jsonify(result)


# ── API: kill-switch ──────────────────────────────────────────────────────────

@propdeskmode_bp.route("/api/kill-switch", methods=["POST"])
@login_required
def api_kill_switch():
    data = request.get_json() or {}
    if not data.get("confirm"):
        return jsonify({"success": False, "error": "confirm required"}), 400

    runner = _runner()
    if not runner or not hasattr(runner, "strategy"):
        return jsonify({"success": False, "error": "no runner"}), 503

    spot   = _get_live_spot(_db(), "NSE_INDEX|Nifty 50")
    result = runner.strategy.emergency_close(current_spot=spot)
    return jsonify({"success": True, **result})
