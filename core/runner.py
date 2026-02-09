"""
TradingRunner - System Orchestrator
----------------------------------
Orchestrates data flow between providers, strategies, and execution.
Uses DatabaseManager for persistence and state tracking.
"""
from typing import List, Dict, Optional, Any
from datetime import datetime
from dataclasses import dataclass, replace
import traceback
import time

from core.database.providers.base import MarketDataProvider, AnalyticsProvider
from core.strategies.base import BaseStrategy, StrategyContext
from core.execution.handler import ExecutionHandler
from core.execution.position_tracker import PositionTracker
from core.events import OHLCVBar, SignalEvent, SignalType, TradeEvent
from core.clock import Clock
from core.database.manager import DatabaseManager
from core.database.legacy_adapter import save_signal
from core.messaging.telemetry import TelemetryPublisher
from core.logging import setup_logger

logger = setup_logger("trading_runner")

@dataclass
class RunnerConfig:
    """Configuration for TradingRunner."""
    symbols: List[str]                      
    strategy_ids: List[str]                 
    max_bars: Optional[int] = None          
    stop_on_error: bool = False             
    log_signals: bool = True                
    log_trades: bool = True                 
    warn_on_missing_analytics: bool = False 
    disable_state_update: bool = False      


class TradingRunner:
    """
    System orchestrator - coordinates data → strategies → execution.
    """
    
    def __init__(
        self,
        config: RunnerConfig,
        db_manager: DatabaseManager,
        market_data_provider: MarketDataProvider,
        analytics_provider: AnalyticsProvider,
        strategies: List[BaseStrategy],
        execution_handler: ExecutionHandler,
        position_tracker: PositionTracker,
        clock: Clock,
        telemetry: Optional[TelemetryPublisher] = None
    ):
        self.config = config
        self.db_manager = db_manager
        self.market_data = market_data_provider
        self.analytics = analytics_provider
        self.strategies = strategies
        self.execution = execution_handler
        self.positions = position_tracker
        self.clock = clock
        self.telemetry = telemetry
        
        self._is_running = False
        self._bar_count = 0
        self._signal_count = 0
        self._trade_count = 0
        self._disabled_strategies: set = set()
        # Track open positions with their exit parameters (TP/SL/time-stop)
        self._open_exit_params: Dict[str, Dict] = {}  # symbol -> {sl, tp, bars_held, max_bars, strategy_id, direction}

        self._validate_setup()
    
    @property
    def is_running(self) -> bool:
        return self._is_running

    def _validate_setup(self) -> None:
        if not self.config.symbols:
            raise ValueError("No symbols configured")
        if not self.strategies:
            raise ValueError("No strategies configured")
        
        strategy_ids = {s.strategy_id for s in self.strategies}
        missing = set(self.config.strategy_ids) - strategy_ids
        if missing:
            raise ValueError(f"Strategies not found: {missing}")
    
    def run(self) -> Dict[str, Any]:
        logger.info("=" * 70)
        logger.info("TRADING RUNNER - Starting")
        logger.info("=" * 70)
        logger.info(f"Symbols: {self.config.symbols}")
        logger.info(f"Strategies: {[s.strategy_id for s in self.strategies]}")
        logger.info(f"Execution mode: {self.execution.config.mode.value}")
        logger.info("-" * 70)

        self._is_running = True

        try:
            while self._is_running:
                if self.config.max_bars and self._bar_count >= self.config.max_bars:
                    logger.info(f"Reached max bars: {self.config.max_bars}")
                    break

                any_bar_processed = False
                for symbol in self.config.symbols:
                    if not self._is_running:
                        break

                    if self._process_symbol(symbol):
                        any_bar_processed = True

                if not any_bar_processed:
                    is_streaming = any(self.market_data.is_data_available(s) for s in self.config.symbols)
                    if is_streaming:
                        # Reduced polling frequency from 100Hz to 2Hz to prevent DuckDB lock conflicts
                        # Live 1m bars only update every 60s, so 0.5s polling is sufficient
                        time.sleep(0.5)
                        continue
                    else:
                        logger.info("All market data exhausted.")
                        break

                self._bar_count += 1

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.error(f"ERROR in runner: {e}")
            logger.error(traceback.format_exc())
            raise
        finally:
            self._is_running = False

        return self._get_stats()
    
    def _check_exit_conditions(self, symbol: str, bar: OHLCVBar) -> None:
        """Check if open positions need to be exited based on TP/SL/time-stop."""
        if symbol not in self._open_exit_params:
            return

        params = self._open_exit_params[symbol]
        params['bars_held'] += 1

        sl = params.get('sl')
        tp = params.get('tp')
        max_bars = params.get('max_bars', 0)
        direction = params.get('direction', 'LONG')
        entry_price = params.get('entry_price')
        atr = params.get('atr_at_entry', 0)
        exit_reason = None

        if direction == 'LONG':
            if sl and bar.low <= sl:
                exit_reason = 'SL'
            elif tp and bar.high >= tp:
                exit_reason = 'TP'
        else:  # SHORT
            if sl and bar.high >= sl:
                exit_reason = 'SL'
            elif tp and bar.low <= tp:
                exit_reason = 'TP'

        if not exit_reason and max_bars and params['bars_held'] >= max_bars:
            exit_reason = 'TIME_STOP'

        if exit_reason:
            exit_price = bar.close
            if exit_reason == 'SL':
                exit_price = sl
            elif exit_reason == 'TP':
                exit_price = tp

            exit_signal = SignalEvent(
                strategy_id=params.get('strategy_id', 'exit_monitor'),
                symbol=symbol,
                timestamp=bar.timestamp,
                signal_type=SignalType.EXIT,
                confidence=1.0,
                metadata={'exit_reason': exit_reason, 'signal_id': f"exit_{symbol}_{bar.timestamp.isoformat()}"}
            )

            trade = self.execution.process_signal(exit_signal, exit_price)
            if trade:
                self._trade_count += 1
                if self.config.log_trades:
                    self._log_trade(trade)
                self.positions.apply_trade(trade)
                logger.info(f"[EXIT] {symbol} {exit_reason} at {exit_price:.2f} after {params['bars_held']} bars")

                # Notify strategy of exit for cooldown
                for strat in self.strategies:
                    if strat.strategy_id == params.get('strategy_id') and hasattr(strat, 'record_exit'):
                        strat.record_exit(symbol)

                del self._open_exit_params[symbol]

    def _process_symbol(self, symbol: str) -> bool:
        bar = self.market_data.get_next_bar(symbol)
        if not bar:
            return False

        if hasattr(self.clock, 'set_time'):
            self.clock.set_time(bar.timestamp)

        # Check TP/SL/time-stop exits before processing new signals
        self._check_exit_conditions(symbol, bar)

        analytics_snapshot = self.analytics.get_latest_snapshot(symbol, as_of=bar.timestamp)
        current_position = self.positions.get_position_quantity(symbol)
        market_regime = self.analytics.get_market_regime(symbol, as_of=bar.timestamp)

        for strategy in self.strategies:
            if strategy.strategy_id in self._disabled_strategies:
                continue
            if not strategy.is_enabled:
                continue
            
            try:
                context = StrategyContext(
                    symbol=symbol,
                    current_position=current_position,
                    analytics_snapshot=analytics_snapshot,
                    market_regime=market_regime,
                    strategy_params=strategy.config
                )
                
                signal = strategy.process_bar(bar, context)
                
                if signal:
                    # Check if signal already has an ID in metadata
                    signal_id = signal.metadata.get('signal_id')
                    if not signal_id:
                        from hashlib import sha256
                        raw_id = f"{symbol}_{strategy.strategy_id}_{bar.timestamp.isoformat()}"
                        signal_id = sha256(raw_id.encode()).hexdigest()
                    
                    signal_with_id = replace(signal, metadata={**signal.metadata, 'signal_id': signal_id})
                    self._signal_count += 1
                    
                    if self.config.log_signals:
                        self._log_signal(signal_with_id, bar.close)
                    
                    # Persist signal
                    save_signal(signal_with_id)

                    trade = self.execution.process_signal(signal_with_id, bar.close)

                    if trade:
                        self._trade_count += 1
                        if self.config.log_trades:
                            self._log_trade(trade)
                        self.positions.apply_trade(trade)

                        # Track exit parameters for TP/SL/time-stop monitoring
                        new_pos = self.positions.get_position_quantity(symbol)
                        if new_pos != 0 and symbol not in self._open_exit_params:
                            meta = signal.metadata or {}
                            self._open_exit_params[symbol] = {
                                'sl': meta.get('sl'),
                                'tp': meta.get('tp'),
                                'max_bars': meta.get('h_bars', 0),
                                'bars_held': 0,
                                'strategy_id': strategy.strategy_id,
                                'direction': 'LONG' if trade.direction == 'BUY' else 'SHORT',
                                'entry_price': trade.price,
                                'atr_at_entry': meta.get('atr_at_event', 0),
                            }

                        # Notify strategy of exit for cooldown tracking
                        if new_pos == 0 and hasattr(strategy, 'record_exit'):
                            strategy.record_exit(symbol)
                            if symbol in self._open_exit_params:
                                del self._open_exit_params[symbol]
                    
                    self._update_runner_state(symbol, strategy, signal_with_id, bar)
                else:
                    self._update_runner_state(symbol, strategy, None, bar)
                        
            except Exception as e:
                error_msg = f"Strategy {strategy.strategy_id} error on {symbol}: {e}"
                logger.error(f"[ERROR] {error_msg}")
                logger.error(traceback.format_exc())
                self._disabled_strategies.add(strategy.strategy_id)
                if self.config.stop_on_error:
                    raise RuntimeError(error_msg)
                    
        return True
    
    def _log_signal(self, signal: SignalEvent, price: float) -> None:
        msg = (f"[SIGNAL] {signal.strategy_id:20} | {signal.symbol:10} | "
               f"{signal.signal_type.value:6} | conf={signal.confidence:.2f} | "
               f"price={price:.2f} | time={self.clock.now()}")
        logger.info(msg)
        if self.telemetry:
            self.telemetry.publish_log("INFO", msg)
    
    def _log_trade(self, trade: TradeEvent) -> None:
        status = trade.status.value if hasattr(trade.status, 'value') else str(trade.status)
        msg = (f"[TRADE]  {trade.symbol:10} | {trade.direction:5} | "
               f"qty={trade.quantity:8.2f} | price={trade.price:8.2f} | "
               f"status={status} | time={self.clock.now()}")
        logger.info(msg)
        if self.telemetry:
            self.telemetry.publish_log("INFO", msg)
    
    def _get_stats(self) -> Dict[str, Any]:
        return {
            'bars_processed': self._bar_count,
            'signals_generated': self._signal_count,
            'trades_executed': self._trade_count,
            'strategies_disabled': list(self._disabled_strategies),
            'current_positions': {
                symbol: pos.quantity 
                for symbol, pos in self.positions.get_all_positions().items()
            },
            'execution_mode': self.execution.config.mode.value,
            'end_time': str(self.clock.now())
        }
    
    def stop(self) -> None:
        logger.info(f"[RUNNER] Stopping at {self.clock.now()}...")
        self._is_running = False
    
    def _update_runner_state(self, symbol: str, strategy: BaseStrategy,
                              signal: Optional[SignalEvent], bar: OHLCVBar):
        """Persist runner state to config database."""
        if self.config.disable_state_update:
            return

        logger.debug(f"Updating runner_state for {symbol}/{strategy.strategy_id}")
        status = "RUNNING"
        if strategy.strategy_id in self._disabled_strategies:
            status = "DISABLED"
        elif not strategy.is_enabled:
            status = "DISABLED"

        signal_state = "PENDING"
        current_bias = "NEUTRAL"
        confidence = 0.0

        if signal:
            signal_state = "TRIGGERED"
            current_bias = signal.signal_type.value
            confidence = signal.confidence

        # Determine timeframe from strategy config if possible
        timeframe = strategy.config.get("preferred_timeframe", "1m")
        if not timeframe and "bar_minutes" in strategy.config:
            timeframe = f"{strategy.config['bar_minutes']}m"

        try:
            # Ensure timestamp is SQLite compatible
            ts = bar.timestamp
            if hasattr(ts, 'to_pydatetime'):
                ts = ts.to_pydatetime()

            with self.db_manager.config_writer() as conn:
                conn.execute("""
                    INSERT INTO runner_state
                    (symbol, strategy_id, timeframe, current_bias, signal_state,
                     confidence, last_bar_ts, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT (symbol, strategy_id) DO UPDATE SET
                        timeframe = EXCLUDED.timeframe,
                        current_bias = EXCLUDED.current_bias,
                        signal_state = EXCLUDED.signal_state,
                        confidence = EXCLUDED.confidence,
                        last_bar_ts = EXCLUDED.last_bar_ts,
                        status = EXCLUDED.status,
                        updated_at = CURRENT_TIMESTAMP
                """, [
                    symbol,
                    strategy.strategy_id,
                    timeframe,
                    current_bias,
                    signal_state,
                    confidence,
                    ts,
                    status
                ])
                logger.debug(f"✓ Runner state updated: {symbol} | {strategy.strategy_id} | {current_bias} | {confidence:.2f}")
        except Exception as e:
            logger.error(f"Failed to update runner state for {symbol}/{strategy.strategy_id}: {e}", exc_info=True)
