import subprocess
import threading
from datetime import datetime
from pathlib import Path
from flask import Blueprint, render_template, jsonify, request, current_app
from flask_app.middleware import login_required, role_required
from core.database.manager import DatabaseManager
from core.logging import setup_logger

logger = setup_logger("database_bp")

database_bp = Blueprint('database', __name__, url_prefix='/database')

def get_db_manager():
    return getattr(current_app, 'db_manager', None) or DatabaseManager(Path("data").resolve())

def get_db_for_table(table_name: str):
    """Routing logic to find which DB a table belongs to."""
    trading_tables = ['orders', 'trades', 'positions']
    signals_tables = ['confluence_insights', 'regime_insights', 'signals']
    config_tables = ['users', 'roles', 'user_watchlist', 'instrument_meta', 'websocket_status', 'runner_state', 'fo_stocks']
    market_tables = ['ticks', 'candles']

    if table_name in trading_tables: return 'trading'
    if table_name in signals_tables: return 'signals'
    if table_name in config_tables: return 'config'
    if table_name in market_tables: return 'live_buffer'
    return 'config' # Default

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
    """Lists all tables from all databases."""
    all_tables = []
    db = get_db_manager()
    
    try:
        # 1. Config DB
        with db.config_reader() as conn:
            res = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            all_tables.extend([r[0] for r in res])
            
        # 2. Trading DB
        with db.trading_reader() as conn:
            res = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            all_tables.extend([r[0] for r in res])
            
        # 3. Signals DB
        with db.signals_reader() as conn:
            res = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            all_tables.extend([r[0] for r in res])
            
        # 4. Live Buffer
        with db.live_buffer_reader() as conns:
            if 'ticks' in conns: all_tables.append('ticks (live)')
            if 'candles' in conns: all_tables.append('candles (live)')
    except Exception as e:
        logger.error(f"Error listing tables: {e}")

    return jsonify({"success": True, "tables": list(set(all_tables))})

