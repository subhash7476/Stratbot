"""
Walk-forward backtest for HMM Regime Strategy.

Trains HMM on rolling windows of daily data, generates intraday signals,
and runs through the existing TradingRunner infrastructure.

Usage:
  python scripts/run_regime_backtest.py
  python scripts/run_regime_backtest.py --n_states 2 --train_months 12
"""
import argparse
import json
import sys
import os
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from pathlib import Path
from hashlib import sha256
import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, ROOT)

from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.strategies.regime.observer import RegimeObserver
from core.strategies.regime.classifier import HMMRegimeClassifier, RegimeState
from core.strategies.regime.executor import batch_generate_regime_signals, batch_generate_vix_baseline_signals
from core.strategies.precomputed_signals import PrecomputedSignalStrategy
from core.strategies.regime.circuit_breaker import WeeklyCircuitBreaker
from core.database.providers.market_data import DuckDBMarketDataProvider
from core.database.providers.analytics import DuckDBAnalyticsProvider
from core.execution.handler import ExecutionHandler, ExecutionConfig, ExecutionMode
from core.runner import TradingRunner, RunnerConfig
from core.brokers.paper_broker import PaperBroker
from core.clock import ReplayClock
from core.analytics.resampler import resample_ohlcv
from core.logging import setup_logger

logger = setup_logger("regime_backtest")

SYMBOL = 'NSE_INDEX|Nifty 50'


def load_config():
    config_path = Path(ROOT) / 'core' / 'strategies' / 'regime' / 'regime_config.json'
    with open(config_path) as f:
        return json.load(f)


def load_daily_features(db, start_date, end_date):
    """Load daily data and compute observer features."""
    q = MarketDataQuery(db)
    # Extra warmup for VIX percentile (90 days)
    warmup_start = start_date - timedelta(days=120)

    nifty = q.get_ohlcv('NSE_INDEX|Nifty 50', warmup_start, end_date, '1d')
    banknifty = q.get_ohlcv('NSE_INDEX|Nifty Bank', warmup_start, end_date, '1d')
    vix = q.get_ohlcv('NSE_INDEX|India VIX', warmup_start, end_date, '1d')

    if nifty is None or len(nifty) < 100:
        raise ValueError(f"Insufficient Nifty data: {len(nifty) if nifty is not None else 0} rows")

    observer = RegimeObserver()
    features = observer.compute_features(nifty, banknifty, vix)
    return features


def load_15m_data(db, symbol, start_date, end_date):
    """Load 1m data and resample to 15m."""
    q = MarketDataQuery(db)
    warmup_start = start_date - timedelta(days=30)  # Extra for EMA warmup
    df_1m = q.get_ohlcv(symbol, warmup_start, end_date, '1m')
    if df_1m is None or len(df_1m) == 0:
        raise ValueError(f"No 1m data for {symbol}")

    df_15m = resample_ohlcv(df_1m, '15m')
    # Ensure DatetimeIndex for downstream filtering and executor
    if 'timestamp' in df_15m.columns:
        df_15m['timestamp'] = pd.to_datetime(df_15m['timestamp'])
        df_15m = df_15m.set_index('timestamp')
    return df_15m


