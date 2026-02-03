from flask import Blueprint, render_template, jsonify, request
from flask_app.middleware import login_required
from core.data.duckdb_client import db_cursor
from core.strategies.registry import get_available_strategies

backtest_bp = Blueprint('backtest', __name__)

@backtest_bp.route('/')
@login_required
def index():
    """Main backtest page."""
    return render_template('backtest/index.html')

@backtest_bp.route('/api/runs')
@login_required
def get_runs():
    """Returns list of past backtest runs."""
    with db_cursor(read_only=True) as conn:
        runs = conn.execute("""
            SELECT run_id, strategy_id, symbol, start_date, end_date,
                   total_trades, win_rate, total_pnl, max_drawdown,
                   sharpe_ratio, status, error_message, created_at, params
            FROM backtest_runs ORDER BY created_at DESC
        """).fetchdf()

        # Custom serialization to handle NaT/NaN and other Pandas types
        import pandas as pd
        import numpy as np
        
        def clean_value(v):
            if pd.isna(v): return None
            if hasattr(v, 'isoformat'): return v.isoformat()
            try:
                if isinstance(v, (np.integer, int)): return int(v)
                if isinstance(v, (np.floating, float)): return float(v)
            except:
                pass
            return v

        runs_list = [
            {k: clean_value(v) for k, v in row.items()} 
            for row in runs.to_dict(orient='records')
        ]

        return jsonify({"success": True, "runs": runs_list})



@backtest_bp.route('/api/runs/<run_id>/trades')
@login_required
def get_run_trades(run_id):
    """Returns list of trades for a specific backtest run."""
    with db_cursor(read_only=True) as conn:
        trades = conn.execute("""
            SELECT symbol, entry_ts, exit_ts, direction, entry_price, exit_price, qty, pnl, fees, metadata
            FROM backtest_trades 
            WHERE run_id = ?
            ORDER BY entry_ts ASC
        """, [run_id]).fetchdf()
        
        # Custom serialization to handle NaT/NaN and other Pandas types
        import pandas as pd
        import numpy as np
        
        def clean_value(v):
            if pd.isna(v): return None
            if hasattr(v, 'isoformat'): return v.isoformat()
            try:
                if isinstance(v, (np.integer, int)): return int(v)
                if isinstance(v, (np.floating, float)): return float(v)
            except:
                pass
            return v

        trades_list = [
            {k: clean_value(v) for k, v in row.items()} 
            for row in trades.to_dict(orient='records')
        ]
        
        return jsonify({
            "success": True, 
            "trades": trades_list
        })



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
    """Returns list of tradable symbols from F&O master."""
    with db_cursor(read_only=True) as conn:
        symbols = conn.execute("""
            SELECT trading_symbol, name 
            FROM fo_stocks_master 
            ORDER BY trading_symbol
        """).fetchdf()
        
        return jsonify({
            "success": True, 
            "symbols": symbols.to_dict(orient='records')
        })


