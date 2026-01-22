# flask_app/blueprints/auth/__init__.py
"""
Authentication Blueprint
========================

Handles Upstox OAuth login, token management, and instrument downloads.
Equivalent to Streamlit Page 1: Login & Instruments.
"""

from flask import Blueprint

bp = Blueprint('auth', __name__, template_folder='../../templates/auth')

from . import routes
