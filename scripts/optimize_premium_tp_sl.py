"""
Parameter Optimization for Premium TP/SL Strategy
-------------------------------------------------
Grid search over TP/SL percentages, ATR multipliers, and hold periods.
Uses multiprocessing for parallel execution.
"""
import sys
import os
import argparse
import uuid
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass, asdict
from itertools import product
from concurrent.futures import ProcessPoolExecutor, as_completed
import pandas as pd
import numpy as np

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


@dataclass
class OptimizationConfig:
    symbol: str
    days: int
    timeframes: List[str]
    tp_pct_range: List[float]
    sl_pct_range: List[float]
    atr_tp_mult_range: List[float]
    atr_sl_mult_range: List[float]
    max_hold_bars_range: List[int]
    # Confluence thresholds
    adx_threshold_range: List[float] = None
    require_macd_increasing_range: List[bool] = None
    rsi_bullish_threshold_range: List[int] = None
    n_workers: int = 4

    def __post_init__(self):
        # Set defaults if not provided
        if self.adx_threshold_range is None:
            self.adx_threshold_range = [20, 25]
        if self.require_macd_increasing_range is None:
            self.require_macd_increasing_range = [True, False]
        if self.rsi_bullish_threshold_range is None:
            self.rsi_bullish_threshold_range = [55, 60]


@dataclass
class BacktestResult:
    params: Dict[str, Any]
    timeframe: str
    total_trades: int
    win_rate: float
    total_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    profit_factor: float
    avg_trade_pnl: float


def calculate_extended_metrics(trades: List[Dict]) -> Dict[str, float]:
    """
    Calculate Sharpe Ratio, Profit Factor, and other advanced metrics.
    """
    if not trades:
        return {
            'sharpe_ratio': 0.0,
            'profit_factor': 0.0,
            'avg_trade_pnl': 0.0,
            'win_rate': 0.0,
            'max_drawdown': 0.0,
            'total_pnl': 0.0,
            'total_trades': 0
        }

    pnls = [t['pnl'] for t in trades]
    total_pnl = sum(pnls)

    # Win Rate
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = (len(wins) / len(pnls) * 100) if pnls else 0.0

    # Profit Factor
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0001
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

    # Sharpe Ratio (annualized, assuming daily returns)
    if len(pnls) > 1:
        returns = np.array(pnls)
        std = np.std(returns)
        sharpe = (np.mean(returns) / std) * np.sqrt(252) if std > 0 else 0.0
    else:
        sharpe = 0.0

    # Max Drawdown
    equity_curve = np.cumsum(pnls)
    if len(equity_curve) > 0:
        peak = np.maximum.accumulate(equity_curve)
        drawdown = peak - equity_curve
        max_dd = np.max(drawdown)
    else:
        max_dd = 0.0

    return {
        'sharpe_ratio': sharpe,
        'profit_factor': profit_factor,
        'avg_trade_pnl': total_pnl / len(pnls) if pnls else 0.0,
        'win_rate': win_rate,
        'max_drawdown': max_dd,
        'total_pnl': total_pnl,
        'total_trades': len(pnls)
    }


