"""
Test/Demo Script for Signal Quality Filter Pipeline

Demonstrates how to use the modular filter system with different configurations.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import duckdb
import logging
from datetime import datetime, timedelta
from core.strategies.pixityAI_batch_events import batch_generate_events_with_quality_filter

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_sample_data(symbol: str = "NSE_EQ|INE155A01022", days: int = 30):
    """
    Load sample data from DuckDB for testing.

    Args:
        symbol: Trading symbol (default: Tata Power)
        days: Number of days to load

    Returns:
        DataFrame with OHLCV data
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    logger.info(f"Loading {days} days of data for {symbol}...")

    # Find data files
    data_dir = "data/market_data/nse/candles/1m"
    if not os.path.exists(data_dir):
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    # Load data from DuckDB files
    all_data = []
    current_date = start_date

    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        db_path = f"{data_dir}/{date_str}.duckdb"

        if os.path.exists(db_path):
            try:
                conn = duckdb.connect(db_path, read_only=True)
                query = f"""
                    SELECT timestamp, open, high, low, close, volume
                    FROM candles
                    WHERE symbol = '{symbol}'
                    ORDER BY timestamp
                """
                df = conn.execute(query).df()
                conn.close()

                if not df.empty:
                    all_data.append(df)
                    logger.debug(f"  Loaded {len(df)} bars from {date_str}")
            except Exception as e:
                logger.warning(f"  Error loading {date_str}: {e}")

        current_date += timedelta(days=1)

    if not all_data:
        raise ValueError(f"No data found for {symbol}")

    # Combine and prepare
    df = pd.concat(all_data, ignore_index=True)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp').reset_index(drop=True)
    df['symbol'] = symbol

    logger.info(f"Loaded {len(df)} total bars from {df['timestamp'].min()} to {df['timestamp'].max()}")

    return df


def test_no_filter():
    """Test baseline: no filtering."""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 1: NO FILTER (Baseline)")
    logger.info("=" * 80)

    df = load_sample_data(days=30)

    # Temporarily disable filters by passing empty config
    from core.strategies.pixityAI_batch_events import batch_generate_events

    events = batch_generate_events(df, bar_minutes=15)

    logger.info(f"\nBaseline: {len(events)} raw events generated")
    return len(events)


def test_kalman_filter():
    """Test with Kalman filter enabled."""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 2: KALMAN FILTER ONLY")
    logger.info("=" * 80)

    df = load_sample_data(days=30)

    # Create temporary config with only Kalman enabled
    import json
    config = {
        "signal_quality_pipeline": {
            "enabled": True,
            "mode": "SEQUENTIAL",
            "min_confidence_threshold": 0.6,
            "filters": [
                {
                    "name": "kalman",
                    "enabled": True,
                    "weight": 1.0,
                    "params": {
                        "lookback_periods": 50,
                        "min_signal_noise_ratio": 2.0,
                        "trend_alignment_required": True,
                        "process_variance": 0.01,
                        "measurement_variance": 0.1
                    }
                }
            ]
        }
    }

    # Write temp config
    temp_config_path = "core/models/signal_quality_config_test.json"
    with open(temp_config_path, 'w') as f:
        json.dump(config, f, indent=2)

    try:
        events, stats = batch_generate_events_with_quality_filter(
            df,
            config_path=temp_config_path,
            bar_minutes=15
        )

        logger.info(f"\nFiltered Events: {len(events)}/{stats['raw_event_count']}")
        logger.info(f"Acceptance Rate: {stats['acceptance_rate_pct']:.1f}%")
        logger.info(f"\nRejection Reasons:")
        for reason, count in stats['rejection_reasons'].items():
            logger.info(f"  {reason}: {count}")

        return len(events), stats

    finally:
        # Clean up temp config
        if os.path.exists(temp_config_path):
            os.remove(temp_config_path)


def test_multiple_thresholds():
    """Test Kalman filter with different thresholds."""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 3: KALMAN FILTER - VARYING THRESHOLDS")
    logger.info("=" * 80)

    df = load_sample_data(days=30)

    thresholds = [1.0, 1.5, 2.0, 2.5, 3.0]
    results = {}

    for threshold in thresholds:
        import json

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
                            "min_signal_noise_ratio": threshold,
                            "trend_alignment_required": True,
                            "process_variance": 0.01,
                            "measurement_variance": 0.1
                        }
                    }
                ]
            }
        }

        temp_config_path = "core/models/signal_quality_config_test.json"
        with open(temp_config_path, 'w') as f:
            json.dump(config, f, indent=2)

        try:
            events, stats = batch_generate_events_with_quality_filter(
                df,
                config_path=temp_config_path,
                bar_minutes=15
            )

            results[threshold] = {
                'filtered_count': len(events),
                'raw_count': stats.get('raw_event_count', len(events)),
                'acceptance_rate': stats.get('acceptance_rate_pct', 100.0)
            }

        finally:
            if os.path.exists(temp_config_path):
                os.remove(temp_config_path)

    logger.info("\nThreshold Sensitivity Analysis:")
    logger.info(f"{'Threshold':<12} {'Accepted':<12} {'Acceptance Rate':<20}")
    logger.info("-" * 50)
    for threshold, result in results.items():
        logger.info(
            f"{threshold:<12.1f} "
            f"{result['filtered_count']:<12} "
            f"{result['acceptance_rate']:<20.1f}%"
        )

    return results


def main():
    """Run all tests."""
    try:
        # Test 1: Baseline
        baseline_count = test_no_filter()

        # Test 2: Kalman filter
        filtered_count, stats = test_kalman_filter()

        # Test 3: Threshold analysis
        threshold_results = test_multiple_thresholds()

        # Summary
        logger.info("\n" + "=" * 80)
        logger.info("SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Baseline (no filter):        {baseline_count} events")
        logger.info(f"With Kalman (S/N=2.0):       {filtered_count} events ({filtered_count/baseline_count*100:.1f}%)")
        logger.info(f"\nReduction: {baseline_count - filtered_count} events filtered out")

        logger.info("\n✓ All tests completed successfully!")

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
