# flask_app/blueprints/options/__init__.py
"""Options Analyzer Blueprint"""

from flask import Blueprint

bp = Blueprint('options', __name__)

from . import routes