def run_single_backtest(
    df_resampled: pd.DataFrame,
    symbol: str,
    timeframe: str,
    params: Dict[str, Any],
    local_insights: List
) -> BacktestResult:
    """
    Run a single backtest with given parameters.
    """
    from core.clock import ReplayClock
    from core.runner import TradingRunner, RunnerConfig
    from core.execution.handler import ExecutionHandler, ExecutionConfig, ExecutionMode
    from core.execution.position_tracker import PositionTracker
    from core.brokers.paper_broker import PaperBroker
    from core.strategies.registry import create_strategy
    from core.data.market_data_provider import MarketDataProvider
    from core.data.analytics_provider import AnalyticsProvider
    from core.events import OHLCVBar, TradeStatus, SignalEvent

    # Simple providers (inline to avoid import issues)
    class SimpleDataFrameProvider(MarketDataProvider):
        def __init__(self, df, sym):
            super().__init__([sym])
            self.df = df
            self.symbol = sym
            self.idx = 0

        def get_next_bar(self, sym: str):
            if sym != self.symbol or self.idx >= len(self.df):
                return None
            row = self.df.iloc[self.idx]
            bar = OHLCVBar(
                symbol=self.symbol,
                timestamp=row.name,
                open=float(row['open']),
                high=float(row['high']),
                low=float(row['low']),
                close=float(row['close']),
                volume=float(row['volume'])
            )
            self.idx += 1
            return bar

        def get_latest_bar(self, sym: str):
            if self.idx == 0:
                return None
            row = self.df.iloc[self.idx - 1]
            return OHLCVBar(
                symbol=self.symbol,
                timestamp=row.name,
                open=float(row['open']),
                high=float(row['high']),
                low=float(row['low']),
                close=float(row['close']),
                volume=float(row['volume'])
            )

        def is_data_available(self, sym: str) -> bool:
            return self.idx < len(self.df)

        def reset(self, sym: str) -> None:
            self.idx = 0

        def get_progress(self, sym: str):
            return self.idx, len(self.df)

    class SimpleAnalyticsProvider(AnalyticsProvider):
        def __init__(self, insights):
            self.insights = {i.timestamp: i for i in insights}
            self.timestamps = sorted(self.insights.keys())

        def get_latest_snapshot(self, sym: str, as_of=None):
            if not as_of:
                return self.insights[self.timestamps[-1]] if self.timestamps else None
            for ts in reversed(self.timestamps):
                if ts <= as_of:
                    return self.insights.get(ts)
            return None

        def get_market_regime(self, sym: str, as_of=None):
            return None

    # Create strategy with params
    strategy = create_strategy("premium_tp_sl", f"opt_{uuid.uuid4().hex[:8]}", params)

    if strategy is None:
        return BacktestResult(
            params=params, timeframe=timeframe, total_trades=0,
            win_rate=0, total_pnl=0, max_drawdown=0,
            sharpe_ratio=0, profit_factor=0, avg_trade_pnl=0
        )

    # Setup execution components
    start_time = df_resampled.index[0]
    market_data = SimpleDataFrameProvider(df_resampled, symbol)
    analytics = SimpleAnalyticsProvider(local_insights)
    clock = ReplayClock(start_time)
    broker = PaperBroker(clock)
    exec_config = ExecutionConfig(mode=ExecutionMode.PAPER)
    execution = ExecutionHandler(clock, broker, exec_config)
    # Disable kill switch for backtesting
    execution._kill_switch_disabled = True
    position_tracker = PositionTracker()

    # Capture trades
    executed_trades = []
    original_process_signal = execution.process_signal

    def wrapped_process_signal(signal: SignalEvent, current_price: float):
        trade = original_process_signal(signal, current_price)
        if trade:
            executed_trades.append(trade)
        return trade

    execution.process_signal = wrapped_process_signal

    # Run backtest
    runner = TradingRunner(
        config=RunnerConfig(
            symbols=[symbol],
            strategy_ids=[strategy.strategy_id],
            log_signals=False,
            log_trades=False,
            warn_on_missing_analytics=False,
            disable_state_update=True
        ),
        market_data_provider=market_data,
        analytics_provider=analytics,
        strategies=[strategy],
        execution_handler=execution,
        position_tracker=position_tracker,
        clock=clock
    )
    runner.run()

    # Pair trades and calculate metrics
    trades_list = []
    entry_price = None
    side = None

    sorted_trades = sorted(executed_trades, key=lambda t: t.timestamp)
    for t in sorted_trades:
        if t.status != TradeStatus.FILLED:
            continue
        if side is None:
            if t.direction == "BUY":
                entry_price = t.price
                side = 1
            elif t.direction == "SELL":
                entry_price = t.price
                side = -1
        else:
            trade_pnl = side * (t.price - entry_price)
            trades_list.append({'pnl': trade_pnl})
            entry_price = None
            side = None

    metrics = calculate_extended_metrics(trades_list)

    return BacktestResult(
        params=params,
        timeframe=timeframe,
        total_trades=metrics['total_trades'],
        win_rate=metrics['win_rate'],
        total_pnl=metrics['total_pnl'],
        max_drawdown=metrics['max_drawdown'],
        sharpe_ratio=metrics['sharpe_ratio'],
        profit_factor=metrics['profit_factor'],
        avg_trade_pnl=metrics['avg_trade_pnl']
    )


