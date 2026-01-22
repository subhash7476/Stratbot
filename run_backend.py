#!/usr/bin/env python
# run_backend.py
"""
FastAPI Backend Launcher
========================

Run this script to start the FastAPI backend server.

Usage:
    python run_backend.py              # Default: localhost:8000
    python run_backend.py --port 8080  # Custom port
    python run_backend.py --host 0.0.0.0  # Allow external access
    python run_backend.py --reload     # Auto-reload on code changes

The backend provides:
- REST API endpoints at http://localhost:8000/api/
- WebSocket endpoints at ws://localhost:8000/ws/
- API documentation at http://localhost:8000/docs
"""

import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(description="Start the FastAPI backend server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload on code changes")
    parser.add_argument("--workers", type=int, default=1, help="Number of worker processes")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])

    args = parser.parse_args()

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           Trading Bot Pro - FastAPI Backend                 ║
╠══════════════════════════════════════════════════════════════╣
║  Server:     http://{args.host}:{args.port}                         ║
║  API Docs:   http://{args.host}:{args.port}/docs                    ║
║  Health:     http://{args.host}:{args.port}/health                  ║
╚══════════════════════════════════════════════════════════════╝
    """)

    try:
        import uvicorn

        uvicorn.run(
            "backend.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            workers=args.workers if not args.reload else 1,
            log_level=args.log_level,
            access_log=True
        )

    except ImportError:
        print("ERROR: uvicorn not installed!")
        print("Please install it: pip install uvicorn[standard]")
        print("\nOr install all backend dependencies:")
        print("pip install fastapi uvicorn[standard] websockets")
        sys.exit(1)


if __name__ == "__main__":
    main()