def run_single_backtest(db, symbol, train_start, train_end, test_start, test_end,
                         config, n_states, run_id, mode='hmm'):
    """Run one walk-forward window: train HMM (or use VIX baseline), generate signals, run backtest."""

    logger.info(f"  Train: {train_start.date()} to {train_end.date()}")
    logger.info(f"  Test:  {test_start.date()} to {test_end.date()}")

    # 1. Load daily features for full range (train + test)
    features = load_daily_features(db, train_start, test_end)

    # 2. Split into train/test by date
    hmm_features = RegimeObserver().get_hmm_features(features)
    train_mask = [d <= train_end.date() for d in features.index]

    train_features = hmm_features.loc[train_mask]

    logger.info(f"  Train features: {len(train_features)} days")

    clf = None
    if mode == 'hmm':
        if len(train_features) < 60:
            logger.warning(f"  Insufficient training data ({len(train_features)} days), skipping window")
            return None

        # 3. Train HMM
        clf = HMMRegimeClassifier(n_states=n_states, config=config.get('classifier', {}))
        clf.fit(train_features)
        logger.info(f"  HMM converged: {clf.model.monitor_.converged}, states: {clf.state_map}")

    # 4. Load 15m data for test period
    df_15m = load_15m_data(db, symbol, test_start, test_end)
    df_15m_test = df_15m[df_15m.index >= test_start]
    logger.info(f"  15m bars in test: {len(df_15m_test)}")

    if len(df_15m_test) == 0:
        logger.warning("  No 15m data in test period, skipping")
        return None

    # 5. Generate signals (HMM or VIX baseline)
    executor_config = config.get('executor', {})
    executor_config['sizing'] = config.get('sizing', {})
    executor_config['initial_capital'] = config.get('backtest', {}).get('initial_capital', 100000)

    if mode == 'hmm':
        signals = batch_generate_regime_signals(
            nifty_15m=df_15m,
            daily_features=features,
            classifier=clf,
            config=executor_config,
            run_id=run_id,
            symbol=symbol,
        )
    else:
        signals = batch_generate_vix_baseline_signals(
            nifty_15m=df_15m,
            daily_features=features,
            config=executor_config,
            run_id=run_id,
            symbol=symbol,
        )

    # Filter signals to test period
    signals = [s for s in signals if s.timestamp >= test_start]
    logger.info(f"  Signals in test period: {len(signals)}")

    strategy_id = 'hmm_regime' if mode == 'hmm' else 'vix_baseline'

    if not signals:
        logger.warning("  No signals generated, skipping")
        return {'run_id': run_id, 'trades': 0, 'pnl': 0, 'max_dd': 0, 'win_rate': 0,
                'sharpe': 0, 'signals': 0, 'regime_dist': {},
                'train_start': train_start.date().isoformat(),
                'train_end': train_end.date().isoformat(),
                'test_start': test_start.date().isoformat(),
                'test_end': test_end.date().isoformat(),
                'train_days': 0, 'n_states': n_states, 'converged': True, 'mode': mode}

    # 6. Run through TradingRunner
    initial_capital = config.get('backtest', {}).get('initial_capital', 100000.0)
    clock = ReplayClock(test_start)
    broker = PaperBroker(clock)
    exec_config = ExecutionConfig(mode=ExecutionMode.PAPER, max_drawdown_limit=0.99)

    execution = ExecutionHandler(
        db_manager=db, clock=clock, broker=broker,
        config=exec_config, initial_capital=initial_capital, load_db_state=False
    )
    execution._is_signal_already_executed = lambda signal_id: False

    strategy = PrecomputedSignalStrategy(strategy_id, signals, {})

    # Market data provider for test period (uses 15m resampled from 1m)
    market_data = DuckDBMarketDataProvider(
        db_manager=db, symbols=[symbol], timeframe='15m',
        start_time=test_start - timedelta(days=5), end_time=test_end
    )
    analytics = DuckDBAnalyticsProvider(db_manager=db)

    runner = TradingRunner(
        config=RunnerConfig(symbols=[symbol], strategy_ids=[strategy_id], disable_state_update=True),
        db_manager=db,
        market_data_provider=market_data,
        analytics_provider=analytics,
        strategies=[strategy],
        execution_handler=execution,
        position_tracker=execution.position_tracker,
        clock=clock,
    )

    runner.run()

    # 7. Collect metrics
    trades = execution._trade_history
    total_pnl = execution.metrics.cash_balance - initial_capital
    max_dd = execution.metrics.max_drawdown_pct

    wins = sum(1 for t in trades if t.direction == 'SELL' and t.price > 0)  # Simplified
    win_rate = 0.0
    if len(trades) > 1:
        # Pair entry/exit trades
        pnls = []
        i = 0
        while i < len(trades) - 1:
            entry = trades[i]
            exit_t = trades[i + 1]
            if entry.direction == 'BUY' and exit_t.direction == 'SELL':
                trade_pnl = (exit_t.price - entry.price) * entry.quantity - entry.fees - exit_t.fees
                pnls.append(trade_pnl)
            i += 2
        if pnls:
            win_rate = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            # Sharpe (annualized from trade returns)
            if len(pnls) > 1:
                returns = np.array(pnls) / initial_capital
                sharpe = np.mean(returns) / (np.std(returns) + 1e-10) * np.sqrt(252 / max(1, len(pnls)))
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    # Regime distribution in signals
    regime_dist = {}
    for s in signals:
        r = s.metadata.get('regime', 'UNKNOWN')
        regime_dist[r] = regime_dist.get(r, 0) + 1

    result = {
        'run_id': run_id,
        'train_start': train_start.date().isoformat(),
        'train_end': train_end.date().isoformat(),
        'test_start': test_start.date().isoformat(),
        'test_end': test_end.date().isoformat(),
        'train_days': len(train_features),
        'trades': len(trades),
        'signals': len(signals),
        'pnl': round(total_pnl, 2),
        'max_dd': round(max_dd * 100, 1),
        'win_rate': round(win_rate, 1),
        'sharpe': round(sharpe, 2),
        'regime_dist': regime_dist,
        'n_states': n_states,
        'converged': clf.model.monitor_.converged if clf else True,
        'mode': mode,
    }

    logger.info(f"  Result: PnL=Rs {total_pnl:,.0f}, Trades={len(trades)}, "
                f"WR={win_rate:.1f}%, DD={max_dd*100:.1f}%, Sharpe={sharpe:.2f}")

    return result


