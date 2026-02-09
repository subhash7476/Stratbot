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
