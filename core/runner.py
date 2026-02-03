"""
TradingRunner - System Orchestrator

The TradingRunner is the ONLY component that coordinates data flow.
It does NOT contain strategy logic, execution logic, or analytics computation.

RESPONSIBILITIES:
1. Pull market data from provider
2. Pull analytics snapshots from provider  
3. Track positions via PositionTracker
4. Invoke strategies in deterministic order
5. Hand signals to execution
6. Record trades

RULES:
- No strategy logic (strategies decide)
- No execution logic (execution acts)
- No analytics computation (analytics is pre-computed)
- No discretionary decisions
- Deterministic iteration order
- Single-threaded (Phase 3)
"""
from typing import List, Dict, Optional, Any
from datetime import datetime
from dataclasses import dataclass, replace
import traceback
import time

from core.data.market_data_provider import MarketDataProvider
from core.data.analytics_provider import AnalyticsProvider
from core.strategies.base import BaseStrategy, StrategyContext
from core.execution.handler import ExecutionHandler
from core.execution.position_tracker import PositionTracker
from core.events import OHLCVBar, SignalEvent, TradeEvent
from core.clock import Clock


@dataclass
class RunnerConfig:
    """Configuration for TradingRunner."""
    symbols: List[str]                      # Symbols to trade
    strategy_ids: List[str]                 # Strategy IDs to run
    max_bars: Optional[int] = None          # Max bars to process (None = unlimited)
    stop_on_error: bool = False             # Stop if any strategy errors
    log_signals: bool = True                # Log all signals
    log_trades: bool = True                 # Log all trades
    warn_on_missing_analytics: bool = False # Log warning if analytics snapshot is missing


