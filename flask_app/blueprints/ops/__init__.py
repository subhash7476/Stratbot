from flask import Blueprint

bp = Blueprint('ops', __name__)

from . import routes
