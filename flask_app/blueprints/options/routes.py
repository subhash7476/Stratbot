# flask_app/blueprints/options/routes.py
"""
Options Analyzer Routes
=======================

Handles:
- Option chain display
- Greeks calculation
- Strategy analysis
- Position management
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import render_template, jsonify, request
from . import bp
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def get_db():
    """Get database connection"""
    try:
        from core.database import get_db as get_trading_db
        return get_trading_db()
    except Exception as e:
        logger.error(f"Database error: {e}")
        return None


@bp.route('/')
def index():
    """Options analyzer page"""
    # Get list of available underlyings
    underlyings = get_option_underlyings()
    return render_template('options/index.html', underlyings=underlyings)


def get_option_underlyings():
    """Get list of underlyings with options"""
    db = get_db()
    if not db:
        return []

    try:
        rows = db.safe_query(
            """
            SELECT DISTINCT symbol
            FROM fo_stocks_master
            WHERE is_active = TRUE
            ORDER BY symbol
            """,
            fetch='all'
        )
        return [row[0] for row in rows] if rows else []
    except Exception as e:
        logger.error(f"Error getting underlyings: {e}")
        return []


@bp.route('/chain/<symbol>')
def option_chain(symbol):
    """Get option chain for a symbol"""
    try:
        from core.options import get_option_chain

        expiry = request.args.get('expiry')
        chain = get_option_chain(symbol, expiry)

        if chain is None:
            return jsonify({'success': False, 'error': 'Could not fetch option chain'})

        return jsonify({
            'success': True,
            'symbol': symbol,
            'expiry': expiry,
            'chain': chain
        })

    except ImportError:
        return jsonify({'success': False, 'error': 'Options module not available'})
    except Exception as e:
        logger.error(f"Error getting option chain for {symbol}: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/expiries/<symbol>')
def expiries(symbol):
    """Get available expiry dates for a symbol"""
    try:
        from core.options import get_expiry_dates

        dates = get_expiry_dates(symbol)
        return jsonify({
            'success': True,
            'symbol': symbol,
            'expiries': dates
        })

    except ImportError:
        return jsonify({'success': False, 'error': 'Options module not available'})
    except Exception as e:
        logger.error(f"Error getting expiries for {symbol}: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/greeks', methods=['POST'])
def calculate_greeks():
    """Calculate option Greeks"""
    try:
        data = request.json

        spot = float(data.get('spot', 0))
        strike = float(data.get('strike', 0))
        expiry_days = int(data.get('expiry_days', 30))
        iv = float(data.get('iv', 20)) / 100
        option_type = data.get('option_type', 'CE')
        risk_free = float(data.get('risk_free', 6)) / 100

        from core.options.greeks import calculate_greeks

        greeks = calculate_greeks(
            spot=spot,
            strike=strike,
            time_to_expiry=expiry_days / 365,
            volatility=iv,
            risk_free_rate=risk_free,
            option_type=option_type
        )

        return jsonify({
            'success': True,
            'greeks': greeks
        })

    except ImportError:
        return jsonify({'success': False, 'error': 'Greeks module not available'})
    except Exception as e:
        logger.error(f"Error calculating Greeks: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/straddle/<symbol>')
def straddle_analysis(symbol):
    """Get straddle analysis for ATM"""
    try:
        from core.options import get_atm_straddle

        expiry = request.args.get('expiry')
        straddle = get_atm_straddle(symbol, expiry)

        if straddle is None:
            return jsonify({'success': False, 'error': 'Could not fetch straddle data'})

        return jsonify({
            'success': True,
            'symbol': symbol,
            'straddle': straddle
        })

    except ImportError:
        return jsonify({'success': False, 'error': 'Options module not available'})
    except Exception as e:
        logger.error(f"Error getting straddle for {symbol}: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/iv-skew/<symbol>')
def iv_skew(symbol):
    """Get IV skew data"""
    try:
        from core.options import get_iv_skew

        expiry = request.args.get('expiry')
        skew = get_iv_skew(symbol, expiry)

        if skew is None:
            return jsonify({'success': False, 'error': 'Could not calculate IV skew'})

        return jsonify({
            'success': True,
            'symbol': symbol,
            'skew': skew
        })

    except ImportError:
        return jsonify({'success': False, 'error': 'Options module not available'})
    except Exception as e:
        logger.error(f"Error getting IV skew for {symbol}: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/positions')
def positions():
    """Get saved option positions"""
    db = get_db()
    if not db:
        return jsonify({'success': False, 'error': 'Database not available'})

    try:
        rows = db.safe_query(
            """
            SELECT id, symbol, strike, expiry, option_type, quantity,
                   entry_price, current_price, pnl, created_at
            FROM option_positions
            ORDER BY created_at DESC
            """,
            fetch='all'
        )

        positions = []
        if rows:
            for row in rows:
                positions.append({
                    'id': row[0],
                    'symbol': row[1],
                    'strike': row[2],
                    'expiry': row[3].isoformat() if row[3] else None,
                    'option_type': row[4],
                    'quantity': row[5],
                    'entry_price': row[6],
                    'current_price': row[7],
                    'pnl': row[8],
                    'created_at': row[9].isoformat() if row[9] else None
                })

        return jsonify({'success': True, 'positions': positions})

    except Exception as e:
        logger.error(f"Error getting positions: {e}")
        return jsonify({'success': False, 'error': str(e)})