@database_bp.route('/api/query/<table_name>', methods=['POST'])
@login_required
@role_required('admin')
def query_table(table_name):
    """Universal query handler across isolated databases."""
    try:
        from core.database.utils.symbol_utils import key_to_symbol
        db = get_db_manager()
        clean_name = table_name.replace(' (live)', '')
        db_type = get_db_for_table(clean_name)
        
        data = request.get_json()
        page_size = int(data.get('page_size', 50))
        page = int(data.get('page', 1))
        offset = (page - 1) * page_size
        filters = data.get('filters', {})

        # Build dynamic WHERE clause
        where_clauses = []
        params = []

        for col, val in filters.items():
            if val is None or val == '': continue
            
            if isinstance(val, dict):
                # Range filter
                if val.get('min') is not None:
                    where_clauses.append(f"{col} >= ?")
                    params.append(val['min'])
                if val.get('max') is not None:
                    where_clauses.append(f"{col} <= ?")
                    params.append(val['max'])
            else:
                # Text or Select filter
                select_cols = ['status', 'side', 'direction', 'exchange', 'timeframe', 'bias', 'signal_type', 'is_active', 'market_type', 'instrument_type']
                if col.lower() in select_cols:
                    where_clauses.append(f"{col} = ?")
                    params.append(val)
                else:
                    # Use ILIKE for DuckDB, LIKE for SQLite (SQLite LIKE is case-insensitive for ASCII)
                    if db_type == 'live_buffer':
                        where_clauses.append(f"{col} ILIKE ?")
                    else:
                        where_clauses.append(f"{col} LIKE ?")
                    params.append(f"%{val}%")

        where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        mgr_map = {
            'trading': db.trading_reader,
            'signals': db.signals_reader,
            'config': db.config_reader
        }

        result = []
        total = 0

        if db_type in mgr_map:
            with mgr_map[db_type]() as conn:
                query = f"SELECT * FROM {clean_name}{where_sql} LIMIT ? OFFSET ?"
                query_params = params + [page_size, offset]
                cursor = conn.execute(query, query_params)
                rows = cursor.fetchall()
                cols = [d[0] for d in cursor.description]
                result = [dict(zip(cols, r)) for r in rows]
                
                count_query = f"SELECT count(*) FROM {clean_name}{where_sql}"
                total_res = conn.execute(count_query, params).fetchone()
                total = total_res[0] if total_res else 0
        elif db_type == 'live_buffer':
            with db.live_buffer_reader() as conns:
                conn = conns.get('candles') if clean_name == 'candles' else conns.get('ticks')
                if not conn: return jsonify({"success": False, "message": "Live table not active"}), 404
                
                query = f"SELECT * FROM {clean_name}{where_sql} LIMIT ? OFFSET ?"
                query_params = params + [page_size, offset]
                cursor = conn.execute(query, query_params)
                rows = cursor.fetchall()
                cols = [d[0] for d in cursor.description]
                result = [dict(zip(cols, r)) for r in rows]
                
                count_query = f"SELECT count(*) FROM {clean_name}{where_sql}"
                total_res = conn.execute(count_query, params).fetchone()
                total = total_res[0] if total_res else 0
        else:
            return jsonify({"success": False, "message": "Table not found"}), 404

        # Enrich with Trading Symbol if missing
        for row in result:
            key = row.get('instrument_key') or row.get('symbol')
            if key and not row.get('trading_symbol'):
                row['trading_symbol'] = key_to_symbol(key, db)

        return jsonify({
            "success": True,
            "data": result,
            "total": total,
            "page_size": page_size,
            "page": page
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500

@database_bp.route('/api/table-metadata/<table_name>')
@login_required
@role_required('admin')
def get_table_metadata(table_name):
    """Universal metadata handler."""
    try:
        db = get_db_manager()
        clean_name = table_name.replace(' (live)', '')
        db_type = get_db_for_table(clean_name)
        
        # Simple mapping for the context manager
        mgr_map = {
            'trading': db.trading_reader,
            'signals': db.signals_reader,
            'config': db.config_reader
        }

        if db_type in mgr_map:
            with mgr_map[db_type]() as conn:
                columns = conn.execute(f"PRAGMA table_info('{clean_name}')").fetchall()
                metadata = []
                for col in columns:
                    col_name = col[1]
                    col_type = col[2].upper()
                    
                    col_meta = {
                        "name": col_name,
                        "type": col_type,
                        "filter_type": "text"
                    }

                    if any(t in col_type for t in ['INT', 'FLOAT', 'DOUBLE', 'DECIMAL', 'NUMERIC', 'REAL']):
                        col_meta["filter_type"] = "range"
                        stats = conn.execute(f"SELECT MIN({col_name}), MAX({col_name}) FROM {clean_name}").fetchone()
                        if stats is not None:
                            col_meta["min"] = stats[0] if stats[0] is not None else 0
                            col_meta["max"] = stats[1] if stats[1] is not None else 100
                    elif "BOOLEAN" in col_type:
                        col_meta["filter_type"] = "select"
                        col_meta["options"] = ["true", "false"]
                    elif col_name.lower() in ['status', 'side', 'direction', 'exchange', 'timeframe', 'bias', 'signal_type']:
                        col_meta["filter_type"] = "select"
                        distinct = conn.execute(f"SELECT DISTINCT {col_name} FROM {clean_name} WHERE {col_name} IS NOT NULL LIMIT 20").fetchall()
                        col_meta["options"] = [str(d[0]) for d in distinct]
                    elif "TIMESTAMP" in col_type or "DATETIME" in col_type or "DATE" in col_type:
                        col_meta["filter_type"] = "date"

                    metadata.append(col_meta)
                return jsonify({"success": True, "metadata": metadata})
        elif db_type == 'live_buffer':
             with db.live_buffer_reader() as conns:
                conn = conns.get('candles') if clean_name == 'candles' else conns.get('ticks')
                if not conn: return jsonify({"success": False, "message": "Live table not active"}), 404
                
                # DuckDB doesn't have PRAGMA table_info in the same way, but it has DESCRIBE
                cols = conn.execute(f"DESCRIBE {clean_name}").fetchall()
                metadata = []
                for col in cols:
                    col_name = col[0]
                    col_type = col[1].upper()
                    col_meta = {
                        "name": col_name,
                        "type": col_type,
                        "filter_type": "text"
                    }
                    if any(t in col_type for t in ['INT', 'FLOAT', 'DOUBLE', 'DECIMAL', 'NUMERIC', 'REAL']):
                        col_meta["filter_type"] = "range"
                    elif "TIMESTAMP" in col_type:
                        col_meta["filter_type"] = "date"
                    
                    metadata.append(col_meta)
                return jsonify({"success": True, "metadata": metadata})
        
        return jsonify({"success": False, "message": "Table not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@database_bp.route('/api/request-historical-fetch', methods=['POST'])
@login_required
@role_required('admin')
def request_historical_fetch():
    """Request historical data fetch."""
    try:
        data = request.get_json()

        # Resolve instrument key if it's a trading symbol
        instrument_key = data['instrument_key']

        # If it's a list of keys, we skip resolution logic for now (assuming frontend sends correct keys)
        if instrument_key != 'ALL' and ',' not in instrument_key and '|' not in instrument_key:
            from core.database.utils.symbol_utils import resolve_to_instrument_key
            resolved_key = resolve_to_instrument_key(instrument_key)
            if resolved_key:
                instrument_key = resolved_key
            else:
                return jsonify({"success": False, "message": f"Could not resolve instrument key for symbol: {data['instrument_key']}"}), 400

        # This part still calls a script, which is fine
        workers = data.get('workers', 5)
        cmd = [
            'python', 'scripts/fetch_upstox_historical.py',
            '--instrument_key', instrument_key,
            '--unit', data['unit'],
            '--interval', str(data['interval']),
            '--from', data['from_date'],
            '--to', data['to_date'],
            '--workers', str(workers)
        ]

        logger.info(f"Initiating historical fetch: {instrument_key} ({data['unit']} {data['interval']}) from {data['from_date']} to {data['to_date']} with {workers} workers")

        def run_fetch():
            try:
                # Capture output to log it
                process = subprocess.run(cmd, capture_output=True, text=True)
                if process.returncode == 0:
                    logger.info(f"Historical fetch completed successfully for {instrument_key}")
                else:
                    logger.error(f"Historical fetch failed for {instrument_key} (exit {process.returncode})")
                    if process.stderr:
                        logger.error(f"Fetcher stderr: {process.stderr.strip()}")
                    if process.stdout:
                        logger.info(f"Fetcher stdout: {process.stdout.strip()}")
            except Exception as e:
                logger.error(f"Error in historical fetch thread: {e}")

        threading.Thread(target=run_fetch, daemon=True).start()
        return jsonify({"success": True, "message": "Historical data fetch initiated"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@database_bp.route('/api/historical-dates')
@login_required
@role_required('admin')
def get_historical_dates():
    """Get list of available historical data dates from 1m candles directory."""
    try:
        dates_dir = Path("data/market_data/nse/candles/1m")
        if not dates_dir.exists():
            return jsonify({"success": True, "dates": []})
        
        dates = []
        for file_path in dates_dir.glob("*.duckdb"):
            # Extract date from filename (e.g., "2026-02-04.duckdb" -> "2026-02-04")
            date_str = file_path.stem
            dates.append(date_str)
        
        # Sort dates in descending order (most recent first)
        dates.sort(reverse=True)
        
        return jsonify({"success": True, "dates": dates})
    except Exception as e:
        logger.error(f"Error getting historical dates: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@database_bp.route('/api/historical-symbols/<date_str>')
@login_required
@role_required('admin')
def get_historical_symbols(date_str):
    """Get list of available symbols for a specific date."""
    try:
        # Validate date format
        from datetime import datetime
        datetime.strptime(date_str, "%Y-%m-%d")  # Will raise ValueError if invalid
        
        file_path = Path(f"data/market_data/nse/candles/1m/{date_str}.duckdb")
        if not file_path.exists():
            return jsonify({"success": False, "message": f"Data file not found for date: {date_str}"}), 404
        
        import duckdb
        conn = duckdb.connect(str(file_path), read_only=True)
        try:
            # Query distinct symbols from the candles table
            result = conn.execute("SELECT DISTINCT symbol FROM candles ORDER BY symbol").fetchall()
            symbols = [row[0] for row in result]
            
            return jsonify({"success": True, "symbols": symbols})
        finally:
            conn.close()
    except ValueError:
        return jsonify({"success": False, "message": "Invalid date format. Use YYYY-MM-DD"}), 400
    except Exception as e:
        logger.error(f"Error getting historical symbols for {date_str}: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@database_bp.route('/api/fix-timestamps')
@login_required
@role_required('admin')
def fix_timestamps_endpoint():
    """Correct timestamps in all historical DuckDB files."""
    try:
        count = fix_all_historical_timestamps()
        return jsonify({"success": True, "message": f"Fixed timestamps in {count} files"})
    except Exception as e:
        logger.error(f"Error in fix-timestamps endpoint: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

def fix_all_historical_timestamps():
    """Shifts timestamps by -5:30 if they are in the 'buggy' range."""
    import duckdb
    import os
    
    dates_dir = Path("data/market_data/nse/candles/1m")
    if not dates_dir.exists():
        return 0
    
    fixed_files = 0
    for file_path in dates_dir.glob("*.duckdb"):
        try:
            conn = duckdb.connect(str(file_path), read_only=False)
            # Only shift if we find timestamps that are clearly after market hours (due to +5:30 shift)
            # Market ends at 15:30 IST. If shifted, it shows 21:00 IST.
            # We check if there are any records > 15:30
            res = conn.execute("SELECT COUNT(*) FROM candles WHERE CAST(timestamp AS TIME) > '15:30:00'").fetchone()
            if res and res[0] > 0:
                conn.execute("""
                    UPDATE candles 
                    SET timestamp = timestamp - INTERVAL '5 hours 30 minutes'
                    WHERE CAST(timestamp AS TIME) > '15:30:00'
                """)
                conn.execute("CHECKPOINT")
                fixed_files += 1
            conn.close()
        except Exception as e:
            logger.error(f"Failed to fix {file_path}: {e}")
            
    return fixed_files

@database_bp.route('/api/historical-data', methods=['POST'])
@login_required
@role_required('admin')
def get_historical_data():
    """Query historical candle data with filters."""
    try:
        data = request.get_json()
        date_str = data.get('date')
        symbol = data.get('symbol')
        start_time = data.get('start_time')  # Optional
        end_time = data.get('end_time')      # Optional
        page_size = int(data.get('page_size', 50))
        page = int(data.get('page', 1))
        offset = (page - 1) * page_size

        # Validate date format
        from datetime import datetime
        datetime.strptime(date_str, "%Y-%m-%d")  # Will raise ValueError if invalid

        file_path = Path(f"data/market_data/nse/candles/1m/{date_str}.duckdb")
        if not file_path.exists():
            return jsonify({"success": False, "message": f"Data file not found for date: {date_str}"}), 404

        import duckdb
        conn = duckdb.connect(str(file_path), read_only=True)
        try:
            # Build query with filters
            where_conditions = ["symbol = ?"]
            params = [symbol]
            
            if start_time:
                where_conditions.append("timestamp >= ?")
                params.append(start_time)
            if end_time:
                where_conditions.append("timestamp <= ?")
                params.append(end_time)
            
            where_clause = " AND ".join(where_conditions)
            base_query = f"SELECT * FROM candles WHERE {where_clause}"
            
            # Get total count
            count_query = f"SELECT COUNT(*) FROM candles WHERE {where_clause}"
            res = conn.execute(count_query, params).fetchone()
            total = res[0] if res else 0
            
            # Get paginated data
            query = f"{base_query} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
            params.extend([page_size, offset])
            
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            cols = [desc[0] for desc in cursor.description]
            result = [dict(zip(cols, row)) for row in rows]
            
            return jsonify({
                "success": True,
                "data": result,
                "total": total,
                "page_size": page_size,
                "page": page
            })
        finally:
            conn.close()
    except ValueError:
        return jsonify({"success": False, "message": "Invalid date format. Use YYYY-MM-DD"}), 400
    except Exception as e:
        logger.error(f"Error getting historical data: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