def main():
    parser = argparse.ArgumentParser(description='HMM Regime Walk-Forward Backtest')
    parser.add_argument('--n_states', type=int, default=3, help='Number of HMM states')
    parser.add_argument('--train_months', type=int, default=None, help='Override training window months')
    parser.add_argument('--test_months', type=int, default=None, help='Override test window months')
    parser.add_argument('--mode', choices=['hmm', 'vix_baseline', 'compare'], default='hmm',
                        help='hmm=HMM regime, vix_baseline=simple VIX thresholds, compare=run both')
    args = parser.parse_args()

    config = load_config()

    # Override from CLI
    if args.train_months:
        config['backtest']['train_months'] = args.train_months
    if args.test_months:
        config['backtest']['test_months'] = args.test_months

    train_months = config['backtest']['train_months']
    test_months = config['backtest']['test_months']
    step_months = config['backtest'].get('walk_forward_step_months', test_months)

    db = DatabaseManager(Path(ROOT) / 'data')

    modes = ['hmm', 'vix_baseline'] if args.mode == 'compare' else [args.mode]

    logger.info("=" * 70)
    logger.info("HMM REGIME WALK-FORWARD BACKTEST")
    logger.info("=" * 70)
    logger.info(f"Mode: {args.mode}, States: {args.n_states}, Train: {train_months}mo, Test: {test_months}mo, Step: {step_months}mo")
    logger.info(f"Symbol: {SYMBOL}")
    logger.info("")

    # Generate walk-forward windows
    # Data range: Jan 2023 - Feb 2026
    # Need observer warmup (90 days), so effective start is ~Apr 2023
    data_start = datetime(2023, 5, 1)  # After warmup
    data_end = datetime(2026, 2, 13)

    windows = []
    train_start = data_start
    while True:
        train_end = train_start + relativedelta(months=train_months)
        test_start = train_end
        test_end = test_start + relativedelta(months=test_months)

        if test_end > data_end:
            # Truncate last window
            test_end = data_end
            if test_start >= data_end:
                break

        windows.append((train_start, train_end, test_start, test_end))
        train_start = train_start + relativedelta(months=step_months)

        if train_start + relativedelta(months=train_months) >= data_end:
            break

    logger.info(f"Walk-forward windows: {len(windows)}")
    for i, (ts, te, xs, xe) in enumerate(windows):
        logger.info(f"  Window {i+1}: Train {ts.date()}-{te.date()} | Test {xs.date()}-{xe.date()}")
    logger.info("")

    # Run all windows for each mode
    all_results = {}
    for mode in modes:
        logger.info(f"\n{'='*70}")
        logger.info(f"MODE: {mode.upper()}")
        logger.info(f"{'='*70}")

        results = []
        for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
            run_id = f"{mode}_wf_{args.n_states}s_w{i+1}"
            logger.info(f"--- Window {i+1}/{len(windows)} ---")

            try:
                result = run_single_backtest(
                    db, SYMBOL, train_start, train_end, test_start, test_end,
                    config, args.n_states, run_id, mode=mode
                )
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"  Window {i+1} failed: {e}")
                import traceback
                traceback.print_exc()

            logger.info("")

        all_results[mode] = results

    # Summary
    logger.info("=" * 70)
    logger.info("WALK-FORWARD SUMMARY")
    logger.info("=" * 70)

    for mode, results in all_results.items():
        print(f"\n{'='*70}")
        print(f"  {mode.upper()} RESULTS")
        print(f"{'='*70}")

        if not results:
            print("  No results to summarize")
            continue

        print(f"\n{'Window':<10} {'Train Period':<26} {'Test Period':<26} {'PnL':>10} {'Trades':>7} {'WR%':>6} {'DD%':>6} {'Sharpe':>7}")
        print("-" * 100)

        total_pnl = 0
        total_trades = 0
        sharpes = []
        max_dds = []

        for r in results:
            print(f"{r['run_id'][-2:]:<10} {r['train_start']} - {r['train_end']}  "
                  f"{r['test_start']} - {r['test_end']}  "
                  f"{r['pnl']:>10,.0f} {r['trades']:>7} {r['win_rate']:>5.1f}% {r['max_dd']:>5.1f}% {r['sharpe']:>7.2f}")
            total_pnl += r['pnl']
            total_trades += r['trades']
            sharpes.append(r['sharpe'])
            max_dds.append(r['max_dd'])

        print("-" * 100)
        avg_sharpe = np.mean(sharpes) if sharpes else 0
        worst_dd = max(max_dds) if max_dds else 0
        active = [r for r in results if r['trades'] > 0]
        avg_wr = np.mean([r['win_rate'] for r in active]) if active else 0

        print(f"{'TOTAL':<10} {'':26} {'':26} {total_pnl:>10,.0f} {total_trades:>7} {avg_wr:>5.1f}% {worst_dd:>5.1f}% {avg_sharpe:>7.2f}")

    # Comparison table if both modes ran
    if len(all_results) > 1:
        print(f"\n{'='*70}")
        print("  HEAD-TO-HEAD COMPARISON (HMM vs VIX Baseline)")
        print(f"{'='*70}\n")
        print(f"{'Metric':<25} {'HMM':>15} {'VIX Baseline':>15} {'Winner':>10}")
        print("-" * 65)
        summaries = {}
        for mode in list(all_results.keys()):
            results = all_results[mode]
            pnl = sum(r['pnl'] for r in results)
            trades = sum(r['trades'] for r in results)
            active = [r for r in results if r['trades'] > 0]
            wr = np.mean([r['win_rate'] for r in active]) if active else 0
            dd = max(r['max_dd'] for r in results) if results else 0
            sh = np.mean([r['sharpe'] for r in results]) if results else 0
            summaries[mode] = {'pnl': pnl, 'trades': trades, 'wr': wr, 'dd': dd, 'sharpe': sh}

        hmm = summaries.get('hmm', {})
        vix = summaries.get('vix_baseline', {})
        for metric, key, higher_better in [
            ('Total PnL (Rs)', 'pnl', True), ('Total Trades', 'trades', None),
            ('Avg Win Rate %', 'wr', True), ('Max Drawdown %', 'dd', False),
            ('Avg Sharpe', 'sharpe', True)
        ]:
            h_val = hmm.get(key, 0)
            v_val = vix.get(key, 0)
            if higher_better is not None:
                winner = 'HMM' if (h_val > v_val) == higher_better else 'VIX'
            else:
                winner = ''
            fmt = '.1f' if key in ('wr', 'dd') else '.2f' if key == 'sharpe' else ',.0f'
            print(f"{metric:<25} {h_val:>15{fmt}} {v_val:>15{fmt}} {winner:>10}")
        print()

    # Save results
    save_data = {mode: results for mode, results in all_results.items() if not mode.endswith('_summary')}
    results_path = Path(ROOT) / 'data' / 'regime_backtest_results.json'
    with open(results_path, 'w') as f:
        json.dump(save_data, f, indent=2, default=str)
    logger.info(f"\nResults saved to: {results_path}")


if __name__ == "__main__":
    main()
