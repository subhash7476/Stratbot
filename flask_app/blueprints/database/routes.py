# flask_app/blueprints/database/routes.py
"""
Database Viewer Routes
======================

Handles:
- Browsing database tables
- Running custom queries
- Viewing table schemas
- Data export
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import render_template, jsonify, request, Response
from . import bp
import logging
import json

logger = logging.getLogger(__name__)


def get_db():
    """Get database connection"""
    try:
        from core.database import get_db as get_trading_db
        return get_trading_db()
    except Exception as e:
        logger.error(f"Database error: {e}")
        return None


@bp.route('/')
def index():
    """Database viewer page"""
    tables = get_tables()
    return render_template('database/index.html', tables=tables)


def get_tables():
    """Get list of database tables"""
    db = get_db()
    if not db:
        return []

    try:
        rows = db.con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
        return [row[0] for row in rows]
    except Exception as e:
        logger.error(f"Error getting tables: {e}")
        return []


@bp.route('/tables')
def tables():
    """Get tables list (JSON)"""
    return jsonify({'success': True, 'tables': get_tables()})


@bp.route('/schema/<table_name>')
def schema(table_name):
    """Get table schema"""
    db = get_db()
    if not db:
        return jsonify({'success': False, 'error': 'Database not available'})

    try:
        # Validate table name (prevent SQL injection)
        tables = get_tables()
        if table_name not in tables:
            return jsonify({'success': False, 'error': 'Table not found'}), 404

        rows = db.con.execute(f"DESCRIBE {table_name}").fetchall()
        columns = []
        for row in rows:
            columns.append({
                'name': row[0],
                'type': row[1],
                'nullable': row[2] == 'YES',
                'key': row[3],
                'default': row[4]
            })

        return jsonify({'success': True, 'table': table_name, 'columns': columns})

    except Exception as e:
        logger.error(f"Error getting schema for {table_name}: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/browse/<table_name>')
def browse(table_name):
    """Browse table data"""
    db = get_db()
    if not db:
        return jsonify({'success': False, 'error': 'Database not available'})

    try:
        # Validate table name
        tables = get_tables()
        if table_name not in tables:
            return jsonify({'success': False, 'error': 'Table not found'}), 404

        # Pagination
        limit = min(int(request.args.get('limit', 100)), 1000)
        offset = int(request.args.get('offset', 0))

        # Get total count
        count_result = db.con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
        total = count_result[0] if count_result else 0

        # Get data
        result = db.con.execute(f"SELECT * FROM {table_name} LIMIT {limit} OFFSET {offset}")
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()

        # Convert to list of dicts
        data = []
        for row in rows:
            row_dict = {}
            for i, col in enumerate(columns):
                value = row[i]
                # Handle datetime serialization
                if hasattr(value, 'isoformat'):
                    value = value.isoformat()
                row_dict[col] = value
            data.append(row_dict)

        return jsonify({
            'success': True,
            'table': table_name,
            'columns': columns,
            'data': data,
            'total': total,
            'limit': limit,
            'offset': offset
        })

    except Exception as e:
        logger.error(f"Error browsing {table_name}: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/query', methods=['POST'])
def query():
    """Run custom query (SELECT only)"""
    db = get_db()
    if not db:
        return jsonify({'success': False, 'error': 'Database not available'})

    try:
        sql = request.json.get('sql', '').strip()

        if not sql:
            return jsonify({'success': False, 'error': 'No query provided'})

        # Only allow SELECT queries
        sql_upper = sql.upper()
        if not sql_upper.startswith('SELECT'):
            return jsonify({'success': False, 'error': 'Only SELECT queries allowed'})

        # Block dangerous keywords
        dangerous = ['DROP', 'DELETE', 'INSERT', 'UPDATE', 'ALTER', 'CREATE', 'TRUNCATE']
        for keyword in dangerous:
            if keyword in sql_upper:
                return jsonify({'success': False, 'error': f'{keyword} not allowed'})

        # Execute query
        result = db.con.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()

        # Convert to list of dicts
        data = []
        for row in rows:
            row_dict = {}
            for i, col in enumerate(columns):
                value = row[i]
                if hasattr(value, 'isoformat'):
                    value = value.isoformat()
                row_dict[col] = value
            data.append(row_dict)

        return jsonify({
            'success': True,
            'columns': columns,
            'data': data,
            'row_count': len(data)
        })

    except Exception as e:
        logger.error(f"Query error: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/export/<table_name>')
def export(table_name):
    """Export table as CSV"""
    db = get_db()
    if not db:
        return jsonify({'success': False, 'error': 'Database not available'}), 500

    try:
        # Validate table name
        tables = get_tables()
        if table_name not in tables:
            return jsonify({'success': False, 'error': 'Table not found'}), 404

        # Get all data
        result = db.con.execute(f"SELECT * FROM {table_name}")
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()

        # Build CSV
        import csv
        import io

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)

        for row in rows:
            writer.writerow(row)

        csv_content = output.getvalue()

        return Response(
            csv_content,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={table_name}.csv'}
        )

    except Exception as e:
        logger.error(f"Export error for {table_name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/stats')
def stats():
    """Get database stats"""
    db = get_db()
    if not db:
        return jsonify({'success': False, 'error': 'Database not available'})

    try:
        tables = get_tables()
        table_stats = []

        for table in tables:
            try:
                count = db.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                table_stats.append({
                    'name': table,
                    'row_count': count
                })
            except:
                table_stats.append({'name': table, 'row_count': 'error'})

        return jsonify({
            'success': True,
            'table_count': len(tables),
            'tables': table_stats
        })

    except Exception as e:
        logger.error(f"Error getting DB stats: {e}")
        return jsonify({'success': False, 'error': str(e)})
