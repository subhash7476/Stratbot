"""
Options Structural Engine Blueprint
-----------------------------------
Serves the Options Structural Engine dashboard.

Endpoints
---------
GET  /options/                    render index page
GET  /options/api/summary         summary cards data (PCR, GEX, levels)
GET  /options/api/chain           option chain table data
GET  /options/api/gex             gamma exposure distribution
GET  /options/api/oi              OI distribution
GET  /options/api/expiries        available expiry dates
GET  /options/api/structural      complete structural snapshot
"""

from flask import Blueprint, render_template, jsonify, request, current_app
from pathlib import Path
from datetime import datetime, date
import logging

from core.logging import setup_logger
from flask_app.middleware import login_required

logger = setup_logger("options_bp")

options_bp = Blueprint(
    "options",
    __name__,
    url_prefix="/options",
)


# -- Helpers -----------------------------------------------------------------

def _get_facade():
    """Get OptionsFacade instance."""
    from app_facade.options_facade import OptionsFacade
    db_manager = getattr(current_app, "db_manager", None)
    return OptionsFacade(db_manager=db_manager)


def _row_to_dict(cursor, row: tuple) -> dict:
    """Convert SQLite row to dict."""
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def _serialise(obj):
    """Make object JSON-serializable."""
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

@options_bp.route("/")
@login_required
def index():
    """Main options structural engine dashboard page."""
    return render_template("options/index.html")


# -- API Endpoints -----------------------------------------------------------

@options_bp.route("/api/summary")
@login_required
def api_summary():
    """
    Get summary cards data.
    
    Query Params:
    - index: NIFTY or BANKNIFTY (default: NIFTY)
    - expiry: YYYY-MM-DD (default: nearest weekly)
    
    Returns:
    {
        "underlying_ltp": 22450.30,
        "atm_strike": 22450,
        "pcr": 0.95,
        "pcr_sentiment": "Neutral",
        "pcr_change": 0.02,
        "net_gamma": 125000000,
        "gex_regime": "Positive GEX (Stable)",
        "zero_gamma_level": 22400,
        "resistance_strike": 22500,
        "support_strike": 22400,
        "total_strikes": 42,
        "last_update": "2026-03-03T14:30:00"
    }
    """
    index = request.args.get("index", "NIFTY").upper()
    expiry = request.args.get("expiry", None)
    
    try:
        facade = _get_facade()
        summary = facade.get_summary(index, expiry)
        return jsonify(_serialise(summary))
    except Exception as e:
        logger.error(f"Error in api_summary: {e}")
        return jsonify({"error": str(e)}), 500


@options_bp.route("/api/chain")
@login_required
def api_chain():
    """
    Get option chain table data.
    
    Query Params:
    - index: NIFTY or BANKNIFTY
    - expiry: YYYY-MM-DD
    - range: Number of strikes around ATM (default: all)
    
    Returns:
    [
        {
            "strike": 22400,
            "ce_oi": 45100,
            "ce_oi_change": 8500,
            "ce_ltp": 185.50,
            "ce_iv": 0.142,
            "ce_delta": 0.52,
            "ce_gamma": 0.0012,
            "pe_oi": 52300,
            "pe_oi_change": 3200,
            "pe_ltp": 142.30,
            "pe_iv": 0.140,
            "pe_delta": -0.48,
            "pe_gamma": 0.0012,
            "is_atm": false
        },
        ...
    ]
    """
    index = request.args.get("index", "NIFTY").upper()
    expiry = request.args.get("expiry", None)
    strikes_range = request.args.get("range", None, type=int)
    
    try:
        facade = _get_facade()
        chain = facade.get_option_chain(index, expiry, strikes_range)
        return jsonify(_serialise(chain))
    except Exception as e:
        logger.error(f"Error in api_chain: {e}")
        return jsonify({"error": str(e)}), 500