def generate_param_combinations(config: OptimizationConfig) -> List[Dict[str, Any]]:
    """Generate all parameter combinations from the grid."""
    combinations = []
    for tp, sl, atr_tp, atr_sl, hold, adx_thresh, macd_inc, rsi_bull in product(
        config.tp_pct_range,
        config.sl_pct_range,
        config.atr_tp_mult_range,
        config.atr_sl_mult_range,
        config.max_hold_bars_range,
        config.adx_threshold_range,
        config.require_macd_increasing_range,
        config.rsi_bullish_threshold_range
    ):
        combinations.append({
            'tp_pct': tp,
            'sl_pct': sl,
            'atr_tp_mult': atr_tp,
            'atr_sl_mult': atr_sl,
            'max_hold_bars': hold,
            # Confluence thresholds
            'adx_threshold': adx_thresh,
            'require_macd_increasing': macd_inc,
            'rsi_bullish_threshold': rsi_bull,
            'rsi_bearish_threshold': 100 - rsi_bull  # Mirror (e.g., 55->45, 60->40)
        })
    return combinations


def load_and_resample_data(symbol: str, days: int, timeframes: List[str]) -> Tuple[Dict[str, pd.DataFrame], datetime, datetime]:
    """Load 1m data and resample for each timeframe."""
    from core.data.duckdb_client import db_cursor

    # Get latest timestamp
    with db_cursor(read_only=True) as conn:
        res = conn.execute(
            "SELECT MAX(timestamp) FROM candles WHERE instrument_key = ?",
            [symbol]
        ).fetchone()

        if not res or not res[0]:
            raise ValueError(f"No data found for {symbol}")

    end_time = res[0]
    start_time = end_time - timedelta(days=days)
    warmup_days = 10
    fetch_start = start_time - timedelta(days=warmup_days)

    # Fetch 1m data
    with db_cursor(read_only=True) as conn:
        df_1m = conn.execute("""
            SELECT timestamp, open, high, low, close, volume
            FROM candles
            WHERE instrument_key = ?
            AND timestamp >= ?
            AND timestamp <= ?
            ORDER BY timestamp
        """, [symbol, fetch_start, end_time]).fetchdf()

    if df_1m.empty:
        raise ValueError(f"No data found for {symbol} in date range")

    # Apply market hours filter (09:15-15:30)
    df_1m['timestamp'] = pd.to_datetime(df_1m['timestamp'])
    market_start = datetime.strptime("09:15", "%H:%M").time()
    market_end = datetime.strptime("15:30", "%H:%M").time()

    df_1m = df_1m[
        (df_1m['timestamp'].dt.time >= market_start) &
        (df_1m['timestamp'].dt.time <= market_end)
    ]

    # Resample for each timeframe
    resampled = {}
    for tf in timeframes:
        tf_code = tf.replace('m', 'min').replace('h', 'H').replace('d', 'D')
        df_tf = df_1m.set_index('timestamp').resample(tf_code).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        resampled[tf] = df_tf

    return resampled, start_time, end_time


def generate_insights_for_timeframe(
    symbol: str,
    df_resampled: pd.DataFrame,
    confluence_config: Optional[Dict[str, Any]] = None
) -> List:
    """Generate confluence insights for a resampled dataframe with configurable thresholds."""
    from core.analytics.confluence_engine import ConfluenceEngine

    engine = ConfluenceEngine(confluence_config)
    df_for_engine = df_resampled.reset_index()
    return engine.generate_insights_bulk(symbol, df_for_engine)


