from flask import Blueprint, render_template, jsonify, request, session, current_app
from pathlib import Path
from flask_app.middleware import login_required
from app_facade.scanner_facade import ScannerFacade
from core.database.manager import DatabaseManager
from dataclasses import asdict
from datetime import datetime

scanner_bp = Blueprint('scanner', __name__)

def get_facade():
    db_manager = getattr(current_app, 'db_manager', None)
    if not db_manager:
        db_manager = DatabaseManager(Path("data"))
    return ScannerFacade(db_manager)

@scanner_bp.route('/')
@login_required
def index():
    """Live scanner page with dual-panel layout."""
    return render_template('scanner/index.html')

@scanner_bp.route('/api/watchlist')
@login_required
def get_watchlist():
    """Returns watchlist snapshot for left panel."""
    try:
        rows = get_facade().get_watchlist_snapshot()
        return jsonify({
            "success": True,
            "data": [asdict(r) for r in rows],
            "count": len(rows),
            "updated_at": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@scanner_bp.route('/api/scanner-state')
@login_required
def get_scanner_state():
    """Returns live scanner state for right panel."""
    try:
        rows = get_facade().get_live_scanner_state()
        return jsonify({
            "success": True,
            "data": [asdict(r) for r in rows],
            "count": len(rows),
            "updated_at": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@scanner_bp.route('/api/symbol-context/<symbol>')
@login_required
def get_symbol_context(symbol: str):
    """Returns full context for a symbol (bottom drawer)."""
    try:
        context = get_facade().get_symbol_context(symbol)
        if context is None:
            return jsonify({"success": False, "error": "Symbol not found"}), 404
        return jsonify({
            "success": True,
            "data": asdict(context)
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@scanner_bp.route('/api/filter-options')
@login_required
def get_filter_options():
    """Returns unique values for filtering dropdowns."""
    try:
        options = get_facade().get_filter_options()
        return jsonify({"success": True, "data": options})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@scanner_bp.route('/api/instruments/filtered')
@login_required
def get_filtered_instruments():
    """Returns instruments filtered by exchange, type, and search."""
    try:
        filters = {
            'exchange': request.args.get('exchange'),
            'market_type': request.args.get('market_type'),
            'search': request.args.get('search')
        }
        instruments = get_facade().get_filtered_instruments(filters)
        return jsonify({
            "success": True, 
            "data": instruments,
            "count": len(instruments)
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@scanner_bp.route('/api/fo-stocks')
@login_required
def get_fo_stocks():
    """Returns list of F&O stocks."""
    try:
        stocks = get_facade().get_fo_stocks()
        return jsonify({"success": True, "data": stocks, "count": len(stocks)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@scanner_bp.route('/api/watchlist/add-bulk', methods=['POST'])
@login_required
def add_bulk_to_watchlist():
    """Add multiple instruments to watchlist."""
    try:
        data = request.get_json()
        instruments = data.get('instruments', [])
        if not instruments:
            return jsonify({"success": False, "error": "No instruments provided"}), 400
            
        username = session.get('username', 'default')
        success = get_facade().add_bulk_to_watchlist(username, instruments)
        
        if success:
            return jsonify({"success": True, "message": f"Added {len(instruments)} instruments to watchlist"})
        else:
            return jsonify({"success": False, "error": "Failed to add bulk instruments"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@scanner_bp.route('/api/watchlist/add', methods=['POST'])
@login_required
def add_to_watchlist():
    """Add an instrument to user's watchlist."""
    try:
        data = request.get_json()
        instrument_key = data.get('instrument_key')
        trading_symbol = data.get('trading_symbol', '')
        exchange = data.get('exchange', 'NSE')
        market_type = data.get('market_type', 'EQ')

        if not instrument_key:
            return jsonify({"success": False, "error": "instrument_key required"}), 400

        username = session.get('username', 'default')
        db_manager = getattr(current_app, 'db_manager', None) or DatabaseManager(Path("data"))

        with db_manager.config_writer() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO user_watchlist
                (username, instrument_key, trading_symbol, exchange, market_type)
                VALUES (?, ?, ?, ?, ?)
            """, [username, instrument_key, trading_symbol, exchange, market_type])

        return jsonify({"success": True, "message": f"Added {trading_symbol} to watchlist"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@scanner_bp.route('/api/watchlist/remove', methods=['POST'])
@login_required
def remove_from_watchlist():
    """Remove an instrument from user's watchlist."""
    try:
        data = request.get_json()
        instrument_key = data.get('instrument_key')

        if not instrument_key:
            return jsonify({"success": False, "error": "instrument_key required"}), 400

        username = session.get('username', 'default')
        db_manager = getattr(current_app, 'db_manager', None) or DatabaseManager(Path("data"))

        with db_manager.config_writer() as conn:
            conn.execute("""
                DELETE FROM user_watchlist
                WHERE username = ? AND instrument_key = ?
            """, [username, instrument_key])

        return jsonify({"success": True, "message": "Removed from watchlist"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@scanner_bp.route('/api/watchlist/user')
@login_required
def get_user_watchlist():
    """Returns user's custom watchlist with live prices."""
    try:
        username = session.get('username', 'default')
        rows = get_facade().get_user_watchlist(username)
        return jsonify({
            "success": True,
            "data": [asdict(r) for r in rows],
            "count": len(rows),
            "updated_at": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@scanner_bp.route('/api/health', methods=['GET'])
@login_required
def health_check():
    """System health check for debugging."""
    from core.database.manager import DatabaseManager
    db_manager = getattr(current_app, 'db_manager', None) or DatabaseManager(Path("data"))

    health = {
        "runner_state_count": 0,
        "signals_count": 0,
        "live_buffer_accessible": False,
        "last_runner_update": None
    }

    try:
        with db_manager.config_reader() as conn:
            result = conn.execute("SELECT COUNT(*) FROM runner_state").fetchone()
            health["runner_state_count"] = result[0]
            
            # Check if updated_at exists, handle potential NULL
            result = conn.execute("SELECT MAX(updated_at) FROM runner_state").fetchone()
            if result and result[0]:
                health["last_runner_update"] = result[0]
    except Exception as e:
        health["runner_state_error"] = str(e)

    try:
        with db_manager.signals_reader() as conn:
            result = conn.execute("SELECT COUNT(*) FROM signals WHERE created_at > datetime('now', '-1 day')").fetchone()
            health["signals_count"] = result[0]
    except Exception as e:
        health["signals_error"] = str(e)

    try:
        with db_manager.live_buffer_reader() as conns:
            health["live_buffer_accessible"] = True
    except Exception as e:
        health["live_buffer_error"] = str(e)

    return jsonify(health)
