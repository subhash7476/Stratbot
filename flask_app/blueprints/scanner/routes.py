# flask_app/blueprints/scanner/routes.py
"""
Scanner Routes
==============

Handles:
- Running market scans
- Signal generation
- Live signal monitoring
- WebSocket real-time updates
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import render_template, jsonify, request
from flask_socketio import emit
from flask_app.extensions import socketio
from . import bp
import logging
from datetime import datetime
import uuid
import threading

logger = logging.getLogger(__name__)

# Store active scans
active_scans = {}
scan_lock = threading.Lock()


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
    """Scanner main page"""
    stats = get_scanner_stats()
    return render_template('scanner/index.html', stats=stats)


def get_scanner_stats():
    """Get scanner statistics"""
    stats = {
        'active_signals': 0,
        'triggered_today': 0,
        'total_scans': 0,
        'last_scan': None,
        'instruments_count': 0
    }

    db = get_db()
    if not db:
        return stats

    try:
        # Active signals (case-insensitive comparison)
        result = db.safe_query(
            "SELECT COUNT(*) FROM unified_signals WHERE UPPER(status) = 'ACTIVE'",
            fetch='one'
        )
        stats['active_signals'] = result[0] if result else 0

        # Triggered today (use updated_at since triggered_at doesn't exist)
        result = db.safe_query(
            """
            SELECT COUNT(*) FROM unified_signals
            WHERE UPPER(status) = 'TRIGGERED'
            AND DATE(updated_at) = CURRENT_DATE
            """,
            fetch='one'
        )
        stats['triggered_today'] = result[0] if result else 0

        # Instruments count
        result = db.safe_query(
            "SELECT COUNT(*) FROM fo_stocks_master WHERE is_active = TRUE",
            fetch='one'
        )
        stats['instruments_count'] = result[0] if result else 0

    except Exception as e:
        logger.error(f"Error getting scanner stats: {e}")

    return stats


@bp.route('/signals')
def signals():
    """Get active signals"""
    db = get_db()
    if not db:
        return jsonify({'success': False, 'error': 'Database not available'})

    try:
        status_filter = request.args.get('status', 'active')

        rows = db.safe_query(
            """
            SELECT signal_id, symbol, instrument_key, signal_type, status,
                   entry_price, sl_price, tp_price, score, confidence,
                   strategy, timeframe, timestamp, created_at, updated_at,
                   reasons, metadata
            FROM unified_signals
            WHERE UPPER(status) = UPPER(?)
            ORDER BY created_at DESC
            LIMIT 100
            """,
            params=[status_filter],
            fetch='all'
        )

        signals = []
        if rows:
            for row in rows:
                signals.append({
                    'signal_id': row[0],
                    'symbol': row[1],
                    'instrument_key': row[2],
                    'signal_type': row[3],
                    'status': row[4],
                    'entry_price': float(row[5]) if row[5] else None,
                    'sl_price': float(row[6]) if row[6] else None,
                    'tp_price': float(row[7]) if row[7] else None,
                    'score': float(row[8]) if row[8] else None,
                    'confidence': float(row[9]) if row[9] else None,
                    'strategy': row[10],
                    'timeframe': row[11],
                    'timestamp': row[12].isoformat() if row[12] else None,
                    'created_at': row[13].isoformat() if row[13] else None,
                    'updated_at': row[14].isoformat() if row[14] else None,
                    'reasons': row[15],
                    'metadata': row[16]
                })

        return jsonify({'success': True, 'signals': signals})

    except Exception as e:
        logger.error(f"Error getting signals: {e}")
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/start-scan', methods=['POST'])
def start_scan():
    """Start a new scan"""
    try:
        scan_id = str(uuid.uuid4())[:8]
        config = request.json or {}

        # Store scan state
        with scan_lock:
            active_scans[scan_id] = {
                'status': 'pending',
                'started_at': datetime.now(),
                'config': config,
                'progress': {'current': 0, 'total': 0, 'phase': 'initializing'}
            }

        # Run scan in background thread
        thread = threading.Thread(
            target=run_scan_background,
            args=(scan_id, config),
            daemon=True
        )
        thread.start()

        return jsonify({
            'success': True,
            'scan_id': scan_id,
            'message': 'Scan started'
        })

    except Exception as e:
        logger.error(f"Error starting scan: {e}")
        return jsonify({'success': False, 'error': str(e)})


def run_scan_background(scan_id: str, config: dict):
    """Run scan in background thread"""
    try:
        with scan_lock:
            active_scans[scan_id]['status'] = 'running'

        # Import scanner
        from core.scanner import MultiStockScanner

        # Initialize scanner with database path
        db_path = config.get('db_path', 'data/trading_bot.duckdb')
        scanner = MultiStockScanner(db_path=db_path)

        # Get market data (this should be provided or fetched)
        # For now, use empty dict - the scanner will need market data to work
        market_data = config.get('market_data', {})

        # Emit initial progress
        total_stocks = len(scanner.stock_universe)
        socketio.emit('scan_progress', {
            'scan_id': scan_id,
            'current': 0,
            'total': total_stocks,
            'symbol': '',
            'phase': 'Starting scan...'
        }, namespace='/scanner')

        with scan_lock:
            if scan_id in active_scans:
                active_scans[scan_id]['progress'] = {
                    'current': 0,
                    'total': total_stocks,
                    'symbol': '',
                    'phase': 'Starting scan...',
                    'percent': 0
                }

        # Run parallel scan
        max_workers = config.get('max_workers', 10)
        signals = scanner.scan_all_stocks_parallel(market_data, max_workers=max_workers)

        # Build results in expected format
        results = {
            'signals': signals,
            'stats': scanner.get_stats(),
            'total_scanned': total_stocks,
            'signals_found': len(signals)
        }

        # Mark complete
        with scan_lock:
            if scan_id in active_scans:
                active_scans[scan_id]['status'] = 'completed'
                active_scans[scan_id]['results'] = results
                active_scans[scan_id]['completed_at'] = datetime.now()
                active_scans[scan_id]['progress'] = {
                    'current': total_stocks,
                    'total': total_stocks,
                    'symbol': '',
                    'phase': 'Complete',
                    'percent': 100
                }

        socketio.emit('scan_complete', {
            'scan_id': scan_id,
            'signals_found': len(signals)
        }, namespace='/scanner')

    except Exception as e:
        logger.error(f"Scan {scan_id} failed: {e}")
        with scan_lock:
            if scan_id in active_scans:
                active_scans[scan_id]['status'] = 'failed'
                active_scans[scan_id]['error'] = str(e)

        socketio.emit('scan_error', {
            'scan_id': scan_id,
            'error': str(e)
        }, namespace='/scanner')


@bp.route('/scan-status/<scan_id>')
def scan_status(scan_id):
    """Get scan status"""
    with scan_lock:
        if scan_id not in active_scans:
            return jsonify({'success': False, 'error': 'Scan not found'}), 404

        scan = active_scans[scan_id]
        return jsonify({
            'success': True,
            'scan_id': scan_id,
            'status': scan['status'],
            'progress': scan.get('progress', {}),
            'started_at': scan['started_at'].isoformat(),
            'error': scan.get('error')
        })


@bp.route('/scan-results/<scan_id>')
def scan_results(scan_id):
    """Get scan results"""
    with scan_lock:
        if scan_id not in active_scans:
            return jsonify({'success': False, 'error': 'Scan not found'}), 404

        scan = active_scans[scan_id]
        if scan['status'] != 'completed':
            return jsonify({'success': False, 'error': 'Scan not completed'})

        return jsonify({
            'success': True,
            'scan_id': scan_id,
            'results': scan.get('results', {})
        })


@bp.route('/cancel-scan/<scan_id>', methods=['POST'])
def cancel_scan(scan_id):
    """Cancel a running scan"""
    with scan_lock:
        if scan_id in active_scans:
            active_scans[scan_id]['status'] = 'cancelled'
            return jsonify({'success': True, 'message': 'Scan cancelled'})

    return jsonify({'success': False, 'error': 'Scan not found'})


@bp.route('/clear-signals', methods=['POST'])
def clear_signals():
    """Clear signals"""
    db = get_db()
    if not db:
        return jsonify({'success': False, 'error': 'Database not available'})

    try:
        status_filter = request.json.get('status') if request.is_json else None

        if status_filter:
            db.safe_query(
                "DELETE FROM unified_signals WHERE UPPER(status) = UPPER(?)",
                params=[status_filter]
            )
        else:
            db.safe_query("DELETE FROM unified_signals")

        return jsonify({'success': True, 'message': 'Signals cleared'})

    except Exception as e:
        logger.error(f"Error clearing signals: {e}")
        return jsonify({'success': False, 'error': str(e)})


# SocketIO events for scanner namespace
@socketio.on('connect', namespace='/scanner')
def scanner_connect():
    """Client connected to scanner namespace"""
    logger.info("Client connected to scanner namespace")
    emit('connected', {'status': 'ok'})


@socketio.on('subscribe_signals', namespace='/scanner')
def subscribe_signals():
    """Subscribe to signal updates"""
    # Could join a room for signal updates
    emit('subscribed', {'channel': 'signals'})
