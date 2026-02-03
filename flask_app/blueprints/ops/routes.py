import json
import os
import requests
from pathlib import Path
from flask import render_template, jsonify, request, redirect, url_for, flash
from urllib.parse import urlencode
from . import bp
from core.auth.credentials import credentials

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
    
    clock = RealTimeClock()
    broker = PaperBroker(clock)
    execution = ExecutionHandler(clock, broker)
    health = HealthMonitor()
    facade = OpsFacade(execution, health)
    
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
    return jsonify(OpsFacade.get_websocket_status())

@bp.route('/api/kill', methods=['POST'])
def api_kill():
    """Triggers the manual kill switch via the STOP file."""
    try:
        with open("STOP", "w", encoding="utf-8") as f:
            f.write("Manual STOP triggered via Web UI")
        return jsonify({"success": True, "message": "Kill switch engaged. System stopping."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
