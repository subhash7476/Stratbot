"""
Quick Comparison: Test 3 configs on 1 symbol, test period only
Estimated runtime: 15-20 minutes
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import logging
from datetime import datetime
from pathlib import Path

from core.database.manager import DatabaseManager
from core.backtest.runner import BacktestRunner

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_temp_config(min_sn: float = 2.0) -> str:
    """Create a temporary filter config file."""
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

    temp_dir = Path("core/models/temp")
    temp_dir.mkdir(exist_ok=True)

    config_path = temp_dir / f"filter_sn{int(min_sn*10)}.json"
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)

    return str(config_path)


def main():
    logger.info("="*80)
    logger.info("QUICK COMPARISON: 3 Configs on Tata Power (Test Period)")
    logger.info("="*80)

    db = DatabaseManager(Path("data"))
    runner = BacktestRunner(db)

    symbol = "NSE_EQ|INE155A01022"  # Tata Power
    start = datetime(2025, 6, 1)
    end = datetime(2025, 12, 31)

    configs = [
        {
            "name": "Baseline (no filter)",
            "params": {
                "skip_meta_model": True,
                "use_signal_quality_filter": False
            }
        },
        {
            "name": "Kalman S/N=1.5 (lenient)",
            "params": {
                "skip_meta_model": True,
                "use_signal_quality_filter": True,
                "signal_quality_config": create_temp_config(min_sn=1.5)
            }
        },
        {
            "name": "Kalman S/N=2.0 (default)",
            "params": {
                "skip_meta_model": True,
                "use_signal_quality_filter": True,
                "signal_quality_config": create_temp_config(min_sn=2.0)
            }
        },
    ]

    results = []

    for i, config in enumerate(configs, 1):
        logger.info(f"\n{'='*80}")
        logger.info(f"[{i}/3] Running: {config['name']}")
        logger.info(f"{'='*80}")

        try:
            run_id = runner.run(
                strategy_id="pixityAI_meta",
                symbol=symbol,
                start_time=start,
                end_time=end,
                initial_capital=100000.0,
                strategy_params=config['params'],
                timeframe='15m'
            )

            # Extract metrics
            with db.backtest_index_reader() as conn:
                row = conn.execute("""
                    SELECT total_trades, win_rate, total_pnl, max_drawdown
                    FROM backtest_runs
                    WHERE run_id = ?
                """, [run_id]).fetchone()

                if row:
                    metrics = {
                        'config': config['name'],
                        'trades': row[0] or 0,
                        'win_rate': row[1] or 0.0,
                        'net_pnl': row[2] or 0.0,
                        'max_dd': row[3] or 0.0
                    }
                    results.append(metrics)
                    logger.info(f"✓ {metrics['trades']} trades, {metrics['win_rate']:.1f}% WR, ₹{metrics['net_pnl']:,.0f} PnL")

        except Exception as e:
            logger.error(f"✗ Failed: {e}")
            results.append({'config': config['name'], 'error': str(e)})

    # Summary Table
    logger.info("\n" + "="*80)
    logger.info("RESULTS SUMMARY - Tata Power (Jun-Dec 2025)")
    logger.info("="*80)
    logger.info(f"\n{'Config':<30} {'Trades':<10} {'Win Rate':<12} {'Net PnL':<15} {'Max DD':<10}")
    logger.info("-" * 80)

    baseline = results[0] if results else None

    for r in results:
        if 'error' in r:
            logger.info(f"{r['config']:<30} ERROR: {r['error']}")
            continue

        trades = r['trades']
        win_rate = r['win_rate']
        net_pnl = r['net_pnl']
        max_dd = r['max_dd']

        # Calculate improvements vs baseline
        if baseline and r != baseline and 'error' not in baseline:
            pnl_delta = net_pnl - baseline['net_pnl']
            pnl_pct = (pnl_delta / baseline['net_pnl'] * 100) if baseline['net_pnl'] != 0 else 0
            wr_delta = win_rate - baseline['win_rate']

            logger.info(
                f"{r['config']:<30} "
                f"{trades:<10} "
                f"{win_rate:<12.1f}% "
                f"₹{net_pnl:<14,.0f} "
                f"{max_dd:<10.1f}%"
            )
            logger.info(f"{'  vs Baseline:':<30} {trades - baseline['trades']:+d} trades | "
                       f"{wr_delta:+.1f}% WR | ₹{pnl_delta:+,.0f} ({pnl_pct:+.1f}%)")
        else:
            logger.info(
                f"{r['config']:<30} "
                f"{trades:<10} "
                f"{win_rate:<12.1f}% "
                f"₹{net_pnl:<14,.0f} "
                f"{max_dd:<10.1f}%"
            )

    # Cleanup
    temp_dir = Path("core/models/temp")
    if temp_dir.exists():
        for f in temp_dir.glob("*.json"):
            f.unlink()
        try:
            temp_dir.rmdir()
        except:
            pass

    logger.info("\n✓ Comparison complete!")


if __name__ == "__main__":
    main()
