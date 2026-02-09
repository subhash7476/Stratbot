"""
Flask Application Factory
Creates and configures the Flask application with all blueprints.
"""
import os
import threading
import json
import logging
import time
from queue import Queue, Empty
from pathlib import Path
from flask import Flask, Response
from core.database.manager import DatabaseManager
from core.messaging.zmq_handler import ZmqSubscriber
from core.logging import setup_logger

logger = setup_logger("flask_app")

class TelemetryBridge:
    """
    ZMQ-to-SSE Bridge.
    Runs in a background thread and forwards ZMQ telemetry to internal subscribers.
    """
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.subscribers = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        # In-memory "latest-wins" telemetry store
        self.latest_telemetry = {
            "metrics": {},
            "positions": {},
            "health": {},
            "logs": []
        }

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="TelemetryBridgeThread", daemon=True)
        self._thread.start()
        logger.info(f"Telemetry bridge started on {self.host}:{self.port}")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def subscribe(self):
        """Returns a new queue for a client to listen to."""
        q = Queue(maxsize=10)  # Small HWM for SSE clients
        with self._lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def _run(self):
        # Subscribe to all telemetry topics
        # Bridge BINDS so multiple nodes can CONNECT
        subscriber = ZmqSubscriber(
            host=self.host,
            port=self.port,
            topics=["telemetry"],
            bind=True
        )

        while not self._stop_event.is_set():
            try:
                # Use a small timeout to allow checking stop_event
                envelope = subscriber.recv(timeout_ms=500)
                if not envelope:
                    continue

                # Update the in-memory store based on message type
                msg_type = envelope.get("type", "")
                topic = envelope.get("topic", "")
                
                with self._lock:
                    if msg_type == "telemetry.metrics":
                        self.latest_telemetry["metrics"] = envelope.get("data", {})
                    elif msg_type == "telemetry.positions":
                        self.latest_telemetry["positions"] = envelope.get("data", {})
                    elif "telemetry.health" in topic:
                        self.latest_telemetry["health"][topic] = envelope.get("data", {})
                    elif "telemetry.logs" in topic:
                        # Add to logs with rolling buffer (keep last 100 entries)
                        log_entry = envelope.get("data", {})
                        self.latest_telemetry["logs"].append(log_entry)
                        if len(self.latest_telemetry["logs"]) > 100:
                            self.latest_telemetry["logs"] = self.latest_telemetry["logs"][-100:]

                # Push to all active SSE queues
                with self._lock:
                    for q in self.subscribers[:]:  # Copy list to avoid modification during iteration
                        try:
                            if q.full():
                                try:
                                    q.get_nowait()  # Drop oldest if full (Backpressure)
                                except:
                                    pass
                            q.put_nowait(envelope)
                        except:
                            # If queue is full or broken, remove it
                            if q in self.subscribers:
                                self.subscribers.remove(q)

            except Exception as e:
                logger.error(f"Error in telemetry bridge loop: {e}")
                time.sleep(1)  # Backoff

        subscriber.close()
        logger.info("Telemetry bridge thread exiting.")

# Global telemetry bridge instance
telemetry_bridge = None

def create_app(test_config=None):
    """Application factory pattern for creating Flask app."""

    # Create app
    template_dir = os.path.join(os.path.dirname(__file__), 'templates')
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    app = Flask(__name__,
                template_folder=template_dir,
                static_folder=static_dir)

    # Initialize Database Manager
    data_root = os.environ.get('DATA_ROOT', 'data')
    db_manager = DatabaseManager(data_root)
    app.db_manager = db_manager # Attach to app instance

    # Initialize Telemetry Bridge
    zmq_config = load_zmq_config()
    global telemetry_bridge
    telemetry_bridge = TelemetryBridge(
        host=zmq_config["host"],
        port=zmq_config["ports"]["telemetry_pub"]
    )
    telemetry_bridge.start()
    
    # Ensure cleanup on exit
    import atexit
    atexit.register(telemetry_bridge.stop)
    
    # Store reference in app for cleanup
    app.telemetry_bridge = telemetry_bridge

    # Configuration
    app.config.from_mapping(
        SECRET_KEY=os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production'),
        DATA_ROOT=data_root
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

    from flask_app.blueprints.dashboard import dashboard_bp
    app.register_blueprint(dashboard_bp)

    from flask_app.blueprints.database import database_bp
    app.register_blueprint(database_bp)

    from flask_app.blueprints.backtest import backtest_bp
    app.register_blueprint(backtest_bp, url_prefix='/backtest')

    from flask_app.blueprints.scanner import scanner_bp
    app.register_blueprint(scanner_bp, url_prefix='/scanner')

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

    # Health check endpoint
    @app.route('/health')
    def health_check():
        return {'status': 'healthy', 'version': '1.0.0 (Isolated Architecture)'}

    # Root redirect to login
    @app.route('/')
    def index():
        from flask import redirect, url_for
        return redirect(url_for('auth.login'))

    # SSE endpoint for telemetry streaming
    @app.route('/api/telemetry/stream')
    def telemetry_stream():
        def event_stream():
            # Subscribe to the telemetry bridge
            queue = telemetry_bridge.subscribe()
            try:
                while True:
                    try:
                        # Get telemetry message from queue with timeout
                        message = queue.get(timeout=1.0)
                        if message:
                            # Send as Server-Sent Event
                            yield f"data: {json.dumps(message)}\n\n"
                    except Empty:
                        # Timeout - continue loop to check if client disconnected
                        continue
            except GeneratorExit:
                # Client disconnected
                pass
            finally:
                # Unsubscribe when client disconnects
                telemetry_bridge.unsubscribe(queue)

        return Response(event_stream(), mimetype="text/event-stream")

    return app

from config.settings import load_zmq_config