@backtest_bp.route('/api/runs/<run_id>', methods=['DELETE'])
@login_required
def delete_run(run_id):
    """Delete a backtest run and its trades."""
    try:
        with db_cursor() as conn:
            conn.execute("DELETE FROM backtest_trades WHERE run_id = ?", [run_id])
            conn.execute("DELETE FROM backtest_equity_curves WHERE run_id = ?", [run_id])
            conn.execute("DELETE FROM backtest_runs WHERE run_id = ?", [run_id])
        return jsonify({"success": True, "message": "Run deleted"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@backtest_bp.route('/api/runs/<run_id>/trades/csv')
@login_required
def export_trades_csv(run_id):
    """Export trades for a run as CSV."""
    from flask import Response
    import csv
    import io

    with db_cursor(read_only=True) as conn:
        trades = conn.execute("""
            SELECT symbol, direction, entry_ts, exit_ts, entry_price, exit_price, qty, pnl, fees
            FROM backtest_trades WHERE run_id = ? ORDER BY entry_ts ASC
        """, [run_id]).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Symbol', 'Direction', 'Entry Time', 'Exit Time', 'Entry Price', 'Exit Price', 'Qty', 'PnL', 'Fees'])
    for row in trades:
        writer.writerow(row)

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=backtest_{run_id[:8]}_trades.csv'}
    )


@backtest_bp.route('/api/run', methods=['POST'])
@login_required
def run_backtest():
    """Run a backtest for the specified strategy and parameters."""
    try:
        data = request.get_json()

        strategy_id = data.get('strategy_id')
        symbol = data.get('symbol')
        capital = data.get('capital', 100000)
        start_date = data.get('start_date')
        end_date = data.get('end_date')

        timeframe = data.get('timeframe', '1m')  # '1m', '5m', '15m', '1h', '1d'

        strategy_params = data.get('params', {})
        tp_pct = float(data.get('tp_pct', 0.006))
        sl_pct = float(data.get('sl_pct', 0.003))
        max_hold_bars = int(data.get('max_hold_bars', 15))

        # Merge UI parameters into strategy params
        strategy_params.update({
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "max_hold_bars": max_hold_bars
        })

        if not all([strategy_id, symbol, start_date, end_date]):
            return jsonify({"success": False, "message": "Missing required parameters"}), 400

        # Resolve symbol to instrument key
        from core.data.symbol_utils import resolve_to_instrument_key
        instrument_key = resolve_to_instrument_key(symbol)
        
        if not instrument_key:
            return jsonify({"success": False, "message": f"Could not resolve symbol '{symbol}' to an instrument key. Please check if the instrument exists."}), 400

        # Import required modules
        from datetime import datetime
        import json
        from core.clock import ReplayClock
        from core.runner import TradingRunner, RunnerConfig
        from core.data.duckdb_market_data_provider import DuckDBMarketDataProvider
        from core.data.duckdb_analytics_provider import DuckDBAnalyticsProvider
        from core.execution.handler import ExecutionHandler, ExecutionConfig, ExecutionMode
        from core.execution.position_tracker import PositionTracker
        from core.brokers.paper_broker import PaperBroker
        from core.strategies.registry import create_strategy
        from core.analytics.populator import AnalyticsPopulator
        import threading
        import uuid

        # Convert date strings to datetime objects
        start_time = datetime.strptime(start_date, '%Y-%m-%d')
        end_time = datetime.strptime(end_date, '%Y-%m-%d')

        # Create a unique run ID
        run_id = str(uuid.uuid4())

        # Update database with run info
        with db_cursor() as conn:
            conn.execute("""
                INSERT INTO backtest_runs
                (run_id, strategy_id, symbol, start_date, end_date, params, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                run_id, strategy_id, symbol, start_time, end_time,
                json.dumps(strategy_params), 'PENDING'
            ])

        # Run backtest in a separate thread
        def run_backtest_thread(strategy_id, symbol, instrument_key, start_time, end_time, run_id, strategy_params, timeframe):
            try:
                # Set status to RUNNING
                with db_cursor() as conn:
                    conn.execute("UPDATE backtest_runs SET status = 'RUNNING' WHERE run_id = ?", [run_id])

                # Phase 2.5: Populate analytics before running the backtest
                populator = AnalyticsPopulator()
                # Use instrument_key for data loading, restricted to backtest range
                populator.update_all([instrument_key], start_date=start_time, end_date=end_time,
                                     timeframe=timeframe)

                # Set up components
                clock = ReplayClock(start_time)
                # Map UI timeframe to table/timeframe params
                tf_map = {'1m': (None, 'ohlcv_1m'), '5m': ('5minute', 'ohlcv_resampled'),
                          '15m': ('15minute', 'ohlcv_resampled'), '1h': ('60minute', 'ohlcv_resampled'),
                          '1d': ('1day', 'ohlcv_resampled')}
                tf_key, tf_table = tf_map.get(timeframe, (None, 'ohlcv_1m'))
                market_data = DuckDBMarketDataProvider(
                    [instrument_key], table_name=tf_table, timeframe=tf_key,
                    start_time=start_time, end_time=end_time
                )
                analytics = DuckDBAnalyticsProvider()
                
                # Pre-load analytics for performance
                if hasattr(analytics, 'pre_load'):
                    analytics.pre_load(instrument_key, start_time, end_time)
                
                # Use the parameters from UI
                strategy = create_strategy(strategy_id, f"{strategy_id}_bt", strategy_params)

                if not strategy:
                    raise ValueError(f"Strategy {strategy_id} not found")

                broker = PaperBroker(clock)
                # Ensure backtest starts with clean peak equity to avoid immediate kill switch
                exec_config = ExecutionConfig(
                    mode=ExecutionMode.PAPER,
                    max_drawdown_limit=0.95 # Relaxed limit for backtesting
                )
                execution = ExecutionHandler(
                    clock, 
                    broker, 
                    exec_config,
                    initial_capital=capital
                )
                
                position_tracker = PositionTracker()

                runner = TradingRunner(
                    config=RunnerConfig(
                        symbols=[instrument_key], 
                        strategy_ids=[strategy.strategy_id],
                        warn_on_missing_analytics=False
                    ),
                    market_data_provider=market_data,
                    analytics_provider=analytics,
                    strategies=[strategy],
                    execution_handler=execution,
                    position_tracker=position_tracker,
                    clock=clock
                )

                stats = runner.run()

                # Get the trade history from execution handler
                history = execution.get_trade_history()
                
                # Group entry/exit pairs for backtest_trades table
                backtest_trades = []
                open_trades = {}  # symbol -> (trade, direction)

                for trade in history:
                    symbol = trade.symbol
                    if symbol not in open_trades:
                        # This is an entry trade
                        direction = "LONG" if trade.direction == "BUY" else "SHORT"
                        open_trades[symbol] = (trade, direction)
                    else:
                        # This is an exit trade — pair with the open entry
                        entry, direction = open_trades.pop(symbol)
                        if direction == "LONG":
                            pnl = (trade.price - entry.price) * entry.quantity
                        else:
                            pnl = (entry.price - trade.price) * entry.quantity
                        fees = entry.fees + trade.fees
                        backtest_trades.append([
                            f"bt_{entry.trade_id}_{trade.trade_id}",
                            run_id,
                            symbol,
                            entry.timestamp,
                            trade.timestamp,
                            direction,
                            entry.price,
                            trade.price,
                            int(entry.quantity),
                            pnl,
                            fees,
                            "{}"
                        ])

                # Calculate metrics
                total_trades = len(backtest_trades)
                wins = len([t for t in backtest_trades if t[9] > 0])
                win_rate = (wins / total_trades) if total_trades > 0 else 0.0
                total_pnl_val = sum([t[9] for t in backtest_trades])
                
                # Get max drawdown from metrics
                max_drawdown = execution.metrics.max_drawdown_pct * 100

                # Update database with results
                with db_cursor() as conn:
                    # 1. Save individual trades
                    if backtest_trades:
                        conn.executemany("""
                            INSERT INTO backtest_trades 
                            (trade_id, run_id, symbol, entry_ts, exit_ts, direction, entry_price, exit_price, qty, pnl, fees, metadata)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, backtest_trades)

                    # 2. Update run summary
                    conn.execute("""
                        UPDATE backtest_runs
                        SET total_trades = ?, win_rate = ?, total_pnl = ?, max_drawdown = ?, status = 'COMPLETED'
                        WHERE run_id = ?
                    """, [total_trades, win_rate, total_pnl_val, max_drawdown, run_id])

            except Exception as e:
                import traceback
                print(f"Backtest error: {traceback.format_exc()}")
                # Update run status to failed
                with db_cursor() as conn:
                    conn.execute("""
                        UPDATE backtest_runs
                        SET status = 'FAILED', error_message = ?
                        WHERE run_id = ?
                    """, [str(e), run_id])

        # Start the backtest in a separate thread
        thread = threading.Thread(
            target=run_backtest_thread, 
            args=(strategy_id, symbol, instrument_key, start_time, end_time, run_id, strategy_params, timeframe)
        )
        thread.daemon = True
        thread.start()

        return jsonify({
            "success": True,
            "message": "Backtest started successfully",
            "run_id": run_id
        })

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
