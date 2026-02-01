"""
Run the Flask development server.
"""
import sys
import os
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from flask_app import create_app
from scripts.init_db import bootstrap

if __name__ == '__main__':
    # Ensure database is initialized/migrated
    bootstrap()
    
    app = create_app()
    
    # Development server configuration
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'
    
    print(f"Starting Flask server on http://{host}:{port}")
    print(f"Debug mode: {debug}")
    print("\nAvailable routes:")
    print(f"  - http://{host}:{port}/         (redirects to login)")
    print(f"  - http://{host}:{port}/login    (authentication)")
    print(f"  - http://{host}:{port}/health   (health check)")
    print(f"  - http://{host}:{port}/dashboard/ (requires login)")
    
    app.run(host=host, port=port, debug=debug)
