from flask import Blueprint, render_template, jsonify
from flask_app.middleware import login_required, role_required
from core.data.duckdb_client import db_cursor

database_bp = Blueprint('database', __name__, url_prefix='/database')

@database_bp.route('/')
@login_required
@role_required('admin')
def index():
    """Database management page."""
    return render_template('database/index.html')

@database_bp.route('/api/tables')
@login_required
@role_required('admin')
def get_tables():
    """Lists all tables in the database."""
    with db_cursor(read_only=True) as conn:
        tables = conn.execute("SHOW TABLES").fetchall()
        return jsonify({"success": True, "tables": [t[0] for t in tables]})
