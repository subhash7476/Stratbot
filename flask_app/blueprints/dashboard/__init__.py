# flask_app/blueprints/dashboard/__init__.py
"""Dashboard Blueprint - Main landing page"""

from flask import Blueprint
bp = Blueprint('dashboard', __name__, template_folder='../../templates/dashboard')
from . import routes
