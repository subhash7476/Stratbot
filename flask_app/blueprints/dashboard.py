import os
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
    Read-only: never modifies data, only fetches from persistence layer.
    """
    return jsonify({
        'active_strategies': 0,
        'trades_today': 0,
        'portfolio_value': 0.0,
        'last_updated': None
    })


@dashboard_bp.route('/admin')
@login_required
@role_required('admin')
def admin():
    """Admin page - requires admin role."""
    return render_template('admin.html')
