# flask_app/config.py
"""
Flask Application Configuration
================================

Centralized configuration for the Flask application.
Uses environment variables with sensible defaults.
"""

import os
from pathlib import Path
from datetime import timedelta

# Base directory
BASE_DIR = Path(__file__).parent.parent
FLASK_APP_DIR = Path(__file__).parent


class Config:
    """Base configuration"""

    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    DEBUG = False
    TESTING = False

    # Session
    SESSION_TYPE = 'filesystem'
    SESSION_FILE_DIR = FLASK_APP_DIR / 'sessions'
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)  # Trading day

    # Database
    DATABASE_PATH = BASE_DIR / 'data' / 'trading_bot.duckdb'

    # Upstox API
    UPSTOX_API_KEY = os.environ.get('UPSTOX_API_KEY', '')
    UPSTOX_API_SECRET = os.environ.get('UPSTOX_API_SECRET', '')
    UPSTOX_REDIRECT_URI = os.environ.get('UPSTOX_REDIRECT_URI', 'http://127.0.0.1:5000/auth/callback')

    # Credentials file (legacy support)
    CREDENTIALS_FILE = BASE_DIR / 'config' / 'credentials.json'

    # Market hours (IST)
    MARKET_OPEN_HOUR = 9
    MARKET_OPEN_MINUTE = 15
    MARKET_CLOSE_HOUR = 15
    MARKET_CLOSE_MINUTE = 30

    # WebSocket
    WEBSOCKET_RECONNECT_DELAY = 2  # seconds
    WEBSOCKET_MAX_RECONNECT_DELAY = 30  # seconds
    WEBSOCKET_MAX_RECONNECT_ATTEMPTS = 10

    # Scanner
    SCANNER_DEFAULT_LOOKBACK_DAYS = 60
    SCANNER_DEFAULT_MIN_SCORE = 4

    # SocketIO
    SOCKETIO_ASYNC_MODE = 'threading'
    SOCKETIO_PING_TIMEOUT = 10
    SOCKETIO_PING_INTERVAL = 5


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    ENV = 'development'


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    ENV = 'production'

    # In production, SECRET_KEY must be set via environment
    @property
    def SECRET_KEY(self):
        key = os.environ.get('SECRET_KEY')
        if not key:
            raise ValueError("SECRET_KEY environment variable must be set in production")
        return key


class TestingConfig(Config):
    """Testing configuration"""
    TESTING = True
    DEBUG = True


# Configuration mapping
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}


def get_config():
    """Get configuration based on environment"""
    env = os.environ.get('FLASK_ENV', 'development')
    return config.get(env, config['default'])()
