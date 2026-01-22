# flask_app/blueprints/scanner/__init__.py
"""Scanner Blueprint"""

from flask import Blueprint

bp = Blueprint('scanner', __name__)

from . import routes
