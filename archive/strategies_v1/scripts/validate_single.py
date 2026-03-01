
import os
import sys
import logging
import json
import pandas as pd
import duckdb
import argparse
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database.manager import DatabaseManager
from core.backtest.runner import BacktestRunner

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SingleValidation")

def run_single_validation():
    parser = argparse.ArgumentParser(description="Run validation for a single symbol.")
    parser.add_argument("--symbol", type=str, default="NSE_EQ|INE171Z01026", help="Symbol to validate")
    parser.add_argument("--model", type=str, default="core/models/pixityAI_global_15m.joblib", help="Model path")
    parser.add_argument("--sl", type=float, default=2.0, help="SL ATR Multiplier")
    parser.add_argument("--tp", type=float, default=4.0, help="TP ATR Multiplier")
    
    args = parser.parse_args()
    symbol = args.symbol
    
    # Validation Period (Test Set)
    test_start = datetime(2025, 6, 1)
    test_end = datetime(2025, 12, 31)
    
    timeframe = "15m"
    initial_capital = 100000.0
    
    # Strategy Params
    strategy_params = {
        "model_path": args.model,
        "skip_meta_model": False,
        "use_signal_quality_filter": False,
        "long_threshold": 0.45,
        "short_threshold": 0.45
    }

    # CRITICAL: We need to ensure the risk engine uses the updated SL/TP multipliers.
    # Currently it's hardcoded in core/execution/pixityAI_risk_engine.py.
    # We should ideally pass them via config, but since we already modified the file to 2.0/4.0, 
    # we just acknowledge that here.

    db_manager = DatabaseManager(Path("data"))
    runner = BacktestRunner(db_manager)

    logger.info(f"Starting Validation Run for {symbol}")
    logger.info(f"Period: {test_start.date()} -> {test_end.date()}")
    logger.info(f"Model: {strategy_params['model_path']}")

    # 2. Run Backtest
    run_id = f"validation_global_{symbol.split('|')[-1]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    try:
        runner.run(
            strategy_id="pixityAI_meta",
            symbol=symbol,
            start_time=test_start,
            end_time=test_end,
            initial_capital=initial_capital,
            strategy_params=strategy_params,
            timeframe=timeframe,
            run_id=run_id
        )
        logger.info(f"Backtest Complete. Run ID: {run_id}")
    except Exception as e:
        logger.error(f"Backtest Failed: {e}")
        return

    # 3. Fetch Results & Trade Details
    analyze_results(run_id, db_manager)

def analyze_results(run_id, db_manager):
    db_path = Path("data/backtest/runs") / f"{run_id}.duckdb"
    
    if not db_path.exists():
        logger.error(f"Run DB not found: {db_path}")
        return

    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        trades_df = conn.execute("SELECT * FROM trades ORDER BY entry_ts").df()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to read trades: {e}")
        return

    if trades_df.empty:
        logger.warning("No trades executed in this run.")
        return

    trades_df['net_pnl'] = trades_df['pnl'] - trades_df['fees']
    total_trades = len(trades_df)
    win_rate = (len(trades_df[trades_df['net_pnl'] > 0]) / total_trades * 100) if total_trades > 0 else 0
    total_net_pnl = trades_df['net_pnl'].sum()
    
    print("\n" + "="*60)
    print(f"VALIDATION RESULTS: {run_id}")
    print("="*60)
    print(f"Total Trades: {total_trades}")
    print(f"Win Rate:     {win_rate:.2f}%")
    print(f"Total Net PnL: Rs {total_net_pnl:,.2f}")
    
    # Detailed Trade Log
    print("\n" + "-"*100)
    print(f"{'Direction':<10} | {'Entry Time':<20} | {'Entry':<8} | {'Exit':<8} | {'Conf':<6} | {'SL':<8} | {'TP':<8} | {'PnL (Net)':<10}")
    print("-" * 100)
    
    for _, row in trades_df.iterrows():
        meta = json.loads(row['metadata']) if isinstance(row['metadata'], str) else row['metadata']
        sl = meta.get('sl', 0.0)
        tp = meta.get('tp', 0.0)
        conf = meta.get('confidence', 0.0)
        
        entry_time = str(row['entry_ts'])[:19]
        
        print(f"{row['direction']:<10} | {entry_time:<20} | {row['entry_price']:<8.2f} | {row['exit_price']:<8.2f} | {conf:<6.4f} | {sl:<8.2f} | {tp:<8.2f} | {row['net_pnl']:<10.2f}")
        
    print("-" * 100 + "\n")

if __name__ == "__main__":
    run_single_validation()
