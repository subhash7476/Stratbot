"""
Comparative Backtest: Signal Quality Filters

Tests filtered vs unfiltered PixityAI signals on profitable symbols.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import logging
from datetime import datetime
from pathlib import Path
import pandas as pd

from core.database.manager import DatabaseManager
from core.backtest.runner import BacktestRunner

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Test symbols from MEMORY.md (walk-forward validated as profitable)
TEST_SYMBOLS = [
    ("NSE_EQ|INE155A01022", "Tata Power"),      # Walk-forward: Rs +20,757
    ("NSE_EQ|INE118H01025", "Bajaj Finance"),   # Walk-forward profitable
]

# Test periods (from MEMORY.md walk-forward setup)
TRAIN_PERIOD = (datetime(2024, 10, 17), datetime(2025, 5, 31))  # Training
TEST_PERIOD = (datetime(2025, 6, 1), datetime(2025, 12, 31))    # Out-of-sample


def create_temp_config(min_sn: float = 2.0) -> str:
    """Create a temporary filter config file with specific S/N threshold."""
    config = {
        "signal_quality_pipeline": {
            "enabled": True,
            "mode": "SEQUENTIAL",
            "filters": [
                {
                    "name": "kalman",
                    "enabled": True,
                    "weight": 1.0,
                    "params": {
                        "lookback_periods": 50,
                        "min_signal_noise_ratio": min_sn,
                        "trend_alignment_required": True,
                        "process_variance": 0.01,
                        "measurement_variance": 0.1
                    }
                }
            ]
        }
    }

    # Create temp config directory if needed
    temp_dir = Path("core/models/temp")
    temp_dir.mkdir(exist_ok=True)

    config_path = temp_dir / f"filter_sn{int(min_sn*10)}.json"
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)

    return str(config_path)


def get_filter_configs():
    """Get filter configurations to test."""
    return [
        {
            "name": "baseline",
            "params": {
                "skip_meta_model": True,
                "use_signal_quality_filter": False
            },
            "description": "No filtering (raw events)"
        },
        {
            "name": "meta_model",
            "params": {
                "skip_meta_model": False,
                "use_signal_quality_filter": False
            },
            "description": "Meta-model filter (anti-predictive)"
        },
        {
            "name": "kalman_sn15",
            "params": {
                "skip_meta_model": True,
                "use_signal_quality_filter": True,
                "signal_quality_config": create_temp_config(min_sn=1.5)
            },
            "description": "Kalman filter (S/N=1.5, lenient)"
        },
        {
            "name": "kalman_sn20",
            "params": {
                "skip_meta_model": True,
                "use_signal_quality_filter": True,
                "signal_quality_config": create_temp_config(min_sn=2.0)
            },
            "description": "Kalman filter (S/N=2.0, default)"
        },
        {
            "name": "kalman_sn25",
            "params": {
                "skip_meta_model": True,
                "use_signal_quality_filter": True,
                "signal_quality_config": create_temp_config(min_sn=2.5)
            },
            "description": "Kalman filter (S/N=2.5, strict)"
        },
    ]


def run_single_backtest(
    runner: BacktestRunner,
    symbol: str,
    start_time: datetime,
    end_time: datetime,
    config_name: str,
    config_params: dict
) -> tuple[str, dict]:
    """Run a single backtest and return run_id + metrics."""
    logger.info(f"\n{'='*80}")
    logger.info(f"Running: {config_name} | {symbol} | {start_time.date()} to {end_time.date()}")
    logger.info(f"{'='*80}")

    try:
        run_id = runner.run(
            strategy_id="pixityAI_meta",
            symbol=symbol,
            start_time=start_time,
            end_time=end_time,
            initial_capital=100000.0,
            strategy_params=config_params,
            timeframe='15m'  # From MEMORY.md: 15m >> 1h for profitability
        )

        # Extract metrics from backtest_runs table
        with runner.db.backtest_index_reader() as conn:
            row = conn.execute("""
                SELECT total_trades, win_rate, total_pnl, max_drawdown
                FROM backtest_runs
                WHERE run_id = ?
            """, [run_id]).fetchone()

            if row:
                metrics = {
                    'total_trades': row[0] or 0,
                    'win_rate': row[1] or 0.0,
                    'net_pnl': row[2] or 0.0,
                    'sharpe_ratio': 0.0,  # Not calculated yet
                    'max_drawdown_pct': row[3] or 0.0
                }
            else:
                metrics = {'error': 'No metrics found'}

        logger.info(f"✓ Completed: {metrics.get('total_trades', 0)} trades, "
                   f"Net PnL: Rs {metrics.get('net_pnl', 0):,.0f}")

        return run_id, metrics

    except Exception as e:
        logger.error(f"✗ Failed: {e}", exc_info=True)
        return None, {"error": str(e)}


def compare_results(results: dict):
    """Generate comparison table of backtest results."""
    logger.info("\n" + "="*100)
    logger.info("COMPARATIVE RESULTS")
    logger.info("="*100)

    # Group by symbol + period
    for symbol_key in TEST_SYMBOLS:
        symbol, name = symbol_key
        logger.info(f"\n{'─'*100}")
        logger.info(f"{name} ({symbol})")
        logger.info(f"{'─'*100}")

        for period_name, period in [("Train", TRAIN_PERIOD), ("Test", TEST_PERIOD)]:
            logger.info(f"\n{period_name} Period: {period[0].date()} to {period[1].date()}")
            logger.info(f"\n{'Filter':<20} {'Trades':<10} {'Win Rate':<12} {'Net PnL':<15} {'Sharpe':<10} {'Max DD':<10}")
            logger.info("-" * 100)

            # Get all configs for this symbol + period
            for config in FILTER_CONFIGS:
                key = (symbol, period_name, config['name'])
                if key not in results:
                    continue

                metrics = results[key]
                if 'error' in metrics:
                    logger.info(f"{config['name']:<20} ERROR: {metrics['error']}")
                    continue

                total_trades = metrics.get('total_trades', 0)
                win_rate = metrics.get('win_rate', 0)
                net_pnl = metrics.get('net_pnl', 0)
                sharpe = metrics.get('sharpe_ratio', 0)
                max_dd = metrics.get('max_drawdown_pct', 0)

                logger.info(
                    f"{config['name']:<20} "
                    f"{total_trades:<10} "
                    f"{win_rate:<12.1f}% "
                    f"₹{net_pnl:<14,.0f} "
                    f"{sharpe:<10.2f} "
                    f"{max_dd:<10.1f}%"
                )

    # Summary insights
    logger.info("\n" + "="*100)
    logger.info("KEY INSIGHTS")
    logger.info("="*100)

    # Find best filter by Net PnL
    best_filters = {}
    for (symbol, period, config_name), metrics in results.items():
        if 'error' in metrics:
            continue

        key = f"{symbol}_{period}"
        if key not in best_filters or metrics['net_pnl'] > best_filters[key]['pnl']:
            best_filters[key] = {
                'config': config_name,
                'pnl': metrics['net_pnl'],
                'trades': metrics['total_trades'],
                'win_rate': metrics['win_rate']
            }

    for key, best in best_filters.items():
        logger.info(
            f"{key}: Best = {best['config']} "
            f"(₹{best['pnl']:,.0f}, {best['trades']} trades, {best['win_rate']:.1f}% WR)"
        )


def main():
    """Run all comparative backtests."""
    db = DatabaseManager(Path("data"))
    runner = BacktestRunner(db)

    results = {}

    filter_configs = get_filter_configs()

    # Run all combinations
    for symbol, name in TEST_SYMBOLS:
        for period_name, (start, end) in [("Train", TRAIN_PERIOD), ("Test", TEST_PERIOD)]:
            for config in filter_configs:
                key = (symbol, period_name, config['name'])

                run_id, metrics = run_single_backtest(
                    runner=runner,
                    symbol=symbol,
                    start_time=start,
                    end_time=end,
                    config_name=f"{name} | {period_name} | {config['description']}",
                    config_params=config['params']
                )

                results[key] = metrics

    # Generate comparison
    compare_results(results)

    # Cleanup temp configs
    temp_dir = Path("core/models/temp")
    if temp_dir.exists():
        for f in temp_dir.glob("*.json"):
            f.unlink()
        temp_dir.rmdir()

    logger.info("\n✓ All backtests completed!")


if __name__ == "__main__":
    main()
