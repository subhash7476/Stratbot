
import os
import sys
import logging
import json
import pandas as pd
import duckdb
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
logger = logging.getLogger("MultiValidationWithRegime")

def run_multi_validation():
    # 1. Config
    symbols = [
        "NSE_EQ|INE205A01025", # Rank 1 (Top Performer)
        "NSE_EQ|INE171Z01026", # Rank 2
        "NSE_EQ|INE303R01014", # Rank 3
        "NSE_EQ|INE572E01012"  # Rank 4
    ]
    
    # Previous scan stats for comparison
    prev_stats = {
        "NSE_EQ|INE205A01025": {"pnl": 2618.00, "win_rate": 50.0},
        "NSE_EQ|INE171Z01026": {"pnl": 1858.98, "win_rate": 44.0},
        "NSE_EQ|INE303R01014": {"pnl": 1263.68, "win_rate": 42.0},
        "NSE_EQ|INE572E01012": {"pnl": 265.78,  "win_rate": 46.0}
    }
    
    # Validation Period
    test_start = datetime(2025, 6, 1)
    test_end = datetime(2025, 12, 31)
    
    timeframe = "15m"
    initial_capital = 100000.0
    
    # Strategy Params
    strategy_params = {
        "model_path": "core/models/pixityAI_global_15m.joblib",
        "skip_meta_model": False,
        "use_signal_quality_filter": False,
        "long_threshold": 0.45,
        "short_threshold": 0.45,
    }

    # Load Regime Map
    regime_map_path = "core/models/regime_map_validation.json"
    with open(regime_map_path, 'r') as f:
        regime_map = json.load(f)

    db_manager = DatabaseManager(Path("data"))
    runner = BacktestRunner(db_manager)

    results = []

    logger.info(f"Starting Multi-Symbol Validation (Global Model + 2.0 ATR Risk + REGIME FILTER)")
    logger.info(f"Period: {test_start.date()} -> {test_end.date()}")

    for symbol in symbols:
        run_id = f"val_regime_2atr_{symbol.split('|')[-1]}_{datetime.now().strftime('%H%M%S')}"
        logger.info(f"Running {symbol}...")
        
        try:
            # We run the normal backtest, then apply the regime filter post-hoc to the trade list
            # Ideally the strategy should do this, but for quick validation, post-filtering is easier/safer
            # Wait, post-filtering PnL calculation is tricky because capital curve changes.
            # But the 'trades' table is discrete events. We can just sum the net_pnl of valid trades.
            
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
            
            # Analyze Result with Filter
            stats = analyze_run_with_regime(run_id, db_manager, regime_map)
            if stats:
                prev = prev_stats.get(symbol, {})
                results.append({
                    "symbol": symbol,
                    "new_pnl": stats['net_pnl'],
                    "old_pnl": prev.get('pnl', 0),
                    "new_wr": stats['win_rate'],
                    "old_wr": prev.get('win_rate', 0),
                    "trades": stats['trades']
                })
                
        except Exception as e:
            logger.error(f"Failed {symbol}: {e}")

    # Output Comparison Table
    print("\n" + "="*90)
    print(f"COMPARISON: GLOBAL (2.0 ATR) + REGIME FILTER vs OLD PREV SCAN")
    print("="*90)
    print(f"{'Symbol':<20} | {'Old Net PnL':<12} | {'New Net PnL':<12} | {'Diff':<10} | {'Old WR':<6} | {'New WR':<6} | {'Trades':<6}")
    print("-" * 90)
    
    for r in results:
        diff = r['new_pnl'] - r['old_pnl']
        symbol_short = r['symbol'].split('|')[-1]
        print(f"{symbol_short:<20} | {r['old_pnl']:<12.2f} | {r['new_pnl']:<12.2f} | {diff:<10.2f} | {r['old_wr']:<6.1f} | {r['new_wr']:<6.1f} | {r['trades']:<6}")
        
    print("-" * 90)

def analyze_run_with_regime(run_id, db_manager, regime_map):
    db_path = Path("data/backtest/runs") / f"{run_id}.duckdb"
    if not db_path.exists():
        return None

    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        trades_df = conn.execute("SELECT * FROM trades").df()
        conn.close()
    except:
        return None

    if trades_df.empty:
        return {"net_pnl": 0.0, "win_rate": 0.0, "trades": 0}

    # Apply Regime Filter
    # Trade Entry Date must be "EXPANSION"
    valid_indices = []
    
    for idx, row in trades_df.iterrows():
        entry_date = row['entry_ts'].strftime('%Y-%m-%d')
        # Default to neutral if date missing (though map should cover all)
        regime = regime_map.get(entry_date, "UNKNOWN")
        if regime == "EXPANSION":
            valid_indices.append(idx)
            
    filtered_df = trades_df.loc[valid_indices].copy()
    
    if filtered_df.empty:
         return {"net_pnl": 0.0, "win_rate": 0.0, "trades": 0}

    filtered_df['net_pnl'] = filtered_df['pnl'] - filtered_df['fees']
    total_trades = len(filtered_df)
    win_rate = (len(filtered_df[filtered_df['net_pnl'] > 0]) / total_trades * 100)
    
    return {
        "net_pnl": filtered_df['net_pnl'].sum(),
        "win_rate": win_rate,
        "trades": total_trades
    }

if __name__ == "__main__":
    run_multi_validation()
