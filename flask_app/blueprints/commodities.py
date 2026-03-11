import json

from flask import Blueprint, Response, current_app, render_template, request

from app_facade.commodities_facade import CommoditiesFacade

commodities_bp = Blueprint("commodities", __name__, url_prefix="/commodities")


def _as_json(payload, status=200):
    return Response(json.dumps(payload, sort_keys=False), status=status, mimetype="application/json")


def _get_facade() -> CommoditiesFacade:
    """Return singleton facade instance attached to Flask app."""
    facade = getattr(current_app, "commodities_facade", None)
    if facade is None:
        execution_handler = getattr(current_app, "execution_handler", None)
        facade = CommoditiesFacade(
            db_manager=getattr(current_app, "db_manager", None),
            execution_handler=execution_handler,
        )
        current_app.commodities_facade = facade
    return facade


@commodities_bp.route("/")
def index():
    """Main commodities trading dashboard page."""
    return render_template("commodities/index.html")


@commodities_bp.route("/api/state")
def api_state():
    """Returns current commodity dashboard state with strategy snapshot and audit metadata."""
    facade = _get_facade()
    return _as_json(facade.get_state())


@commodities_bp.route("/api/strategy-snapshot")
def api_strategy_snapshot():
    """Returns deterministic latest strategy snapshot payload."""
    facade = _get_facade()
    return _as_json({"strategy_snapshot": facade.get_strategy_snapshot()})


@commodities_bp.route("/api/metrics")
def api_metrics():
    """Returns deterministic metrics payload."""
    facade = _get_facade()
    return _as_json(facade.get_strategy_metrics())


@commodities_bp.route("/api/usdinr-attribution")
def api_usdinr_attribution():
    """Returns with/without USDINR filter attribution for two run IDs."""
    run_id_without = request.args.get("run_id_without", "")
    run_id_with = request.args.get("run_id_with", "")
    if not run_id_without or not run_id_with:
        return _as_json(
            {
                "error": "run_id_without and run_id_with are required",
                "strategy_without_usdinr_filter": {},
                "strategy_with_usdinr_filter": {},
                "win_rate_difference": 0.0,
                "expectancy_difference": 0.0,
                "drawdown_difference": 0.0,
            },
            status=400,
        )

    facade = _get_facade()
    try:
        return _as_json(facade.get_usdinr_attribution(run_id_without, run_id_with))
    except Exception as exc:
        return _as_json(
            {
                "error": str(exc),
                "strategy_without_usdinr_filter": {},
                "strategy_with_usdinr_filter": {},
                "win_rate_difference": 0.0,
                "expectancy_difference": 0.0,
                "drawdown_difference": 0.0,
            },
            status=500,
        )
