from flask import Blueprint, render_template, jsonify, request
from flask_app.middleware import login_required
from core.data.duckdb_client import db_cursor

backtest_bp = Blueprint('backtest', __name__)

@backtest_bp.route('/')
@login_required
def index():
    """Main backtest page."""
    return render_template('backtest/index.html')

@backtest_bp.route('/api/runs')
@login_required
def get_runs():
    """Returns list of past backtest runs."""
    with db_cursor(read_only=True) as conn:
        runs = conn.execute("SELECT * FROM backtest_runs ORDER BY created_at DESC").fetchdf()
        return jsonify({"success": True, "runs": runs.to_dict(orient='records')})