def run_optimization(config: OptimizationConfig) -> pd.DataFrame:
    """Main optimization loop with confluence threshold variations."""
    print(f"\nLoading data for {config.symbol}...")
    resampled_data, start_time, end_time = load_and_resample_data(
        config.symbol, config.days, config.timeframes
    )

    # Generate parameter combinations
    param_combos = generate_param_combinations(config)
    total_combos = len(param_combos) * len(config.timeframes)
    print(f"\nTotal combinations to test: {total_combos}")

    # Group by confluence thresholds to minimize insight regeneration
    confluence_keys = set()
    for params in param_combos:
        key = (
            params['adx_threshold'],
            params['require_macd_increasing'],
            params['rsi_bullish_threshold'],
            params['rsi_bearish_threshold']
        )
        confluence_keys.add(key)

    print(f"Unique confluence configs: {len(confluence_keys)}")

    # Pre-generate insights for each (timeframe, confluence_config) combination
    insights_cache = {}
    for tf, df_tf in resampled_data.items():
        print(f"\n{tf}: {len(df_tf)} bars")
        for conf_key in confluence_keys:
            adx_th, macd_inc, rsi_bull, rsi_bear = conf_key
            confluence_config = {
                'adx_threshold': adx_th,
                'require_macd_increasing': macd_inc,
                'rsi_bullish_threshold': rsi_bull,
                'rsi_bearish_threshold': rsi_bear
            }
            cache_key = (tf, conf_key)
            insights = generate_insights_for_timeframe(config.symbol, df_tf, confluence_config)

            # Count signals
            buy_count = sum(1 for i in insights for ir in i.indicator_results
                          if ir.name == 'premium_flags' and ir.metadata.get('premiumBuy'))
            sell_count = sum(1 for i in insights for ir in i.indicator_results
                           if ir.name == 'premium_flags' and ir.metadata.get('premiumSell'))

            insights_cache[cache_key] = insights
            print(f"  ADX>{adx_th}, MACD_inc={macd_inc}, RSI>{rsi_bull}: {buy_count} buys, {sell_count} sells")

    # Run backtests
    results = []
    completed = 0

    for tf in config.timeframes:
        df_tf = resampled_data[tf]

        for params in param_combos:
            conf_key = (
                params['adx_threshold'],
                params['require_macd_increasing'],
                params['rsi_bullish_threshold'],
                params['rsi_bearish_threshold']
            )
            cache_key = (tf, conf_key)
            insights = insights_cache[cache_key]

            try:
                result = run_single_backtest(df_tf, config.symbol, tf, params, insights)
                results.append(result)
            except Exception as e:
                print(f"Error with {tf} {params}: {e}")

            completed += 1
            if completed % 100 == 0:
                pct = 100 * completed / total_combos
                print(f"Progress: {completed}/{total_combos} ({pct:.1f}%)")

    # Convert to DataFrame and rank
    df_results = pd.DataFrame([
        {
            'timeframe': r.timeframe,
            **r.params,
            'total_trades': r.total_trades,
            'win_rate': r.win_rate,
            'total_pnl': r.total_pnl,
            'max_drawdown': r.max_drawdown,
            'sharpe_ratio': r.sharpe_ratio,
            'profit_factor': r.profit_factor,
            'avg_trade_pnl': r.avg_trade_pnl
        }
        for r in results
    ])

    return df_results.sort_values('sharpe_ratio', ascending=False), start_time, end_time


