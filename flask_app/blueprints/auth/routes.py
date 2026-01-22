# flask_app/blueprints/auth/routes.py
"""
Authentication Routes
=====================

Handles:
- Login page display
- OAuth callback from Upstox
- Token storage and retrieval
- Instrument download
"""

import sys
from pathlib import Path

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import render_template, redirect, url_for, request, flash, session, jsonify, current_app
from . import bp
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def get_credentials():
    """Load credentials from file"""
    try:
        creds_file = current_app.config.get('CREDENTIALS_FILE')
        if creds_file and Path(creds_file).exists():
            with open(creds_file, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading credentials: {e}")
    return {}


def save_credentials(creds):
    """Save credentials to file"""
    try:
        creds_file = current_app.config.get('CREDENTIALS_FILE')
        if creds_file:
            Path(creds_file).parent.mkdir(parents=True, exist_ok=True)
            with open(creds_file, 'w') as f:
                json.dump(creds, f, indent=2)
            return True
    except Exception as e:
        logger.error(f"Error saving credentials: {e}")
    return False


def get_access_token():
    """Get current access token"""
    creds = get_credentials()
    return creds.get('access_token')


def is_authenticated():
    """Check if user has valid access token"""
    token = get_access_token()
    return token is not None and len(token) > 0


@bp.route('/')
@bp.route('/login')
def login():
    """Login page"""
    creds = get_credentials()
    has_token = bool(creds.get('access_token'))
    last_login = creds.get('last_login', 'Never')

    return render_template('auth/login.html',
                           has_token=has_token,
                           last_login=last_login,
                           api_key=creds.get('api_key', ''))


@bp.route('/initiate')
def initiate_login():
    """Redirect to Upstox OAuth page"""
    creds = get_credentials()
    api_key = creds.get('api_key')

    if not api_key:
        flash('Please configure API key first', 'error')
        return redirect(url_for('auth.login'))

    redirect_uri = current_app.config.get('UPSTOX_REDIRECT_URI', 'http://127.0.0.1:5000/auth/callback')

    auth_url = (
        f"https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code"
        f"&client_id={api_key}"
        f"&redirect_uri={redirect_uri}"
    )

    return redirect(auth_url)


@bp.route('/callback')
def callback():
    """Handle OAuth callback from Upstox"""
    code = request.args.get('code')
    error = request.args.get('error')

    if error:
        flash(f'Authentication error: {error}', 'error')
        return redirect(url_for('auth.login'))

    if not code:
        flash('No authorization code received', 'error')
        return redirect(url_for('auth.login'))

    # Exchange code for token
    try:
        creds = get_credentials()
        api_key = creds.get('api_key')
        api_secret = creds.get('api_secret')
        redirect_uri = current_app.config.get('UPSTOX_REDIRECT_URI')

        import requests
        response = requests.post(
            'https://api.upstox.com/v2/login/authorization/token',
            data={
                'code': code,
                'client_id': api_key,
                'client_secret': api_secret,
                'redirect_uri': redirect_uri,
                'grant_type': 'authorization_code'
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )

        if response.status_code == 200:
            data = response.json()
            access_token = data.get('access_token')

            if access_token:
                creds['access_token'] = access_token
                creds['last_login'] = datetime.now().isoformat()
                save_credentials(creds)

                session['authenticated'] = True
                flash('Login successful!', 'success')
                return redirect(url_for('dashboard.index'))

        flash(f'Token exchange failed: {response.text}', 'error')

    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        flash(f'Authentication error: {str(e)}', 'error')

    return redirect(url_for('auth.login'))


@bp.route('/logout')
def logout():
    """Logout - clear session"""
    session.clear()
    flash('Logged out successfully', 'info')
    return redirect(url_for('auth.login'))


@bp.route('/save-config', methods=['POST'])
def save_config():
    """Save API configuration"""
    api_key = request.form.get('api_key', '').strip()
    api_secret = request.form.get('api_secret', '').strip()
    redirect_uri = request.form.get('redirect_uri', '').strip()

    if not api_key or not api_secret:
        flash('API Key and Secret are required', 'error')
        return redirect(url_for('auth.login'))

    creds = get_credentials()
    creds['api_key'] = api_key
    creds['api_secret'] = api_secret
    if redirect_uri:
        creds['redirect_uri'] = redirect_uri

    if save_credentials(creds):
        flash('Configuration saved successfully', 'success')
    else:
        flash('Failed to save configuration', 'error')

    return redirect(url_for('auth.login'))


@bp.route('/download-instruments', methods=['POST'])
def download_instruments():
    """Download instruments from Upstox"""
    try:
        from core.api.instruments import download_and_split_instruments

        success = download_and_split_instruments()

        if success:
            flash('Instruments downloaded successfully', 'success')
        else:
            flash('Failed to download instruments', 'error')

    except Exception as e:
        logger.error(f"Error downloading instruments: {e}")
        flash(f'Error: {str(e)}', 'error')

    return redirect(url_for('auth.login'))


@bp.route('/status')
def status():
    """Get authentication status (JSON)"""
    return jsonify({
        'authenticated': is_authenticated(),
        'has_token': bool(get_access_token())
    })
