# flask_app/blueprints/dashboard/routes.py
"""Dashboard Routes"""

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import render_template, jsonify
from . import bp
from datetime import datetime, time as dt_time
import logging

logger = logging.getLogger(__name__)


def is_market_hours():
    """Check if current time is within market hours"""
    now = datetime.now().time()
    market_open = dt_time(9, 15)
    market_close = dt_time(15, 30)
    return market_open <= now <= market_close


def get_dashboard_stats():
    """Get statistics for dashboard"""
    stats = {
        'market_status': 'Open' if is_market_hours() else 'Closed',
        'active_signals': 0,
        'todays_trades': 0,
        'instruments_tracked': 0,
        'websocket_status': 'Disconnected',
        'last_data_update': None
    }

    try:
        from core.database import get_db
        db = get_db()

        if db:
            # Count active signals (case-insensitive)
            result = db.safe_query(
                "SELECT COUNT(*) FROM unified_signals WHERE UPPER(status) = 'ACTIVE'",
                fetch='one'
            )
            stats['active_signals'] = result[0] if result else 0

            # Count instruments
            result = db.safe_query(
                "SELECT COUNT(*) FROM fo_stocks_master WHERE is_active = TRUE",
                fetch='one'
            )
            stats['instruments_tracked'] = result[0] if result else 0

            # Get last data update
            result = db.safe_query(
                "SELECT MAX(timestamp) FROM live_ohlcv_cache",
                fetch='one'
            )
            if result and result[0]:
                stats['last_data_update'] = result[0]

    except Exception as e:
        logger.error(f"Error getting dashboard stats: {e}")

    # Check WebSocket status
    try:
        from core.websocket import WebSocketManager
        ws = WebSocketManager.get_instance()
        stats['websocket_status'] = 'Connected' if ws.is_connected else 'Disconnected'
    except:
        pass

    return stats


@bp.route('/')
def index():
    """Dashboard home page"""
    stats = get_dashboard_stats()
    return render_template('dashboard/index.html', stats=stats)


@bp.route('/stats')
def stats():
    """Get dashboard stats (JSON for AJAX refresh)"""
    return jsonify(get_dashboard_stats())
