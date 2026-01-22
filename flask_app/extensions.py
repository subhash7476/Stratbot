# flask_app/extensions.py
"""
Flask Extensions
================

Initialize Flask extensions here to avoid circular imports.
Extensions are initialized without app, then bound in create_app().
"""

from flask_socketio import SocketIO

# SocketIO for real-time communication
# async_mode='threading' works well with DuckDB and our WebSocket client
socketio = SocketIO(
    cors_allowed_origins='*',
    async_mode='threading',
    ping_timeout=10,
    ping_interval=5,
    logger=False,
    engineio_logger=False
)


# Simple in-memory session store (for development)
# In production, consider Redis
class SessionStore:
    """Simple session data store"""
    _data = {}

    @classmethod
    def get(cls, key, default=None):
        return cls._data.get(key, default)

    @classmethod
    def set(cls, key, value):
        cls._data[key] = value

    @classmethod
    def delete(cls, key):
        cls._data.pop(key, None)

    @classmethod
    def clear(cls):
        cls._data.clear()


session_store = SessionStore()