@options_bp.route("/api/gex")
@login_required
def api_gex():
    """
    Get Gamma Exposure distribution.
    
    Query Params:
    - index: NIFTY or BANKNIFTY
    - expiry: YYYY-MM-DD
    
    Returns:
    [
        {
            "strike": 22400,
            "option_type": "CE",
            "gamma": 0.0012,
            "oi": 45100,
            "gamma_exposure": 4063500
        },
        ...
    ]
    """
    index = request.args.get("index", "NIFTY").upper()
    expiry = request.args.get("expiry", None)
    
    try:
        facade = _get_facade()
        gex_data = facade.get_gex_distribution(index, expiry)
        return jsonify(_serialise(gex_data))
    except Exception as e:
        logger.error(f"Error in api_gex: {e}")
        return jsonify({"error": str(e)}), 500


@options_bp.route("/api/oi")
@login_required
def api_oi():
    """
    Get OI distribution for charting.
    
    Query Params:
    - index: NIFTY or BANKNIFTY
    - expiry: YYYY-MM-DD
    
    Returns:
    {
        "ce_by_strike": {22400: 45100, 22500: 25300, ...},
        "pe_by_strike": {22400: 52300, 22500: 15400, ...},
        "highest_ce": [[22400, 45100], [22500, 25300], ...],
        "highest_pe": [[22400, 52300], [22500, 15400], ...]
    }
    """
    index = request.args.get("index", "NIFTY").upper()
    expiry = request.args.get("expiry", None)
    
    try:
        facade = _get_facade()
        oi_data = facade.get_oi_distribution(index, expiry)
        return jsonify(_serialise(oi_data))
    except Exception as e:
        logger.error(f"Error in api_oi: {e}")
        return jsonify({"error": str(e)}), 500


@options_bp.route("/api/expiries")
@login_required
def api_expiries():
    """
    Get available weekly expiry dates from instrument master.
    
    Query Params:
    - index: NIFTY or BANKNIFTY
    - count: Number of expiries (default: 4)
    
    Returns:
    ["2026-03-04", "2026-03-11", "2026-03-18", "2026-03-25"]
    """
    index = request.args.get("index", "NIFTY").upper()
    count = request.args.get("count", 4, type=int)
    
    try:
        facade = _get_facade()
        # Use instrument master if available
        from core.data.options_provider import OptionsProvider
        provider = OptionsProvider(read_only=True)
        expiries = provider.get_expiry_list(index, count)
        return jsonify({"expiries": expiries})
    except Exception as e:
        logger.error(f"Error in api_expiries: {e}")
        return jsonify({"error": str(e)}), 500


@options_bp.route("/api/structural")
@login_required
def api_structural():
    """
    Get complete structural snapshot.
    
    Query Params:
    - index: NIFTY or BANKNIFTY
    - expiry: YYYY-MM-DD
    
    Returns:
    Complete structural data including PCR, GEX, OI analysis, max pain
    """
    index = request.args.get("index", "NIFTY").upper()
    expiry = request.args.get("expiry", None)
    
    try:
        facade = _get_facade()
        structural = facade.get_structural_data(index, expiry)
        structural_dict = facade.to_dict(structural)
        return jsonify(_serialise(structural_dict))
    except Exception as e:
        logger.error(f"Error in api_structural: {e}")
        return jsonify({"error": str(e)}), 500


@options_bp.route("/api/refresh")
@login_required
def api_refresh():
    """
    Force refresh data from Upstox API.
    
    Query Params:
    - index: NIFTY or BANKNIFTY
    - expiry: YYYY-MM-DD
    
    Returns:
    Summary data after refresh
    """
    index = request.args.get("index", "NIFTY").upper()
    expiry = request.args.get("expiry", None)
    
    try:
        facade = _get_facade()
        summary = facade.get_summary(index, expiry)
        return jsonify(_serialise(summary))
    except Exception as e:
        logger.error(f"Error in api_refresh: {e}")
        return jsonify({"error": str(e)}), 500