class TradingRunner:
    """
    System orchestrator - coordinates data → strategies → execution.
    
    GUARANTEES:
    - No strategy logic
    - No execution logic  
    - No analytics computation
    - Deterministic iteration order
    - Single-threaded (Phase 3)
    - Runner does not advance time.
    - Runner only observes time via injected Clock.
    """
    
    def __init__(
        self,
        config: RunnerConfig,
        market_data_provider: MarketDataProvider,
        analytics_provider: AnalyticsProvider,
        strategies: List[BaseStrategy],
        execution_handler: ExecutionHandler,
        position_tracker: PositionTracker,
        clock: Clock
    ):
        self.config = config
        self.market_data = market_data_provider
        self.analytics = analytics_provider
        self.strategies = strategies
        self.execution = execution_handler
        self.positions = position_tracker
        self.clock = clock
        
        # State tracking
        self._is_running = False
        self._bar_count = 0
        self._signal_count = 0
        self._trade_count = 0
        self._disabled_strategies: set = set()
        
        # Validation
        self._validate_setup()
    
    def _validate_setup(self) -> None:
        """Validate that all components are properly configured."""
        if not self.config.symbols:
            raise ValueError("No symbols configured")
        
        if not self.strategies:
            raise ValueError("No strategies configured")
        
        # Check strategy IDs match config
        strategy_ids = {s.strategy_id for s in self.strategies}
        missing = set(self.config.strategy_ids) - strategy_ids
        if missing:
            raise ValueError(f"Strategies not found: {missing}")
    
    def run(self) -> Dict[str, Any]:
        """
        Run the main trading loop.
        """
        print("=" * 70)
        print("TRADING RUNNER - Starting")
        print("=" * 70)
        print(f"Symbols: {self.config.symbols}")
        print(f"Strategies: {[s.strategy_id for s in self.strategies]}")
        print(f"Execution mode: {self.execution.config.mode.value}")
        print("-" * 70)
        
        self._is_running = True
        
        try:
            while self._is_running:
                # Check if we've reached max bars
                if self.config.max_bars and self._bar_count >= self.config.max_bars:
                    print(f"\nReached max bars: {self.config.max_bars}")
                    break
                
                # Track if any bar was processed in this iteration
                any_bar_processed = False
                
                # Process one bar per symbol
                for symbol in self.config.symbols:
                    if not self._is_running:
                        break
                    
                    if self._process_symbol(symbol):
                        any_bar_processed = True
                
                if not any_bar_processed:
                    # Check if providers are still active (Streaming mode)
                    is_streaming = any(self.market_data.is_data_available(s) for s in self.config.symbols)
                    
                    if is_streaming:
                        # Wait a bit for new data to arrive
                        time.sleep(0.1)
                        continue
                    else:
                        # All providers exhausted (Backtest mode)
                        print("\nAll market data exhausted.")
                        break
                    
                self._bar_count += 1
                
        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
        except Exception as e:
            print(f"\n\nERROR in runner: {e}")
            traceback.print_exc()
            raise
        finally:
            self._is_running = False
        
        return self._get_stats()
    
    def _process_symbol(self, symbol: str) -> bool:
        """
        Process one bar for a symbol through all strategies.
        """
        # 1. Fetch market data
        bar = self.market_data.get_next_bar(symbol)
        if not bar:
            return False
        
        # Sync clock to bar timestamp
        if hasattr(self.clock, 'set_time'):
            self.clock.set_time(bar.timestamp)
        
        # 2. Fetch analytics snapshot (may be None)
        analytics_snapshot = self.analytics.get_latest_snapshot(symbol, as_of=bar.timestamp)
        
        if analytics_snapshot is None and self.config.warn_on_missing_analytics:
            print(f"[WARNING] Missing analytics snapshot for {symbol} at {bar.timestamp}")
        
        # 3. Get current position
        current_position = self.positions.get_position_quantity(symbol)
        
        # 4. Get market regime (may be None)
        market_regime = self.analytics.get_market_regime(symbol, as_of=bar.timestamp)
        
        # 5. Process through each strategy
        for strategy in self.strategies:
            if strategy.strategy_id in self._disabled_strategies:
                continue
            
            if not strategy.is_enabled:
                continue
            
            try:
                # Build context
                context = StrategyContext(
                    symbol=symbol,
                    current_position=current_position,
                    analytics_snapshot=analytics_snapshot,
                    market_regime=market_regime,
                    strategy_params=strategy.config
                )
                
                # Invoke strategy
                signal = strategy.process_bar(bar, context)
                
                if signal:
                    # Phase D: Generate deterministic signal_id
                    from hashlib import sha256
                    raw_id = f"{symbol}_{strategy.strategy_id}_{bar.timestamp.isoformat()}"
                    signal_id = sha256(raw_id.encode()).hexdigest()
                    
                    signal_with_id = replace(signal, metadata={**signal.metadata, 'signal_id': signal_id})

                    self._signal_count += 1
                    
                    if self.config.log_signals:
                        self._log_signal(signal_with_id, bar.close)
                    
                    # Phase D: Persist signal before execution
                    from core.data.analytics_persistence import save_signal
                    save_signal(signal_with_id)

                    # 6. Execute signal
                    trade = self.execution.process_signal(signal_with_id, bar.close)
                    
                    if trade:
                        self._trade_count += 1
                        
                        if self.config.log_trades:
                            self._log_trade(trade)
                        
                        # 7. Update position from trade
                        self.positions.apply_trade(trade)
                        
            except Exception as e:
                error_msg = f"Strategy {strategy.strategy_id} error on {symbol}: {e}"
                print(f"\n[ERROR] {error_msg}")
                traceback.print_exc()
                
                # Disable strategy
                self._disabled_strategies.add(strategy.strategy_id)
                print(f"[RUNNER] Disabled strategy: {strategy.strategy_id}")
                
                if self.config.stop_on_error:
                    raise RuntimeError(error_msg)
                    
        return True
    
    def _log_signal(self, signal: SignalEvent, price: float) -> None:
        """Log a signal."""
        print(f"[SIGNAL] {signal.strategy_id:20} | {signal.symbol:10} | "
              f"{signal.signal_type.value:6} | conf={signal.confidence:.2f} | "
              f"price={price:.2f} | time={self.clock.now()}")
    
    def _log_trade(self, trade: TradeEvent) -> None:
        """Log a trade."""
        status = trade.status.value if hasattr(trade.status, 'value') else str(trade.status)
        print(f"[TRADE]  {trade.symbol:10} | {trade.direction:5} | "
              f"qty={trade.quantity:8.2f} | price={trade.price:8.2f} | "
              f"status={status} | time={self.clock.now()}")
    
    def _get_stats(self) -> Dict[str, Any]:
        """Get run statistics."""
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
        """Stop the runner gracefully."""
        print(f"\n[RUNNER] Stopping at {self.clock.now()}...")
        self._is_running = False
    
    def get_position_summary(self) -> str:
        """Get a formatted position summary."""
        positions = self.positions.get_all_positions()
        
        if not positions:
            return "No positions"
        
        lines = [f"\nCurrent Positions (at {self.clock.now()}):", "-" * 60]
        for symbol, pos in positions.items():
            lines.append(f"  {symbol:10} | qty={pos.quantity:10.2f} | "
                        f"avg={pos.avg_entry_price:8.2f}")
        lines.append("-" * 60)
        
        return "\n".join(lines)
