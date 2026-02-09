"""
Quick test: Single backtest with/without Kalman filter
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

    config_path = temp_dir / f"filter_test.json"
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)

    return str(config_path)


def main():
    logger.info("Testing PixityAI Backtest with Signal Quality Filter")
    logger.info("="*80)

    db = DatabaseManager(Path("data"))
    runner = BacktestRunner(db)

    symbol = "NSE_EQ|INE155A01022"  # Tata Power
    start = datetime(2025, 6, 1)
    end = datetime(2025, 12, 31)

    # Test 1: Baseline (no filter)
    logger.info("\n[TEST 1] Running baseline (no filter)...")
    run1 = runner.run(
        strategy_id="pixityAI_meta",
        symbol=symbol,
        start_time=start,
        end_time=end,
        initial_capital=100000.0,
        strategy_params={
            "skip_meta_model": True,
            "use_signal_quality_filter": False
        },
        timeframe='15m'
    )

    with db.backtest_index_reader() as conn:
        row1 = conn.execute(
            "SELECT total_trades, win_rate, total_pnl FROM backtest_runs WHERE run_id = ?",
            [run1]
        ).fetchone()

    logger.info(f"✓ Baseline: {row1[0]} trades, {row1[1]:.1f}% WR, ₹{row1[2]:,.0f} PnL")

    # Test 2: With Kalman Filter
    logger.info("\n[TEST 2] Running with Kalman filter (S/N=2.0)...")
    config_path = create_temp_config(min_sn=2.0)

    run2 = runner.run(
        strategy_id="pixityAI_meta",
        symbol=symbol,
        start_time=start,
        end_time=end,
        initial_capital=100000.0,
        strategy_params={
            "skip_meta_model": True,
            "use_signal_quality_filter": True,
            "signal_quality_config": config_path
        },
        timeframe='15m'
    )

    with db.backtest_index_reader() as conn:
        row2 = conn.execute(
            "SELECT total_trades, win_rate, total_pnl FROM backtest_runs WHERE run_id = ?",
            [run2]
        ).fetchone()

    logger.info(f"✓ Filtered: {row2[0]} trades, {row2[1]:.1f}% WR, ₹{row2[2]:,.0f} PnL")

    # Summary
    logger.info("\n" + "="*80)
    logger.info("COMPARISON SUMMARY")
    logger.info("="*80)
    logger.info(f"Baseline:  {row1[0]} trades, {row1[1]:.1f}% WR, ₹{row1[2]:,.0f} PnL")
    logger.info(f"Filtered:  {row2[0]} trades, {row2[1]:.1f}% WR, ₹{row2[2]:,.0f} PnL")
    logger.info(f"Reduction: {row1[0] - row2[0]} trades filtered out ({(1 - row2[0]/row1[0])*100:.1f}%)")
    logger.info(f"PnL Delta: ₹{row2[2] - row1[2]:+,.0f} ({(row2[2]/row1[2] - 1)*100:+.1f}%)")

    # Cleanup
    Path(config_path).unlink()
    Path("core/models/temp").rmdir()

    logger.info("\n✓ Test completed!")


if __name__ == "__main__":
    main()
