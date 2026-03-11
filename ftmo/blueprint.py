"""Flask blueprint for FTMO Challenge dashboard."""

import threading
import logging
from pathlib import Path

from flask import Blueprint, render_template, jsonify, request

from ftmo import db
from ftmo.ingest import import_csv
from ftmo.engine import FTMOBacktestEngine
from ftmo.simulation import FTMOSimulator
from ftmo.analytics import compute_trade_analytics, compute_equity_curve, compute_daily_summary

logger = logging.getLogger(__name__)

ftmo_bp = Blueprint(
    "ftmo", __name__,
    url_prefix="/ftmo",
    template_folder="../flask_app/templates/ftmo",
)

# Module-level state for background tasks
_state = {
    "df_m5": None,           # Imported DataFrame
    "backtest_status": "idle",  # idle | running | completed | failed
    "backtest_error": None,
    "sim_status": "idle",
    "sim_error": None,
}


@ftmo_bp.route("/")
def index():
    return render_template("ftmo/index.html")


# ── Data Import ─────────────────────────────────────────────────────

@ftmo_bp.route("/api/import", methods=["POST"])
def api_import():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"success": False, "error": "Empty filename"}), 400

    source_tz = request.form.get("source_tz", "UTC")

    # Save temp file
    tmp_path = Path("ftmo") / "tmp_upload.csv"
    f.save(str(tmp_path))

    try:
        df = import_csv(tmp_path, source_tz=source_tz)
        _state["df_m5"] = df

        # Init DB
        db.init_db()

        return jsonify({
            "success": True,
            "bars_loaded": len(df),
            "date_range": {
                "start": str(df["timestamp"].iloc[0]),
                "end": str(df["timestamp"].iloc[-1]),
            },
        })
    except Exception as e:
        logger.exception("CSV import failed")
        return jsonify({"success": False, "error": str(e)}), 400
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# ── Backtest ────────────────────────────────────────────────────────

@ftmo_bp.route("/api/backtest", methods=["POST"])
def api_backtest():
    if _state["df_m5"] is None:
        return jsonify({"success": False, "error": "No data imported yet"}), 400

    if _state["backtest_status"] == "running":
        return jsonify({"success": False, "error": "Backtest already running"}), 400

    body = request.get_json(silent=True) or {}
    start_date = body.get("start_date")
    end_date = body.get("end_date")

    def _run():
        try:
            _state["backtest_status"] = "running"
            _state["backtest_error"] = None

            engine = FTMOBacktestEngine(_state["df_m5"])
            result = engine.run(start_date=start_date, end_date=end_date)

            # Persist to DB
            db.init_db()
            db.clear_tables()
            db.insert_trades([t.to_dict() for t in result.trades])
            db.insert_daily_stats([{
                "session_date": d.session_date,
                "starting_equity": d.starting_equity,
                "ending_equity": d.ending_equity,
                "daily_pnl": d.daily_pnl,
                "daily_pnl_pct": d.daily_pnl_pct,
                "trades_taken": d.trades_taken,
                "wins": d.wins,
                "losses": d.losses,
                "max_equity": d.max_equity,
                "daily_drawdown": d.daily_drawdown,
                "overall_drawdown": d.overall_drawdown,
                "risk_status": d.risk_status,
                "consecutive_losses": d.consecutive_losses,
            } for d in result.daily_stats])

            _state["backtest_status"] = "completed"
            logger.info(f"Backtest done: {len(result.trades)} trades, PnL=${result.total_pnl:.2f}")
        except Exception as e:
            _state["backtest_status"] = "failed"
            _state["backtest_error"] = str(e)
            logger.exception("Backtest failed")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"success": True, "status": "started"})


@ftmo_bp.route("/api/backtest/status")
def api_backtest_status():
    return jsonify({
        "success": True,
        "status": _state["backtest_status"],
        "error": _state["backtest_error"],
    })


# ── Trades & Stats ──────────────────────────────────────────────────

@ftmo_bp.route("/api/trades")
def api_trades():
    trades = db.get_all_trades()
    return jsonify({"success": True, "trades": trades})


@ftmo_bp.route("/api/daily-stats")
def api_daily_stats():
    stats = db.get_daily_stats()
    return jsonify({"success": True, "daily_stats": stats})


@ftmo_bp.route("/api/equity-curve")
def api_equity_curve():
    from ftmo.engine import TradeRecord
    trades_raw = db.get_all_trades()
    if not trades_raw:
        return jsonify({"success": True, "data": []})

    # Build minimal TradeRecord objects for equity curve
    trades = []
    for t in trades_raw:
        trades.append(type("T", (), {
            "pnl_dollar": t["pnl_dollar"],
            "timestamp_exit": t["timestamp_exit"],
        })())

    curve = compute_equity_curve(trades)
    return jsonify({"success": True, "data": curve})


