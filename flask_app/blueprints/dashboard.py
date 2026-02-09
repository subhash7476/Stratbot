import os
from datetime import datetime
from flask import Blueprint, render_template, jsonify, redirect, url_for, flash

from flask_app.middleware import login_required, role_required, read_only
from core.auth.credentials import credentials

# Create blueprint with explicit template folder
template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')
dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard', template_folder=template_dir)


@dashboard_bp.route('/')
@login_required
def index():
    """Main dashboard page - requires login."""
    # Check for daily Upstox token refresh
    if credentials.needs_daily_refresh:
        flash("Upstox session expired or missing. Redirecting to login...", "warning")
        return redirect(url_for('ops.upstox_login'))
        
    return render_template('dashboard.html')


@dashboard_bp.route('/api/stats')
@login_required
@read_only
def stats():
    """
    API endpoint for dashboard statistics.
    """
    from flask import current_app
    from core.database.manager import DatabaseManager
    from pathlib import Path
    
    db = getattr(current_app, 'db_manager', None) or DatabaseManager(Path("data"))
    
    active_strategies = 0
    trades_today = 0
    
    try:
        with db.config_reader() as conn:
            res = conn.execute("SELECT count(distinct strategy_id) FROM runner_state WHERE status = 'RUNNING'").fetchone()
            active_strategies = res[0] if res else 0
            
        with db.trading_reader() as conn:
            # Simple count for now, in production filter by date
            res = conn.execute("SELECT count(*) FROM trades").fetchone()
            trades_today = res[0] if res else 0
    except:
        pass

    return jsonify({
        'active_strategies': active_strategies,
        'trades_today': trades_today,
        'portfolio_value': 0.0,
        'last_updated': datetime.now().isoformat()
    })



@dashboard_bp.route('/admin')
@login_required
@role_required('admin')
def admin():
    """Admin page - requires admin role."""
    return render_template('admin.html')
