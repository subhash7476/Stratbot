"""
Filter Validation Script
------------------------
Tests each filter configuration across ALL profitable symbols from
the latest scanner run, in BOTH train and test periods.

Validation protocol (NON-NEGOTIABLE):
  1. Run on ALL profitable symbols from scanner
  2. Run on BOTH train (Oct 2024 - May 2025) AND test (Jun - Dec 2025)
  3. Filter promoted ONLY if it doesn't hurt ANY symbol in EITHER period
  4. "Doesn't hurt" = PnL not decreased AND DD not increased
  5. Drawdown reduction alone counts as improvement
  6. If helps some but hurts others -> stays DISABLED

Usage:
    python scripts/validate_filters.py                        # Test all filters
    python scripts/validate_filters.py --filter ou_reversion  # Test specific filter
    python scripts/validate_filters.py --scan-id <id>         # Use specific scan results
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from core.backtest.runner import BacktestRunner
from core.backtest.scan_persistence import ScanPersistence
from core.database.manager import DatabaseManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Train/Test periods (from walk-forward validation)
TRAIN_START = datetime(2024, 10, 1)
TRAIN_END = datetime(2025, 5, 31)
TEST_START = datetime(2025, 6, 1)
TEST_END = datetime(2025, 12, 31)

# Tolerance for "doesn't hurt" (allow Rs 100 noise in PnL, 0.5% in DD)
PNL_TOLERANCE = -100.0
DD_TOLERANCE = 0.5


def get_profitable_symbols(scan_id: Optional[str] = None) -> List[Dict]:
    """Get profitable symbols from scanner using the is_profitable flag."""
    db = DatabaseManager(Path("data"))
    persistence = ScanPersistence(db)
    symbols = persistence.get_profitable_symbols(scan_id)
    if not symbols:
        raise ValueError("No profitable symbols found in scanner results")
    logger.info(f"Found {len(symbols)} profitable symbols")
    for s in symbols:
        logger.info(f"  {s.get('trading_symbol', s.get('symbol', '?'))} (rank #{s.get('rank', '?')})")
    return symbols


def run_single_backtest(
    runner: BacktestRunner,
    symbol: str,
    start_time: datetime,
    end_time: datetime,
    strategy_params: Dict,
    label: str,
) -> Dict:
    """Run one backtest, return metrics dict."""
    try:
        run_id = runner.run(
            strategy_id="pixityAI_meta",
            symbol=symbol,
            start_time=start_time,
            end_time=end_time,
            initial_capital=100000.0,
            strategy_params=strategy_params,
            timeframe="15m",
        )
        with runner.db.backtest_index_reader() as conn:
            row = conn.execute(
                "SELECT total_trades, win_rate, total_pnl, max_drawdown "
                "FROM backtest_runs WHERE run_id = ?",
                [run_id],
            ).fetchone()

        if row:
            return {
                "run_id": run_id,
                "total_trades": row[0] or 0,
                "win_rate": row[1] or 0.0,
                "total_pnl": row[2] or 0.0,
                "max_drawdown": row[3] or 0.0,
            }
    except Exception as e:
        logger.error(f"  [{label}] Backtest failed for {symbol}: {e}")

    return {"run_id": None, "total_trades": 0, "win_rate": 0.0, "total_pnl": 0.0, "max_drawdown": 0.0}


def create_filter_config(filter_name: str, params: Dict) -> str:
    """Write a temp config enabling only the specified filter. Returns path."""
    config = {
        "signal_quality_pipeline": {
            "enabled": True,
            "mode": "SEQUENTIAL",
            "min_confidence_threshold": 0.6,
            "filters": [
                {"name": filter_name, "enabled": True, "weight": 1.0, "params": params}
            ],
        }
    }
    path = Path(f"_temp_filter_config_{filter_name}.json")
    path.write_text(json.dumps(config, indent=2))
    return str(path)


FILTER_DEFAULTS = {
    "ou_reversion": {
        "min_mean_reversion_speed": 0.5,
        "estimation_window_bars": 200,
        "distance_threshold_sigma": 0.5,
    },
    "gmm_regime": {
        "n_regimes": 3,
        "feature_window": 20,
        "vol_window": 20,
    },
    "volatility": {
        "min_volatility_bps": 75,
        "max_volatility_bps": 500,
        "ewma_alpha": 0.94,
    },
}


def validate_filter(
    filter_name: str,
    symbols: List[Dict],
    periods: List[Tuple[str, datetime, datetime]],
) -> Dict:
    """Validate a filter across all symbols and periods."""
    params = FILTER_DEFAULTS.get(filter_name, {})
    config_path = create_filter_config(filter_name, params)

    baseline_params = {"skip_meta_model": True, "use_signal_quality_filter": False}
    filter_params = {
        "skip_meta_model": True,
        "use_signal_quality_filter": True,
        "signal_quality_config": config_path,
    }

    db = DatabaseManager(Path("data"))
    runner = BacktestRunner(db)
    results = {"filter_name": filter_name, "periods": {}}

    for period_name, start, end in periods:
        period_results = {}
        for sym_info in symbols:
            symbol = sym_info.get("symbol", sym_info.get("instrument_key", ""))
            trading = sym_info.get("trading_symbol", symbol)
            logger.info(f"  {trading} [{period_name}] ...")

            baseline = run_single_backtest(runner, symbol, start, end, baseline_params, f"baseline-{period_name}")
            filtered = run_single_backtest(runner, symbol, start, end, filter_params, f"{filter_name}-{period_name}")

            pnl_change = filtered["total_pnl"] - baseline["total_pnl"]
            dd_change = filtered["max_drawdown"] - baseline["max_drawdown"]
            trade_change = filtered["total_trades"] - baseline["total_trades"]

            period_results[symbol] = {
                "trading_symbol": trading,
                "baseline": baseline,
                "filtered": filtered,
                "pnl_change": pnl_change,
                "dd_change": dd_change,
                "trade_change": trade_change,
            }

            logger.info(
                f"    Baseline: PnL={baseline['total_pnl']:+,.0f} DD={baseline['max_drawdown']:.1f}% "
                f"Trades={baseline['total_trades']}"
            )
            logger.info(
                f"    Filtered: PnL={filtered['total_pnl']:+,.0f} DD={filtered['max_drawdown']:.1f}% "
                f"Trades={filtered['total_trades']}"
            )
            logger.info(f"    Delta:    PnL={pnl_change:+,.0f} DD={dd_change:+.1f}% Trades={trade_change:+d}")

        results["periods"][period_name] = period_results

    # Cleanup temp config
    try:
        os.remove(config_path)
    except OSError:
        pass

    return results


def analyze_results(results: Dict) -> str:
    """Analyze results and produce a verdict."""
    filter_name = results["filter_name"]
    lines = [
        f"\n{'='*70}",
        f"FILTER VALIDATION: {filter_name}",
        f"{'='*70}\n",
    ]

    all_ok = True
    for period_name, period_data in results["periods"].items():
        lines.append(f"--- {period_name.upper()} ---")
        hurt_count = 0
        for symbol, data in period_data.items():
            trading = data["trading_symbol"]
            pnl_d = data["pnl_change"]
            dd_d = data["dd_change"]

            # "Doesn't hurt": PnL not decreased beyond tolerance AND DD not increased beyond tolerance
            pnl_ok = pnl_d >= PNL_TOLERANCE
            dd_ok = dd_d <= DD_TOLERANCE
            verdict = "OK" if (pnl_ok and dd_ok) else "HURT"
            if verdict == "HURT":
                hurt_count += 1
                all_ok = False

            lines.append(
                f"  {trading:20s} PnL={pnl_d:+8,.0f} DD={dd_d:+5.1f}% "
                f"Trades={data['trade_change']:+4d}  [{verdict}]"
            )
        lines.append(f"  -> {len(period_data) - hurt_count}/{len(period_data)} symbols OK\n")

    decision = "PROMOTE" if all_ok else "REJECT"
    lines.append(f"DECISION: {decision}")
    if decision == "REJECT":
        lines.append("REASON: Filter hurt at least one symbol in at least one period.")
        lines.append("Per protocol: filter stays DISABLED by default.\n")
    else:
        lines.append("All symbols improved or neutral in all periods.")
        lines.append("Filter is SAFE to enable. Verify edge significance before production.\n")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Validate signal quality filters")
    parser.add_argument("--filter", type=str, help="Specific filter to validate")
    parser.add_argument("--scan-id", type=str, help="Specific scan ID for profitable symbols")
    args = parser.parse_args()

    symbols = get_profitable_symbols(args.scan_id)
    if not symbols:
        logger.error("No profitable symbols found!")
        return

    periods = [
        ("train", TRAIN_START, TRAIN_END),
        ("test", TEST_START, TEST_END),
    ]

    filters_to_test = [args.filter] if args.filter else ["ou_reversion", "gmm_regime", "volatility"]

    logger.info(f"Validating {len(filters_to_test)} filter(s) on {len(symbols)} symbols")
    logger.info(f"Train: {TRAIN_START.date()} to {TRAIN_END.date()}")
    logger.info(f"Test:  {TEST_START.date()} to {TEST_END.date()}")

    for filter_name in filters_to_test:
        logger.info(f"\n{'='*40}")
        logger.info(f"Starting validation: {filter_name}")
        logger.info(f"{'='*40}")

        try:
            results = validate_filter(filter_name, symbols, periods)
            analysis = analyze_results(results)
            print(analysis)

            out_path = Path(f"filter_validation_{filter_name}.json")
            out_path.write_text(json.dumps(results, indent=2, default=str))
            logger.info(f"Detailed results saved to {out_path}")

        except Exception as e:
            logger.error(f"Validation failed for {filter_name}: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