@ftmo_bp.route("/api/analytics")
def api_analytics():
    from ftmo.engine import TradeRecord
    trades_raw = db.get_all_trades()
    if not trades_raw:
        return jsonify({"success": True, "analytics": {"total_trades": 0}})

    trades = []
    for t in trades_raw:
        trades.append(type("T", (), {
            "pnl_r": t["pnl_r"],
            "pnl_dollar": t["pnl_dollar"],
            "exit_reason": t["exit_reason"],
        })())

    analytics = compute_trade_analytics(trades)
    return jsonify({"success": True, "analytics": analytics})


# ── Simulation ──────────────────────────────────────────────────────

@ftmo_bp.route("/api/simulate", methods=["POST"])
def api_simulate():
    if _state["sim_status"] == "running":
        return jsonify({"success": False, "error": "Simulation already running"}), 400

    body = request.get_json(silent=True) or {}
    window = body.get("window_days", 30)
    step = body.get("step_days", 1)

    def _run():
        try:
            _state["sim_status"] = "running"
            _state["sim_error"] = None

            from ftmo.engine import TradeRecord
            trades_raw = db.get_all_trades()
            daily_stats = db.get_daily_stats()
            if not daily_stats:
                _state["sim_status"] = "failed"
                _state["sim_error"] = "No data - run backtest first"
                return

            all_dates = [d["session_date"] for d in daily_stats]
            trades = []
            for t in trades_raw:
                trades.append(type("TR", (), {
                    "session_date": t["session_date"],
                    "pnl_r": t["pnl_r"],
                    "pnl_dollar": t["pnl_dollar"],
                    "risk_amount": t["risk_amount"],
                    "timestamp_entry": t["timestamp_entry"],
                })())

            sim = FTMOSimulator(trades, all_dates=all_dates)
            results = sim.run_rolling(window_days=window, step_days=step)

            db.insert_simulations([r.to_dict() for r in results])

            _state["sim_status"] = "completed"
            logger.info(f"Simulation done: {len(results)} windows")
        except Exception as e:
            _state["sim_status"] = "failed"
            _state["sim_error"] = str(e)
            logger.exception("Simulation failed")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"success": True, "status": "started"})


@ftmo_bp.route("/api/simulate/status")
def api_simulate_status():
    return jsonify({
        "success": True,
        "status": _state["sim_status"],
        "error": _state["sim_error"],
    })


@ftmo_bp.route("/api/simulations")
def api_simulations():
    sims = db.get_simulations()
    return jsonify({"success": True, "simulations": sims})


@ftmo_bp.route("/api/simulation-summary")
def api_simulation_summary():
    sims = db.get_simulations()
    if not sims:
        return jsonify({"success": True, "summary": {}})

    from ftmo.simulation import SimulationResult
    results = []
    for s in sims:
        results.append(SimulationResult(**{k: s[k] for k in SimulationResult.__dataclass_fields__}))

    summary = FTMOSimulator.get_aggregate_stats(results)
    return jsonify({"success": True, "summary": summary})


# ── Dashboard ───────────────────────────────────────────────────────

@ftmo_bp.route("/api/dashboard")
def api_dashboard():
    stats = db.get_daily_stats()
    trades = db.get_all_trades()

    if not stats:
        return jsonify({"success": True, "state": None})

    last = stats[-1]
    total_pnl = sum(s.get("daily_pnl", 0) for s in stats)
    total_trades = sum(s.get("trades_taken", 0) for s in stats)

    return jsonify({
        "success": True,
        "state": {
            "equity": last.get("ending_equity", 50000),
            "starting_balance": 50000,
            "daily_pnl": last.get("daily_pnl", 0),
            "total_pnl": round(total_pnl, 2),
            "total_trades": total_trades,
            "consecutive_losses": last.get("consecutive_losses", 0),
            "risk_status": last.get("risk_status", "GREEN"),
            "pct_to_target": round(total_pnl / 5000 * 100, 1),
            "pct_to_daily_limit": round(abs(min(last.get("daily_pnl", 0), 0)) / 2500 * 100, 1),
            "pct_to_overall_limit": round(abs(min(total_pnl, 0)) / 5000 * 100, 1),
            "max_drawdown": max(s.get("overall_drawdown", 0) for s in stats),
        },
    })
