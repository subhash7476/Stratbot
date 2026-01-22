#!/usr/bin/env python
# flask_app/run.py
"""
Flask Application Entry Point
=============================

Run this script to start the Flask application.

Usage:
    python flask_app/run.py              # Development mode
    python flask_app/run.py --port 8080  # Custom port
    python flask_app/run.py --prod       # Production mode (use gunicorn instead)
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(description="Run the Trading Bot Flask application")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--prod", action="store_true", help="Production mode (disables debug)")

    args = parser.parse_args()

    # Import here to avoid issues before path setup
    from flask_app.app import create_app
    from flask_app.extensions import socketio

    # Create app
    app = create_app()

    # Override debug based on args
    if args.prod:
        app.debug = False
    elif args.debug:
        app.debug = True

    print(f"""
================================================================
          Trading Bot Pro - Flask App
================================================================
  Server:     http://{args.host}:{args.port}
  Mode:       {'Production' if args.prod else 'Development'}
================================================================
    """)

    # Run with SocketIO
    socketio.run(
        app,
        host=args.host,
        port=args.port,
        debug=app.debug,
        use_reloader=app.debug,
        log_output=True,
        allow_unsafe_werkzeug=True
    )


if __name__ == "__main__":
    main()
