from flask import Blueprint, render_template, jsonify, request, current_app
from pathlib import Path
import json
import uuid
import threading
import pandas as pd
from datetime import datetime

from flask_app.middleware import login_required
from core.database.manager import DatabaseManager
from app_facade.backtest_facade import BacktestFacade
from core.strategies.registry import get_available_strategies
from core.logging import setup_logger

logger = setup_logger("backtest_bp")

backtest_bp = Blueprint('backtest', __name__, url_prefix='/backtest')

def get_db_manager():
    return getattr(current_app, 'db_manager', None) or DatabaseManager(Path("data"))

def get_facade():
    return BacktestFacade(get_db_manager())

@backtest_bp.route('/')
@login_required
def index():
    """Main backtest page."""
    return render_template('backtest/index.html')

@backtest_bp.route('/api/runs')
@login_required
def get_runs():
    """Returns list of past backtest runs from index DB."""
    try:
        runs = get_facade().get_all_runs()
        
        # Custom serialization to handle datetime objects
        def clean_row(row):
            for k, v in row.items():
                if hasattr(v, 'isoformat'):
                    row[k] = v.isoformat()
                elif pd.isna(v):
                    row[k] = None
            return row

        clean_runs = [clean_row(r) for r in runs]
        return jsonify({"success": True, "runs": clean_runs})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@backtest_bp.route('/api/runs/<run_id>/trades')
@login_required
def get_run_trades(run_id):
    """Returns list of trades for a specific backtest run from its DuckDB file."""
    try:
        trades = get_facade().get_run_trades(run_id)
        
        def clean_row(row):
            for k, v in row.items():
                if hasattr(v, 'isoformat'):
                    row[k] = v.isoformat()
                elif pd.isna(v):
                    row[k] = None
            return row

        clean_trades = [clean_row(t) for t in trades]
        return jsonify({"success": True, "trades": clean_trades})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@backtest_bp.route('/api/strategies')
@login_required
def get_strategies():
    """Returns list of available strategies."""
    strategies = get_available_strategies()
    strategy_names = {
        "ehma_pivot": "EHMA Pivot Crossover",
        "confluence_consumer": "Confluence Engine",
        "regime_v2": "Daily Regime V2",
        "regime_adaptive": "Regime Adaptive",
        "premium_tp_sl": "Premium TP/SL Strategy"
    }

    result = []
    for strat_id in strategies:
        result.append({
            "id": strat_id,
            "name": strategy_names.get(strat_id, strat_id.title().replace('_', ' '))
        })

    return jsonify({"success": True, "strategies": result})

