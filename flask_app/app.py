# flask_app/app.py
"""
Flask Application Factory
=========================

Creates and configures the Flask application.
Uses the factory pattern for flexibility and testing.
"""

import sys
from pathlib import Path

# Add project root to path for core imports
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, redirect, url_for
from flask_app.config import get_config
from flask_app.extensions import socketio
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_app(config_object=None):
    """
    Application factory.

    Args:
        config_object: Configuration object (optional, uses get_config() if not provided)

    Returns:
        Flask application instance
    """
    app = Flask(
        __name__,
        template_folder='templates',
        static_folder='static'
    )

    # Load configuration
    if config_object is None:
        config_object = get_config()
    app.config.from_object(config_object)

    # Ensure session directory exists
    session_dir = Path(app.config.get('SESSION_FILE_DIR', 'sessions'))
    session_dir.mkdir(parents=True, exist_ok=True)

    # Initialize extensions
    init_extensions(app)

    # Register blueprints
    register_blueprints(app)

    # Register error handlers
    register_error_handlers(app)

    # Register context processors
    register_context_processors(app)

    # Root route
    @app.route('/')
    def index():
        return redirect(url_for('dashboard.index'))

    logger.info(f"Flask app created (env: {app.config.get('ENV', 'unknown')})")

    return app


def init_extensions(app):
    """Initialize Flask extensions"""
    socketio.init_app(app)
    logger.info("Extensions initialized")


def register_blueprints(app):
    """Register Flask blueprints"""
    from flask_app.blueprints.auth import bp as auth_bp
    from flask_app.blueprints.dashboard import bp as dashboard_bp
    from flask_app.blueprints.data import bp as data_bp
    from flask_app.blueprints.scanner import bp as scanner_bp
    from flask_app.blueprints.database import bp as database_bp
    from flask_app.blueprints.options import bp as options_bp

    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(dashboard_bp, url_prefix='/dashboard')
    app.register_blueprint(data_bp, url_prefix='/data')
    app.register_blueprint(scanner_bp, url_prefix='/scanner')
    app.register_blueprint(database_bp, url_prefix='/database')
    app.register_blueprint(options_bp, url_prefix='/options')

    logger.info("Blueprints registered")


def register_error_handlers(app):
    """Register error handlers"""

    @app.errorhandler(404)
    def not_found_error(error):
        return render_error_page(404, "Page not found"), 404

    @app.errorhandler(500)
    def internal_error(error):
        return render_error_page(500, "Internal server error"), 500

    @app.errorhandler(403)
    def forbidden_error(error):
        return render_error_page(403, "Access forbidden"), 403


def render_error_page(code, message):
    """Render error page"""
    from flask import render_template
    return render_template('error.html', error_code=code, error_message=message)


def register_context_processors(app):
    """Register template context processors"""

    @app.context_processor
    def utility_processor():
        """Add utility functions to templates"""
        from datetime import datetime

        def format_price(value):
            """Format price with 2 decimal places"""
            if value is None:
                return "-"
            return f"₹{value:,.2f}"

        def format_number(value):
            """Format number with commas"""
            if value is None:
                return "-"
            return f"{value:,}"

        def format_percent(value):
            """Format percentage"""
            if value is None:
                return "-"
            return f"{value:+.2f}%"

        def format_datetime(value):
            """Format datetime"""
            if value is None:
                return "-"
            if isinstance(value, str):
                value = datetime.fromisoformat(value)
            return value.strftime("%Y-%m-%d %H:%M")

        def format_time(value):
            """Format time only"""
            if value is None:
                return "-"
            if isinstance(value, str):
                value = datetime.fromisoformat(value)
            return value.strftime("%H:%M")

        return {
            'format_price': format_price,
            'format_number': format_number,
            'format_percent': format_percent,
            'format_datetime': format_datetime,
            'format_time': format_time,
            'now': datetime.now
        }

    @app.context_processor
    def navigation_processor():
        """Add navigation items to templates"""
        nav_items = [
            {'name': 'Dashboard', 'endpoint': 'dashboard.index', 'icon': 'home'},
            {'name': 'Data Manager', 'endpoint': 'data.index', 'icon': 'database'},
            {'name': 'Scanner', 'endpoint': 'scanner.index', 'icon': 'search'},
            {'name': 'Options', 'endpoint': 'options.index', 'icon': 'trending-up'},
            {'name': 'Database', 'endpoint': 'database.index', 'icon': 'server'},
        ]
        return {'nav_items': nav_items}
