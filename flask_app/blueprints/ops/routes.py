import json
import os
import requests
from pathlib import Path
from flask import render_template, jsonify, request, redirect, url_for, flash
from urllib.parse import urlencode
from . import bp
from core.auth.credentials import credentials
from core.logging.log_reader import tail_log_file, count_errors, get_available_log_files

METRICS_PATH = Path("logs/execution_metrics.json")
HEALTH_PATH = Path("logs/health_status.json")

@bp.route('/')
def index():
    """Operations Dashboard main page."""
    metrics = {}
    if METRICS_PATH.exists():
        with open(METRICS_PATH, "r", encoding="utf-8") as f:
            metrics = json.load(f)
            
    health = {}
    if HEALTH_PATH.exists():
        with open(HEALTH_PATH, "r", encoding="utf-8") as f:
            health = json.load(f)
            
    # Add Upstox status
    upstox_status = {
        "connected": credentials.has_upstox_token,
        "api_key_set": bool(credentials.get("api_key")),
        "api_secret_set": bool(credentials.get("api_secret")),
        "redirect_uri": credentials.get("redirect_uri", "http://127.0.0.1:5000/ops/callback/upstox")
    }
            
    return render_template('ops/index.html',
                         metrics=metrics,
                         health=health,
                         upstox=upstox_status,
                         credentials=credentials)

@bp.route('/api/status')
def api_status():
    """JSON endpoint for real-time status updates."""
    from app_facade.ops_facade import OpsFacade
    from core.execution.handler import ExecutionHandler
    from core.execution.health_monitor import HealthMonitor
    from core.clock import RealTimeClock
    from core.brokers.paper_broker import PaperBroker
    from flask import current_app
    
    db_manager = getattr(current_app, 'db_manager', None)
    clock = RealTimeClock()
    broker = PaperBroker(clock)
    execution = ExecutionHandler(db_manager=db_manager, clock=clock, broker=broker)
    health = HealthMonitor()
    facade = OpsFacade(execution, health, db_manager=db_manager)
    
    return jsonify({
        "success": True,
        "metrics": facade.get_live_metrics(),
        "health": facade.get_health_status(),
        "matrix": facade.get_confluence_matrix(),
        "upstox_connected": credentials.has_upstox_token
    })


@bp.route('/api/config/upstox', methods=['POST'])
def save_config():
    """Saves Upstox API keys."""
    data = request.json
    api_key = data.get('api_key')
    api_secret = data.get('api_secret')
    redirect_uri = data.get('redirect_uri')
    
    if not all([api_key, api_secret, redirect_uri]):
        return jsonify({"success": False, "error": "Missing required fields"}), 400
        
    credentials.save({
        "api_key": api_key,
        "api_secret": api_secret,
        "redirect_uri": redirect_uri
    })
    
    return jsonify({"success": True, "message": "Upstox configuration saved."})

@bp.route('/login/upstox')
def upstox_login():
    """Redirects user to Upstox login page."""
    api_key = credentials.get("api_key")
    redirect_uri = credentials.get("redirect_uri")
    
    if not api_key or not redirect_uri:
        flash("Please configure Upstox API keys first.", "error")
        return redirect(url_for('ops.index'))
        
    base_url = "https://api.upstox.com/v2/login/authorization/dialog"
    params = {
        "response_type": "code",
        "client_id": api_key,
        "redirect_uri": redirect_uri
    }
    auth_url = f"{base_url}?{urlencode(params)}"
    return redirect(auth_url)

@bp.route('/callback/upstox')
def upstox_callback():
    """Handles the redirect from Upstox and exchanges code for token."""
    code = request.args.get('code')
    if not code:
        flash("Authorization failed: No code received.", "error")
        return redirect(url_for('ops.index'))
        
    api_key = credentials.get("api_key")
    api_secret = credentials.get("api_secret")
    redirect_uri = credentials.get("redirect_uri")
    
    url = "https://api.upstox.com/v2/login/authorization/token"
    headers = {
        'accept': 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    data = {
        'code': code,
        'client_id': api_key,
        'client_secret': api_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }
    
    try:
        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()
        token_data = response.json()
        
        credentials.save(token_data)
        flash("Upstox connected successfully!", "success")
    except Exception as e:
        flash(f"Failed to exchange token: {e}", "error")
        
    return redirect(url_for('ops.index'))

@bp.route('/api/websocket_status')
def api_websocket_status():
    """Read-only endpoint for current WebSocket status."""
    from app_facade.ops_facade import OpsFacade
    from flask import current_app
    from core.execution.handler import ExecutionHandler
    from core.execution.health_monitor import HealthMonitor
    from core.clock import RealTimeClock
    from core.brokers.paper_broker import PaperBroker
    
    db_manager = getattr(current_app, 'db_manager', None)
    clock = RealTimeClock()
    broker = PaperBroker(clock)
    execution = ExecutionHandler(db_manager=db_manager, clock=clock, broker=broker)
    health = HealthMonitor()
    facade = OpsFacade(execution, health, db_manager=db_manager)
    return jsonify(facade.get_websocket_status())


@bp.route('/api/kill', methods=['POST'])
def api_kill():
    """Triggers the manual kill switch via the STOP file."""
    try:
        with open("STOP", "w", encoding="utf-8") as f:
            f.write("Manual STOP triggered via Web UI")
        return jsonify({"success": True, "message": "Kill switch engaged. System stopping."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/logs')
def api_logs():
    """Return log entries from specified log file with optional filtering."""
    file_name = request.args.get('file', '')
    level_filter = request.args.get('level', '').upper() or None
    source_filter = request.args.get('source', '') or None
    lines = int(request.args.get('lines', 300))
    
    if not file_name:
        return jsonify({"success": False, "error": "Log file name is required"}), 400
    
    # Validate file name to prevent directory traversal
    if '..' in file_name or file_name.startswith('/') or '../' in file_name:
        return jsonify({"success": False, "error": "Invalid file name"}), 400
    
    log_file_path = Path("logs") / file_name
    
    try:
        log_entries = tail_log_file(
            str(log_file_path),
            lines=lines,
            level_filter=level_filter,
            source_filter=source_filter
        )
        return jsonify({"success": True, "entries": log_entries})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/error-count')
def api_error_count():
    """Return count of ERROR entries in log files within time window."""
    file_name = request.args.get('file')
    window_minutes = int(request.args.get('window_minutes', 60))
    
    try:
        if file_name:
            # Validate file name to prevent directory traversal
            if '..' in file_name or file_name.startswith('/') or '../' in file_name:
                return jsonify({"success": False, "error": "Invalid file name"}), 400
            
            log_file_path = Path("logs") / file_name
            error_count = count_errors(str(log_file_path), window_minutes=window_minutes)
        else:
            # Sum errors from ALL log files
            error_count = 0
            for log_file in get_available_log_files():
                error_count += count_errors(str(Path("logs") / log_file), window_minutes=window_minutes)
                
        return jsonify({"success": True, "error_count": error_count})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/log-files')
def api_log_files():
    """Return list of available log files."""
    try:
        log_files = get_available_log_files()
        return jsonify({"success": True, "log_files": log_files})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