@backtest_bp.route('/api/symbols')
@login_required
def get_symbols():
    """Returns list of tradable symbols from fo_stocks, indices, and MCX."""
    try:
        db = get_db_manager()
        with db.config_reader() as conn:
            # 1. FO Stocks
            df_fo = pd.read_sql_query("SELECT instrument_key as value, trading_symbol || ' [FO]' as label FROM fo_stocks WHERE is_active = 1", conn)
            
            # 2. NSE Indices
            df_idx = pd.read_sql_query("SELECT instrument_key as value, trading_symbol || ' [IDX]' as label FROM instrument_meta WHERE instrument_key LIKE 'NSE_INDEX%' AND is_active = 1", conn)
            
            # 3. MCX
            df_mcx = pd.read_sql_query("SELECT instrument_key as value, trading_symbol || ' [MCX]' as label FROM instrument_meta WHERE exchange = 'MCX' AND is_active = 1", conn)
            
            # Combine and sort
            df = pd.concat([df_fo, df_idx, df_mcx]).drop_duplicates(subset=['value'])
            df = df.sort_values('label')
            
            return jsonify({"success": True, "symbols": df.to_dict(orient='records')})
    except Exception as e:
        logger.error(f"Error fetching symbols: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@backtest_bp.route('/api/run', methods=['POST'])
@login_required
def run_backtest():
    """Initiate a backtest run."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Missing JSON data"}), 400
            
        required_fields = ['strategy_id', 'symbol', 'start_date', 'end_date']
        for field in required_fields:
            if not data.get(field):
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400
        
        # The symbol sent from UI is now the instrument_key
        instrument_key = data['symbol']
        db_manager = get_db_manager()
        
        # Resolve trading_symbol for display purposes
        with db_manager.config_reader() as conn:
            res = conn.execute("SELECT trading_symbol || ' [FO]' FROM fo_stocks WHERE instrument_key = ?", [instrument_key]).fetchone()
            if not res:
                res = conn.execute("SELECT trading_symbol || ' [IDX]' FROM instrument_meta WHERE instrument_key LIKE 'NSE_INDEX%' AND instrument_key = ?", [instrument_key]).fetchone()
            if not res:
                res = conn.execute("SELECT trading_symbol || ' [MCX]' FROM instrument_meta WHERE exchange = 'MCX' AND instrument_key = ?", [instrument_key]).fetchone()
            
            trading_symbol = res[0] if res else instrument_key

        run_id = str(uuid.uuid4())
        
        # Extract strategy parameters from request
        strategy_params = {
            'tp_pct': data.get('tp_pct'),
            'sl_pct': data.get('sl_pct'),
            'max_hold_bars': data.get('max_hold_bars'),
            'capital': data.get('capital', 100000),
            'timeframe': data.get('timeframe', '1m'),
        }

        # 1. Record run in index DB as PENDING (store the trading symbol for display purposes)
        with db_manager.backtest_index_writer() as conn:
            conn.execute("""
                INSERT INTO backtest_runs (run_id, strategy_id, symbol, start_date, end_date, params, status)
                VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
            """, [
                run_id, data['strategy_id'], trading_symbol,  # Store trading symbol for display
                data['start_date'], data['end_date'], json.dumps(strategy_params)
            ])

        logger.info(f"Initiating backtest: {data['strategy_id']} on {trading_symbol} ({data['start_date']} to {data['end_date']}) - ID: {run_id}")

        # 2. Launch background thread for execution
        def execute_task():
            try:
                from core.backtest.runner import BacktestRunner
                runner = BacktestRunner(db_manager)

                start_time = datetime.strptime(data['start_date'], '%Y-%m-%d')
                end_time = datetime.strptime(data['end_date'], '%Y-%m-%d')

                logger.info(f"Background backtest task started for ID: {run_id}")
                runner.run(
                    strategy_id=data['strategy_id'],
                    symbol=instrument_key,  # Use instrument key for processing
                    start_time=start_time,
                    end_time=end_time,
                    initial_capital=float(data.get('capital', 100000)),
                    strategy_params=strategy_params,
                    timeframe=data.get('timeframe', '1m'),
                    run_id=run_id
                )
                logger.info(f"Background backtest task finished successfully for ID: {run_id}")
            except Exception as e:
                logger.error(f"Background backtest task error for ID {run_id}: {e}")

        threading.Thread(target=execute_task, daemon=True).start()

        return jsonify({"success": True, "run_id": run_id, "message": "Backtest started"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ─── Scanner Endpoints ──────────────────────────────────────

# Active scan tracking (in-memory, per-process)
_active_scans = {}

@backtest_bp.route('/api/scanner/start', methods=['POST'])
@login_required
def start_scan():
    """Launch a symbol scan in a background thread."""
    try:
        data = request.get_json() or {}

        db_manager = get_db_manager()
        from core.backtest.symbol_scanner import SymbolScanner
        from core.backtest.scan_persistence import ScanPersistence

        scanner = SymbolScanner(db_manager)

        # Build symbol list
        specific_symbols = data.get('symbols')  # Optional list of instrument_keys
        if specific_symbols:
            symbols = [{"instrument_key": s, "trading_symbol": s} for s in specific_symbols]
        else:
            symbols = scanner.get_all_equity_symbols()

        limit = data.get('limit', 0)
        if limit > 0:
            symbols = symbols[:limit]

        timeframe = data.get('timeframe', '15m')
        capital = float(data.get('capital', 100000))
        train_start = datetime.strptime(data.get('train_start', '2024-10-17'), '%Y-%m-%d')
        train_end = datetime.strptime(data.get('train_end', '2025-05-31'), '%Y-%m-%d')
        test_start = datetime.strptime(data.get('test_start', '2025-06-01'), '%Y-%m-%d')
        test_end = datetime.strptime(data.get('test_end', '2025-12-31'), '%Y-%m-%d')

        # Progress tracking
        scan_progress = {"current": 0, "total": len(symbols), "current_symbol": "", "status": "starting"}

        def progress_cb(current, total, symbol, status):
            scan_progress["current"] = current
            scan_progress["total"] = total
            scan_progress["current_symbol"] = symbol
            scan_progress["status"] = status

        scan_id_holder = [None]

        def execute_scan():
            try:
                scan = scanner.scan_all_symbols(
                    symbols=symbols,
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                    initial_capital=capital,
                    timeframe=timeframe,
                    progress_callback=progress_cb,
                )
                scan_id_holder[0] = scan.scan_id

                # Persist results
                persistence = ScanPersistence(db_manager)
                persistence.save_scan(scan)
                scan_progress["status"] = "completed"
                scan_progress["scan_id"] = scan.scan_id

                logger.info(f"Scan {scan.scan_id} completed: {scan.profitable_symbols}/{scan.total_symbols} profitable")
            except Exception as e:
                scan_progress["status"] = f"failed: {str(e)[:200]}"
                logger.error(f"Scan failed: {e}", exc_info=True)

        # Track progress in memory
        progress_id = str(uuid.uuid4())[:8]
        _active_scans[progress_id] = scan_progress

        threading.Thread(target=execute_scan, daemon=True).start()

        return jsonify({
            "success": True,
            "progress_id": progress_id,
            "total_symbols": len(symbols),
            "message": f"Scan started for {len(symbols)} symbols",
        })
    except Exception as e:
        logger.error(f"Failed to start scan: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@backtest_bp.route('/api/scanner/progress/<progress_id>')
@login_required
def get_scan_progress(progress_id):
    """Poll scan progress."""
    progress = _active_scans.get(progress_id)
    if not progress:
        return jsonify({"success": False, "error": "Scan not found"}), 404
    return jsonify({"success": True, **progress})


@backtest_bp.route('/api/scanner/results')
@login_required
def get_scan_results_list():
    """Return all completed scan summaries."""
    try:
        from app_facade.scanner_facade import ScannerFacade
        facade = ScannerFacade(get_db_manager())
        scans = facade.get_all_scans()
        return jsonify({"success": True, "scans": scans})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@backtest_bp.route('/api/scanner/results/<scan_id>')
@login_required
def get_scan_detail(scan_id):
    """Return detailed results for a specific scan."""
    try:
        from app_facade.scanner_facade import ScannerFacade
        facade = ScannerFacade(get_db_manager())
        results = facade.get_scan_results(scan_id)
        if not results:
            return jsonify({"success": False, "error": "Scan not found"}), 404
        return jsonify({"success": True, **results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@backtest_bp.route('/api/scanner/profitable')
@login_required
def get_profitable_symbols():
    """Return profitable symbols from latest scan."""
    try:
        from app_facade.scanner_facade import ScannerFacade
        facade = ScannerFacade(get_db_manager())
        scan_id = request.args.get('scan_id')
        symbols = facade.get_profitable_symbols(scan_id)
        return jsonify({"success": True, "symbols": symbols})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── Existing Endpoints ────────────────────────────────────

@backtest_bp.route('/api/runs/<run_id>', methods=['DELETE'])
@login_required
def delete_run(run_id):
    """Delete a backtest run."""
    try:
        db_manager = get_db_manager()
        with db_manager.backtest_index_writer() as conn:
            conn.execute("DELETE FROM backtest_runs WHERE run_id = ?", [run_id])

        # Also try to delete the DuckDB file if it exists
        runs_path = db_manager.data_root / 'backtest' / 'runs' / f"{run_id}.duckdb"
        if runs_path.exists():
            runs_path.unlink()

        return jsonify({"success": True, "message": "Run deleted"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@backtest_bp.route('/api/portfolio/run', methods=['POST'])
@login_required
def run_portfolio_backtest():
    """Launch a portfolio backtest in a background thread."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Missing JSON data"}), 400

        required_fields = ['symbols', 'start_date', 'end_date']
        for field in required_fields:
            if not data.get(field):
                return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

        symbols = data['symbols']
        start_date = data['start_date']
        end_date = data['end_date']

        db_manager = get_db_manager()

        # Extract parameters
        total_capital = float(data.get('total_capital', 500000.0))
        timeframe = data.get('timeframe', '15m')
        allocation_method = data.get('allocation_method', 'equal_weight')
        max_concurrent_positions = int(data.get('max_concurrent_positions', 5))
        max_correlation = float(data.get('max_correlation', 0.7))

        run_id = str(uuid.uuid4())

        # 1. Record run in index DB as PENDING
        with db_manager.backtest_index_writer() as conn:
            conn.execute("""
                INSERT INTO backtest_runs (run_id, strategy_id, symbol, start_date, end_date, total_pnl, max_drawdown, 
                 sharpe_ratio, win_rate, total_trades, status, created_at, params)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
            """, [
                run_id,
                f"portfolio_{allocation_method}",
                json.dumps([s["trading_symbol"] for s in symbols]),  # Using symbol field to store list of symbols
                start_date,
                end_date,
                0.0,  # Will be updated after completion
                0.0,  # Will be updated after completion
                0.0,  # Will be updated after completion
                0.0,  # Will be updated after completion
                0,    # Will be updated after completion
                datetime.now(),
                json.dumps({
                    "total_capital": total_capital,
                    "allocation_method": allocation_method,
                    "max_concurrent_positions": max_concurrent_positions,
                    "max_correlation": max_correlation,
                    "timeframe": timeframe
                })
            ])

        logger.info(f"Initiating portfolio backtest: {allocation_method} on {len(symbols)} symbols ({start_date} to {end_date}) - ID: {run_id}")

        # 2. Launch background thread for execution
        def execute_task():
            try:
                from core.backtest.portfolio_backtest import PortfolioBacktestRunner
                runner = PortfolioBacktestRunner(db_manager)

                start_time = datetime.strptime(start_date, '%Y-%m-%d')
                end_time = datetime.strptime(end_date, '%Y-%m-%d')

                logger.info(f"Background portfolio backtest task started for ID: {run_id}")
                
                # Run portfolio backtest
                runner.run(
                    symbols=symbols,
                    start_time=start_time,
                    end_time=end_time,
                    total_capital=total_capital,
                    timeframe=timeframe,
                    allocation_method=allocation_method,
                    max_concurrent_positions=max_concurrent_positions,
                    max_correlation=max_correlation,
                    run_id=run_id
                )
                logger.info(f"Background portfolio backtest task finished successfully for ID: {run_id}")
            except Exception as e:
                logger.error(f"Background portfolio backtest task error for ID {run_id}: {e}")
                # Update status to failed
                try:
                    with db_manager.backtest_index_writer() as conn:
                        conn.execute("""
                            UPDATE backtest_runs SET status = 'FAILED', error_message = ?
                            WHERE run_id = ?
                        """, [str(e), run_id])
                except Exception as update_error:
                    logger.error(f"Failed to update run status to FAILED: {update_error}")

        threading.Thread(target=execute_task, daemon=True).start()

        return jsonify({"success": True, "run_id": run_id, "message": "Portfolio backtest started"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@backtest_bp.route('/api/portfolio/<run_id>/metrics')
@login_required
def get_portfolio_metrics(run_id):
    """Return portfolio-level metrics from backtest index + per-symbol breakdown."""
    try:
        db_manager = get_db_manager()

        with db_manager.backtest_index_reader() as conn:
            row = conn.execute("""
                SELECT total_pnl, max_drawdown, sharpe_ratio, win_rate,
                       total_trades, status, params
                FROM backtest_runs WHERE run_id = ?
            """, [run_id]).fetchone()

        if not row:
            return jsonify({"success": False, "error": "Portfolio run not found"}), 404

        params = json.loads(row[6]) if row[6] else {}

        return jsonify({
            "success": True,
            "metrics": {
                "total_pnl": float(row[0] or 0),
                "max_drawdown": float(row[1] or 0),
                "sharpe_ratio": float(row[2] or 0),
                "win_rate": float(row[3] or 0),
                "total_trades": int(row[4] or 0),
                "status": row[5],
                "per_symbol": params.get("per_symbol_metrics", {}),
                "allocation_method": params.get("allocation_method", ""),
                "per_symbol_errors": params.get("per_symbol_errors", {}),
            }
        })
    except Exception as e:
        logger.error(f"Error getting portfolio metrics: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
