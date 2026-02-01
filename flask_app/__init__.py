"""
Flask Application Factory
Creates and configures the Flask application with all blueprints.
"""
import os
from flask import Flask


def create_app(test_config=None):
    """Application factory pattern for creating Flask app."""
    
    # Create app
    template_dir = os.path.join(os.path.dirname(__file__), 'templates')
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    app = Flask(__name__, 
                template_folder=template_dir,
                static_folder=static_dir)
    
    # Configuration
    app.config.from_mapping(
        SECRET_KEY=os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production'),
        DATABASE=os.environ.get('TRADING_DB_PATH', 'data/trading_bot.duckdb'),
    )
    
    if test_config is None:
        # Load instance config if it exists
        app.config.from_pyfile('config.py', silent=True)
    else:
        # Load test config
        app.config.from_mapping(test_config)
    
    # Register blueprints
    from flask_app.blueprints.auth import auth_bp
    app.register_blueprint(auth_bp)

    # Register dashboard blueprint (if it exists)
    try:
        from flask_app.blueprints.dashboard import dashboard_bp
        app.register_blueprint(dashboard_bp)
    except ImportError:
        pass  # Dashboard not implemented yet

    # Register database blueprint
    from flask_app.blueprints.database import database_bp
    app.register_blueprint(database_bp)

    # Register backtest blueprint
    try:
        from flask_app.blueprints.backtest import backtest_bp
        app.register_blueprint(backtest_bp, url_prefix='/backtest')
    except Exception as e:
        print(f"Warning: Could not register backtest blueprint: {e}")
        pass  # Backtest blueprint not implemented yet

    # Register Scanner blueprint
    try:
        from flask_app.blueprints.scanner import scanner_bp
        app.register_blueprint(scanner_bp, url_prefix='/scanner')
    except Exception as e:
        print(f"Warning: Could not register scanner blueprint: {e}")

    # Register Ops blueprint
    from flask_app.blueprints.ops import bp as ops_bp
    app.register_blueprint(ops_bp, url_prefix='/ops')
    
    # Global context processor for templates
    @app.context_processor
    def inject_user_context():
        from flask import session
        return {
            'username': session.get('username'),
            'roles': session.get('roles', [])
        }
    
    # Error handlers
    @app.errorhandler(403)
    def forbidden(error):
        return {'error': 'Forbidden', 'message': 'You do not have permission to access this resource.'}, 403
    
    @app.errorhandler(404)
    def not_found(error):
        return {'error': 'Not Found', 'message': 'The requested resource was not found.'}, 404
    
    # Health check endpoint (no auth required)
    @app.route('/health')
    def health_check():
        return {'status': 'healthy', 'version': '0.1.0'}
    
    # Root redirect to login
    @app.route('/')
    def index():
        from flask import redirect, url_for
        return redirect(url_for('auth.login'))
    
    return app
