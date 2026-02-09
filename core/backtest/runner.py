"""
Refactored Backtest Runner
-------------------------
Handles execution of a backtest run with strict isolation.
"""
import uuid
import json
import logging
import joblib
import pandas as pd
from datetime import datetime, date
from typing import List, Dict, Optional, Any
from pathlib import Path

from core.clock import ReplayClock
from core.runner import TradingRunner, RunnerConfig
from core.database.providers.market_data import DuckDBMarketDataProvider
from core.database.providers.analytics import DuckDBAnalyticsProvider
from core.execution.handler import ExecutionHandler, ExecutionConfig, ExecutionMode
from core.execution.position_tracker import PositionTracker
from core.brokers.paper_broker import PaperBroker
from core.strategies.registry import create_strategy
from core.strategies.precomputed_signals import PrecomputedSignalStrategy
from core.strategies.pixityAI_batch_events import batch_generate_events, batch_generate_events_with_quality_filter
from core.execution.pixityAI_risk_engine import PixityAIRiskEngine
from core.analytics.resampler import resample_ohlcv
from core.analytics.populator import AnalyticsPopulator
from core.database.manager import DatabaseManager
from core.database import schema

logger = logging.getLogger(__name__)

class BacktestRunner:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def run(
        self,
        strategy_id: str,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        initial_capital: float = 100000.0,
        strategy_params: Optional[Dict] = None,
        timeframe: str = '1m',
        run_id: Optional[str] = None
    ) -> str:
        if strategy_id == "pixityAI_meta":
            return self._run_pixityAI_batch(
                strategy_id, symbol, start_time, end_time, initial_capital, strategy_params, timeframe, run_id
            )
        else:
            return self._run_standard(
                strategy_id, symbol, start_time, end_time, initial_capital, strategy_params, timeframe, run_id
            )

    def _run_standard(
        self,
        strategy_id: str,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        initial_capital: float = 100000.0,
        strategy_params: Optional[Dict] = None,
        timeframe: str = '1m',
        run_id: Optional[str] = None
    ) -> str:
        run_id = run_id or str(uuid.uuid4())
        strategy_params = strategy_params or {}

        # 1. Record/Update state in Index
        with self.db.backtest_index_writer() as conn:
            conn.execute("""
                INSERT INTO backtest_runs (run_id, strategy_id, symbol, start_date, end_date, params, status)
                VALUES (?, ?, ?, ?, ?, ?, 'RUNNING')
                ON CONFLICT(run_id) DO UPDATE SET
                    status = 'RUNNING',
                    start_date = excluded.start_date,
                    end_date = excluded.end_date
            """, [run_id, strategy_id, symbol, start_time.date(), end_time.date(), json.dumps(strategy_params)])

        try:
            # 2. Populate Analytics for the range
            populator = AnalyticsPopulator(db_manager=self.db)
            populator.update_all([symbol], start_date=start_time, end_date=end_time, timeframe=timeframe)

            # 3. Setup Components
            clock = ReplayClock(start_time)
            
            # Use refactored providers
            market_data = DuckDBMarketDataProvider(
                symbols=[symbol],
                db_manager=self.db,
                timeframe=timeframe,
                start_time=start_time,
                end_time=end_time
            )
            
            analytics = DuckDBAnalyticsProvider(db_manager=self.db)
            analytics.pre_load(symbol, start_time, end_time)

            strategy = create_strategy(strategy_id, f"{strategy_id}_bt", strategy_params)
            if not strategy:
                raise ValueError(f"Strategy {strategy_id} not found")
                
            broker = PaperBroker(clock)
            
            exec_config = ExecutionConfig(
                mode=ExecutionMode.PAPER,
                max_drawdown_limit=0.99 
            )
            execution = ExecutionHandler(
                db_manager=self.db,
                clock=clock,
                broker=broker,
                config=exec_config,
                initial_capital=initial_capital,
                load_db_state=False
            )
            # Disable idempotency guard for backtests — each run is isolated
            execution._is_signal_already_executed = lambda signal_id: False

            position_tracker = PositionTracker()

            runner = TradingRunner(
                config=RunnerConfig(
                    symbols=[symbol],
                    strategy_ids=[strategy.strategy_id],
                    disable_state_update=True
                ),
                db_manager=self.db,
                market_data_provider=market_data,
                analytics_provider=analytics,
                strategies=[strategy],
                execution_handler=execution,
                position_tracker=position_tracker,
                clock=clock
            )

            # 4. Run loop
            runner.run()

            # 5. Record Results to isolated DuckDB
            self._save_run_results(run_id, symbol, execution)

            # 6. Update Index to COMPLETED
            metrics = self._calculate_metrics(execution, run_id)
            with self.db.backtest_index_writer() as conn:
                conn.execute("""
                    UPDATE backtest_runs
                    SET total_trades = ?, win_rate = ?, total_pnl = ?, max_drawdown = ?, status = 'COMPLETED'
                    WHERE run_id = ?
                """, [metrics['total_trades'], metrics['win_rate'], metrics['total_pnl'], metrics['max_drawdown'], run_id])

            return run_id

        except Exception as e:
            logger.error(f"Backtest {run_id} failed: {e}", exc_info=True)
            try:
                with self.db.backtest_index_writer() as conn:
                    conn.execute("UPDATE backtest_runs SET status = 'FAILED', error_message = ? WHERE run_id = ?", [str(e)[:500], run_id])
            except Exception as db_err:
                logger.error(f"Backtest {run_id}: also failed to write FAILED status: {db_err}")
            raise

    def _run_pixityAI_batch(
        self,
        strategy_id: str,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        initial_capital: float = 100000.0,
        strategy_params: Optional[Dict] = None,
        timeframe: str = '1m',
        run_id: Optional[str] = None
    ) -> str:
        run_id = run_id or str(uuid.uuid4())
        strategy_params = strategy_params or {}

        # 1. Record/Update state in Index
        with self.db.backtest_index_writer() as conn:
            conn.execute("""
                INSERT INTO backtest_runs (run_id, strategy_id, symbol, start_date, end_date, params, status)
                VALUES (?, ?, ?, ?, ?, ?, 'RUNNING')
                ON CONFLICT(run_id) DO UPDATE SET
                    status = 'RUNNING',
                    start_date = excluded.start_date,
                    end_date = excluded.end_date
            """, [run_id, strategy_id, symbol, start_time.date(), end_time.date(), json.dumps(strategy_params)])

        try:
            # 2. Setup Components
            clock = ReplayClock(start_time)
            
            market_data_provider = DuckDBMarketDataProvider(
                symbols=[symbol],
                db_manager=self.db,
                timeframe=timeframe,
                start_time=start_time,
                end_time=end_time
            )
            
            # Load pixityAI config (needed for batch params + model path + thresholds)
            config_path = Path("core/models/pixityAI_config.json")
            if config_path.exists():
                with open(config_path, 'r') as f:
                    pixity_config = json.load(f)
            else:
                pixity_config = {}

            # Per-symbol model selection: strategy_params override > symbol-specific > config
            slug = symbol.split("|")[-1].replace(" ", "").lower()
            if strategy_params.get('model_path'):
                model_path = strategy_params['model_path']
                logger.info(f"Using param-override model: {model_path}")
            else:
                per_symbol_model = f"core/models/pixityAI_{slug}_{timeframe}.joblib"
                if Path(per_symbol_model).exists():
                    model_path = per_symbol_model
                    logger.info(f"Using per-symbol model: {model_path}")
                else:
                    model_path = pixity_config.get("model_path", per_symbol_model)
                    logger.info(f"Using config model: {model_path}")

            # Load 1m data with 90-day warmup for indicator + daily trend computation
            from core.database.queries import MarketDataQuery
            from core.events import SignalType
            from datetime import timedelta
            query = MarketDataQuery(self.db)
            warmup_start = start_time - timedelta(days=90)
            df_1m = query.get_ohlcv(symbol, start_time=warmup_start, end_time=end_time, timeframe="1m")

            if df_1m.empty:
                raise ValueError(f"No 1m data found for {symbol}")

            df_1m['timestamp'] = pd.to_datetime(df_1m['timestamp'])
            df_1m.set_index('timestamp', inplace=True)
            df_resampled = resample_ohlcv(df_1m, timeframe)

            # 3. Batch Generate Events (using config params)
            bar_minutes = 1
            if timeframe.endswith('m'): bar_minutes = int(timeframe[:-1])
            elif timeframe.endswith('h'): bar_minutes = int(timeframe[:-1]) * 60
            elif timeframe.endswith('d'): bar_minutes = 1440

            # Option to use Signal Quality Filter (recommended, replaces anti-predictive meta-model)
            use_signal_quality = strategy_params.get('use_signal_quality_filter', False)

            if use_signal_quality:
                logger.info(f"Using Signal Quality Filter pipeline...")
                signal_config_path = strategy_params.get('signal_quality_config', 'core/models/signal_quality_config.json')

                raw_events, filter_stats = batch_generate_events_with_quality_filter(
                    df_resampled,
                    config_path=signal_config_path,
                    swing_period=pixity_config.get('swing_period', 5),
                    reversion_k=pixity_config.get('reversion_k', 2.0),
                    time_stop_bars=pixity_config.get('time_stop_bars', 12),
                    bar_minutes=bar_minutes
                )

                logger.info(
                    f"Signal Quality Filter: {filter_stats.get('filtered_event_count', len(raw_events))}/"
                    f"{filter_stats.get('raw_event_count', len(raw_events))} events passed "
                    f"({filter_stats.get('acceptance_rate_pct', 100.0):.1f}% acceptance)"
                )

                # Store filter stats in run params for analysis
                strategy_params['filter_stats'] = filter_stats
            else:
                raw_events = batch_generate_events(
                    df_resampled,
                    swing_period=pixity_config.get('swing_period', 5),
                    reversion_k=pixity_config.get('reversion_k', 2.0),
                    time_stop_bars=pixity_config.get('time_stop_bars', 12),
                    bar_minutes=bar_minutes
                )

            # Filter events to backtest date range only (warmup data was for indicators)
            raw_events = [e for e in raw_events if e.timestamp >= start_time]
            logger.info(f"Batch generated {len(raw_events)} raw events for {symbol} ({timeframe})")

            # 4. Filter through Meta-Model
            skip_filter = strategy_params.get('skip_meta_model', False)
            if skip_filter or not Path(model_path).exists():
                logger.info(f"Meta-model filter {'skipped by param' if skip_filter else 'not found'}, using all {len(raw_events)} events")
                approved_events = raw_events
            else:
                from dataclasses import replace
                model = joblib.load(model_path)
                features = ["vwap_dist", "ema_slope", "atr_pct", "adx", "hour", "minute", "vol_z"]
                approved_events = []
                for event in raw_events:
                    feat_dict = {f: event.metadata.get(f, 0.0) for f in features}
                    X = pd.DataFrame([feat_dict])
                    prob = model.predict_proba(X)[0][1]
                    threshold = pixity_config.get(
                        'long_threshold' if event.signal_type == SignalType.BUY else 'short_threshold',
                        0.45
                    )
                    if prob >= threshold:
                        new_event = replace(event, confidence=prob)
                        approved_events.append(new_event)

            logger.info(f"Meta-model approved {len(approved_events)}/{len(raw_events)} events")

            # 5. Enrich via Risk Engine and unique Signal IDs
            from hashlib import sha256
            risk_engine = PixityAIRiskEngine(
                risk_per_trade=pixity_config.get("risk_per_trade", 500.0),
                max_daily_trades=pixity_config.get("max_daily_trades", 10)
            )
            for event in approved_events:
                sizing = risk_engine.calculate_position(event, initial_capital)
                event.metadata.update(sizing)

                # Ensure h_bars is present (fallback from config)
                if 'h_bars' not in event.metadata:
                    event.metadata['h_bars'] = pixity_config.get("time_stop_bars", 12)

                # Generate run-scoped unique Signal ID to bypass idempotency guard
                raw_id = f"{run_id}_{symbol}_{event.timestamp.isoformat()}_{event.signal_type.value}"
                event.metadata['signal_id'] = sha256(raw_id.encode()).hexdigest()

            # 5b. Metadata integrity checks — filter out invalid signals
            valid_events = []
            for event in approved_events:
                meta = event.metadata
                if meta.get('quantity', 0) <= 0:
                    logger.warning(f"Skipping signal at {event.timestamp}: quantity={meta.get('quantity')}")
                    continue
                if not meta.get('sl') or not meta.get('tp'):
                    logger.warning(f"Skipping signal at {event.timestamp}: sl={meta.get('sl')}, tp={meta.get('tp')}")
                    continue
                if meta.get('h_bars', 0) < 1:
                    logger.warning(f"Skipping signal at {event.timestamp}: h_bars={meta.get('h_bars')}")
                    continue
                valid_events.append(event)

            if len(valid_events) < len(approved_events):
                logger.info(f"Metadata validation: {len(valid_events)}/{len(approved_events)} signals passed")
            logger.info(f"Final signal count: {len(valid_events)} tradeable signals")

            # 6. Setup Runner with Precomputed signals
            strategy = PrecomputedSignalStrategy(strategy_id, valid_events, strategy_params)
            broker = PaperBroker(clock)
            exec_config = ExecutionConfig(mode=ExecutionMode.PAPER, max_drawdown_limit=0.99)
            execution = ExecutionHandler(
                db_manager=self.db,
                clock=clock,
                broker=broker,
                config=exec_config,
                initial_capital=initial_capital,
                load_db_state=False
            )
            # Disable idempotency guard for backtests — each run is isolated
            execution._is_signal_already_executed = lambda signal_id: False

            position_tracker = PositionTracker()
            analytics = DuckDBAnalyticsProvider(db_manager=self.db) # Placeholder

            runner = TradingRunner(
                config=RunnerConfig(
                    symbols=[symbol],
                    strategy_ids=[strategy_id],
                    disable_state_update=True
                ),
                db_manager=self.db,
                market_data_provider=market_data_provider,
                analytics_provider=analytics,
                strategies=[strategy],
                execution_handler=execution,
                position_tracker=position_tracker,
                clock=clock
            )

            runner.run()
            
            # 7. Save results
            self._save_run_results(run_id, symbol, execution)
            metrics = self._calculate_metrics(execution, run_id)
            with self.db.backtest_index_writer() as conn:
                conn.execute("""
                    UPDATE backtest_runs
                    SET total_trades = ?, win_rate = ?, total_pnl = ?, max_drawdown = ?, status = 'COMPLETED'
                    WHERE run_id = ?
                """, [metrics['total_trades'], metrics['win_rate'], metrics['total_pnl'], metrics['max_drawdown'], run_id])

            return run_id

        except Exception as e:
            logger.error(f"Batch Backtest {run_id} failed: {e}", exc_info=True)
            try:
                with self.db.backtest_index_writer() as conn:
                    conn.execute("UPDATE backtest_runs SET status = 'FAILED', error_message = ? WHERE run_id = ?", [str(e)[:500], run_id])
            except Exception as db_err:
                logger.error(f"Backtest {run_id}: also failed to write FAILED status: {db_err}")
            raise

    def _save_run_results(self, run_id: str, symbol: str, execution: ExecutionHandler):
        """Saves detailed trades to run-specific DuckDB file."""
        with self.db.backtest_writer(run_id) as conn:
            conn.execute(schema.BACKTEST_RUN_TRADES_SCHEMA)
            
            history = execution.get_trade_history()
            backtest_trades = []
            open_trades = {}

            for trade in history:
                if symbol not in open_trades:
                    direction = "LONG" if trade.direction == "BUY" else "SHORT"
                    open_trades[symbol] = (trade, direction)
                else:
                    entry, direction = open_trades.pop(symbol)
                    pnl = (trade.price - entry.price) * entry.quantity if direction == "LONG" else (entry.price - trade.price) * entry.quantity
                    fees = entry.fees + trade.fees
                    
                    backtest_trades.append((
                        f"bt_{entry.trade_id}",
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
                    ))

            if backtest_trades:
                conn.executemany("INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", backtest_trades)

    def _calculate_metrics(self, execution: ExecutionHandler, run_id: Optional[str] = None) -> Dict[str, Any]:
        """Calculate metrics from the paired trades stored in the run DB."""
        total_trades = 0
        win_rate = 0.0
        total_pnl = 0.0
        max_drawdown = execution.metrics.max_drawdown_pct * 100.0

        if run_id:
            try:
                with self.db.backtest_reader(run_id) as conn:
                    trades = conn.execute("SELECT pnl FROM trades").fetchall()
                    if trades:
                        pnls = [t[0] for t in trades]
                        total_trades = len(pnls)
                        total_pnl = sum(pnls)
                        wins = sum(1 for p in pnls if p > 0)
                        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
            except Exception:
                total_trades = execution.metrics.trades_executed // 2
                total_pnl = execution.metrics.daily_pnl

        return {
            'total_trades': total_trades,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'max_drawdown': max_drawdown
        }