def save_results(
    opt_run_id: str,
    config: OptimizationConfig,
    results_df: pd.DataFrame,
    start_time: datetime,
    end_time: datetime,
    output_csv: Optional[str] = None
):
    """Save results to DuckDB and CSV."""
    from core.data.duckdb_client import db_cursor

    # Filter valid results (minimum trades)
    valid_results = results_df[results_df['total_trades'] >= 3].copy()

    if valid_results.empty:
        print("WARNING: No valid results with >= 3 trades")
        return

    best_row = valid_results.iloc[0]

    # Save to database
    try:
        with db_cursor() as conn:
            # Insert optimization run summary
            conn.execute("""
                INSERT INTO optimization_runs
                (opt_run_id, strategy_id, symbol, timeframes, start_date, end_date,
                 param_grid, total_combinations, best_sharpe, best_params, status, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETED', CURRENT_TIMESTAMP)
            """, [
                opt_run_id,
                'premium_tp_sl',
                config.symbol,
                ','.join(config.timeframes),
                start_time.date(),
                end_time.date(),
                json.dumps({
                    'tp_pct': config.tp_pct_range,
                    'sl_pct': config.sl_pct_range,
                    'atr_tp_mult': config.atr_tp_mult_range,
                    'atr_sl_mult': config.atr_sl_mult_range,
                    'max_hold_bars': config.max_hold_bars_range
                }),
                len(results_df),
                float(best_row['sharpe_ratio']),
                json.dumps({
                    k: float(best_row[k]) if isinstance(best_row[k], (np.floating, float)) else int(best_row[k])
                    for k in ['tp_pct', 'sl_pct', 'atr_tp_mult', 'atr_sl_mult', 'max_hold_bars']
                })
            ])

            # Insert top 100 individual results
            for idx, row in valid_results.head(100).iterrows():
                conn.execute("""
                    INSERT INTO optimization_results
                    (result_id, opt_run_id, timeframe, params, total_trades, win_rate,
                     total_pnl, max_drawdown, sharpe_ratio, profit_factor, avg_trade_pnl)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    f"{opt_run_id}_{idx}",
                    opt_run_id,
                    row['timeframe'],
                    json.dumps({
                        k: float(row[k]) if isinstance(row[k], (np.floating, float)) else int(row[k])
                        for k in ['tp_pct', 'sl_pct', 'atr_tp_mult', 'atr_sl_mult', 'max_hold_bars']
                    }),
                    int(row['total_trades']),
                    float(row['win_rate']),
                    float(row['total_pnl']),
                    float(row['max_drawdown']),
                    float(row['sharpe_ratio']),
                    float(row['profit_factor']),
                    float(row['avg_trade_pnl'])
                ])
        print(f"\nResults saved to database (run_id: {opt_run_id[:8]}...)")
    except Exception as e:
        print(f"Warning: Could not save to database: {e}")

    # Export to CSV
    csv_path = output_csv or str(ROOT / f"logs/optimization_{opt_run_id[:8]}.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    results_df.to_csv(csv_path, index=False)
    print(f"Results exported to {csv_path}")


def main():
    parser = argparse.ArgumentParser(description="Optimize Premium TP/SL Strategy Parameters")
    parser.add_argument("--symbol", default="NSE_EQ|INE002A01018", help="Symbol to optimize")
    parser.add_argument("--days", type=int, default=30, help="Days of data to use")
    parser.add_argument("--timeframes", nargs='+', default=['15m', '1h', '1d'], help="Timeframes to test")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers")
    parser.add_argument("--output_csv", type=str, help="Output CSV path")
    parser.add_argument("--quick", action="store_true", help="Quick test with reduced grid")

    args = parser.parse_args()

    # Define parameter grid
    if args.quick:
        config = OptimizationConfig(
            symbol=args.symbol,
            days=args.days,
            timeframes=['15m'],
            tp_pct_range=[0.006, 0.01],
            sl_pct_range=[0.003, 0.004],
            atr_tp_mult_range=[2.5, 3.0],
            atr_sl_mult_range=[1.5, 2.0],
            max_hold_bars_range=[15, 20],
            # Relaxed confluence thresholds for quick test
            adx_threshold_range=[15, 20],
            require_macd_increasing_range=[False],  # Relax MACD requirement
            rsi_bullish_threshold_range=[55],
            n_workers=args.workers
        )
    else:
        config = OptimizationConfig(
            symbol=args.symbol,
            days=args.days,
            timeframes=args.timeframes,
            tp_pct_range=[0.004, 0.006, 0.008, 0.01, 0.012],
            sl_pct_range=[0.002, 0.003, 0.004, 0.005],
            atr_tp_mult_range=[2.0, 2.5, 3.0, 3.5, 4.0],
            atr_sl_mult_range=[1.0, 1.5, 2.0, 2.5],
            max_hold_bars_range=[10, 15, 20, 30, 50],
            # Confluence threshold ranges to optimize
            adx_threshold_range=[15, 20, 25],
            require_macd_increasing_range=[True, False],
            rsi_bullish_threshold_range=[50, 55, 60],
            n_workers=args.workers
        )

    total_combos = (
        len(config.tp_pct_range) *
        len(config.sl_pct_range) *
        len(config.atr_tp_mult_range) *
        len(config.atr_sl_mult_range) *
        len(config.max_hold_bars_range) *
        len(config.adx_threshold_range) *
        len(config.require_macd_increasing_range) *
        len(config.rsi_bullish_threshold_range) *
        len(config.timeframes)
    )

    print("=" * 70)
    print("PREMIUM TP/SL PARAMETER OPTIMIZATION")
    print("=" * 70)
    print(f"Symbol:      {config.symbol}")
    print(f"Days:        {config.days}")
    print(f"Timeframes:  {config.timeframes}")
    print(f"Grid Size:   {total_combos} combinations")
    print(f"ADX Thresholds: {config.adx_threshold_range}")
    print(f"MACD Increasing: {config.require_macd_increasing_range}")
    print(f"RSI Bullish: {config.rsi_bullish_threshold_range}")
    print("-" * 70)

    opt_run_id = str(uuid.uuid4())

    results_df, start_time, end_time = run_optimization(config)

    # Display results by timeframe
    print("\n" + "=" * 70)
    print("OPTIMIZATION RESULTS")
    print("=" * 70)

    for tf in config.timeframes:
        tf_results = results_df[results_df['timeframe'] == tf]
        if tf_results.empty:
            continue

        valid_tf = tf_results[tf_results['total_trades'] >= 3]
        if valid_tf.empty:
            print(f"\n{tf}: No valid results (< 3 trades)")
            continue

        best = valid_tf.iloc[0]
        print(f"\n{tf} - Best Parameters:")
        print(f"  TP: {best['tp_pct']:.3f} ({best['tp_pct']*100:.2f}%)")
        print(f"  SL: {best['sl_pct']:.3f} ({best['sl_pct']*100:.2f}%)")
        print(f"  ATR TP Mult: {best['atr_tp_mult']:.1f}")
        print(f"  ATR SL Mult: {best['atr_sl_mult']:.1f}")
        print(f"  Max Hold Bars: {int(best['max_hold_bars'])}")
        print(f"  --- Confluence Thresholds ---")
        print(f"  ADX Threshold: {best['adx_threshold']}")
        print(f"  MACD Increasing: {best['require_macd_increasing']}")
        print(f"  RSI Bullish: >{best['rsi_bullish_threshold']}")
        print(f"  --- Performance ---")
        print(f"  Trades: {int(best['total_trades'])}")
        print(f"  Win Rate: {best['win_rate']:.1f}%")
        print(f"  Total PnL: {best['total_pnl']:.4f}")
        print(f"  Sharpe: {best['sharpe_ratio']:.2f}")
        print(f"  Profit Factor: {best['profit_factor']:.2f}")

    # Save results
    save_results(opt_run_id, config, results_df, start_time, end_time, args.output_csv)

    print("\n" + "=" * 70)
    print("Optimization complete!")
    print(f"Run ID: {opt_run_id}")
    print("=" * 70)


if __name__ == "__main__":
    main()
