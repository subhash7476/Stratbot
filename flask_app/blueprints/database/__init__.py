# flask_app/blueprints/database/__init__.py
"""Database Viewer Blueprint"""

from flask import Blueprint

bp = Blueprint('database', __name__)

from . import routes
