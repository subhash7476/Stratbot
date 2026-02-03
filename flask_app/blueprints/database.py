import subprocess
import threading
from datetime import datetime
from flask import Blueprint, render_template, jsonify, request
from flask_app.middleware import login_required, role_required
from core.data.duckdb_client import db_cursor
from core.api.upstox_client import UpstoxClient
from config.credentials import load_credentials

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

@database_bp.route('/api/request-historical-fetch', methods=['POST'])
@login_required
@role_required('admin')
def request_historical_fetch():
    """Request historical data fetch via CLI script."""
    try:
        data = request.get_json()

        # Validate required parameters
        required_fields = ['instrument_key', 'unit', 'interval', 'from_date', 'to_date']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({
                    "success": False,
                    "message": f"Missing required field: {field}"
                }), 400

        # Validate parameters format
        instrument_key = data['instrument_key']
        unit = data['unit']
        interval = data['interval']
        from_date = data['from_date']
        to_date = data['to_date']

        # Validate unit and interval values
        valid_units = ['minutes', 'hours', 'days', 'weeks', 'months']
        if unit not in valid_units:
            return jsonify({
                "success": False,
                "message": f"Invalid unit. Valid units are: {', '.join(valid_units)}"
            }), 400

        # Validate interval based on unit
        if unit == 'minutes' and not (1 <= interval <= 300):
            return jsonify({
                "success": False,
                "message": "For minutes unit, interval must be between 1 and 300"
            }), 400
        elif unit == 'hours' and not (1 <= interval <= 5):
            return jsonify({
                "success": False,
                "message": "For hours unit, interval must be between 1 and 5"
            }), 400
        elif unit in ['days', 'weeks', 'months'] and interval != 1:
            return jsonify({
                "success": False,
                "message": f"For {unit} unit, interval must be 1"
            }), 400

        # Validate date format (YYYY-MM-DD)
        try:
            datetime.strptime(from_date, '%Y-%m-%d')
            datetime.strptime(to_date, '%Y-%m-%d')
        except ValueError:
            return jsonify({
                "success": False,
                "message": "Dates must be in YYYY-MM-DD format"
            }), 400

        # Start the CLI script as a subprocess
        cmd = [
            'python', 'scripts/fetch_upstox_historical.py',
            '--instrument_key', instrument_key,
            '--unit', unit,
            '--interval', str(interval),
            '--from', from_date,
            '--to', to_date
        ]

        # Run the command in a separate thread to avoid blocking
        def run_fetch():
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)  # 5 minute timeout
                if result.returncode != 0:
                    print(f"Error running fetch script: {result.stderr}")
                else:
                    print(f"Fetch script completed successfully: {result.stdout}")
            except subprocess.TimeoutExpired:
                print("Fetch script timed out after 5 minutes")
            except Exception as e:
                print(f"Error running fetch script: {e}")

        thread = threading.Thread(target=run_fetch)
        thread.daemon = True
        thread.start()

        return jsonify({
            "success": True,
            "message": "Historical data fetch initiated",
            "job_status": "started"
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error initiating fetch: {str(e)}"
        }), 500

@database_bp.route('/api/historical-data')
@login_required
@role_required('admin')
def get_historical_data():
    """Get historical candle data from database."""
    try:
        # Get query parameters
        instrument_key = request.args.get('instrument_key')
        from_date = request.args.get('from_date')
        to_date = request.args.get('to_date')
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))

        # Basic validation
        if not instrument_key:
            return jsonify({
                "success": False,
                "message": "instrument_key is required"
            }), 400

        # Build query with filters
        query = "SELECT * FROM ohlcv_1m WHERE instrument_key = ?"
        params = [instrument_key]

        if from_date:
            query += " AND timestamp >= ?"
            params.append(from_date)

        if to_date:
            query += " AND timestamp <= ?"
            params.append(to_date)

        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with db_cursor(read_only=True) as conn:
            rows = conn.execute(query, params).fetchall()

            # Get column names
            cursor_desc = conn.execute("SELECT * FROM ohlcv_1m LIMIT 0").description
            columns = [desc[0] for desc in cursor_desc]

            # Convert rows to list of dictionaries
            data = []
            for row in rows:
                row_dict = {}
                for i, col in enumerate(columns):
                    row_dict[col] = row[i]
                data.append(row_dict)

            # Get total count for pagination
            count_query = "SELECT COUNT(*) FROM ohlcv_1m WHERE instrument_key = ?"
            count_params = [instrument_key]

            if from_date:
                count_query += " AND timestamp >= ?"
                count_params.append(from_date)

            if to_date:
                count_query += " AND timestamp <= ?"
                count_params.append(to_date)

            total_count = conn.execute(count_query, count_params).fetchone()[0]

            return jsonify({
                "success": True,
                "data": data,
                "total": total_count,
                "limit": limit,
                "offset": offset
            })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error fetching historical data: {str(e)}"
        }), 500

@database_bp.route('/api/instruments-search')
@login_required
@role_required('admin')
def instruments_search():
    """Search for instruments from FO stocks master."""
    try:
        search_term = request.args.get('search_term', '').lower()

        with db_cursor(read_only=True) as conn:
            # Query the fo_stocks_master table
            if search_term:
                # Search for instruments that match the search term
                query = """
                    SELECT instrument_key, trading_symbol, exchange
                    FROM fo_stocks_master
                    WHERE LOWER(trading_symbol) LIKE ? OR LOWER(instrument_key) LIKE ?
                    LIMIT 20
                """
                search_pattern = f"%{search_term}%"
                instruments = conn.execute(query, [search_pattern, search_pattern]).fetchall()
            else:
                # Return first 20 instruments if no search term
                query = """
                    SELECT instrument_key, trading_symbol, exchange
                    FROM fo_stocks_master
                    LIMIT 20
                """
                instruments = conn.execute(query).fetchall()

        # Format the results
        formatted_instruments = []
        for row in instruments:
            formatted_instruments.append({
                "key": row[0],  # instrument_key
                "name": row[1],  # trading_symbol
                "exchange": row[2]  # exchange
            })

        return jsonify({
            "success": True,
            "instruments": formatted_instruments
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error searching instruments: {str(e)}"
        }), 500
