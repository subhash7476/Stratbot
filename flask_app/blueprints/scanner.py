from flask import Blueprint, render_template, jsonify
from flask_app.middleware import login_required
from core.data.duckdb_client import db_cursor

scanner_bp = Blueprint('scanner', __name__)

@scanner_bp.route('/')
@login_required
def index():
    """Live scanner page."""
    return render_template('scanner/index.html')

@scanner_bp.route('/api/results')
@login_required
def get_results():
    """Returns latest scanner results."""
    # Placeholder for live scanning logic
    return jsonify({"success": True, "results": []})
