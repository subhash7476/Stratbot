"""
Execution Handler
-----------------
Translates SignalEvent → Order → TradeEvent.

GUARANTEES:
- Validates signals (risk checks, position limits)
- Converts to broker-specific orders
- Never calls back to strategies
- Never recomputes analytics
- Can be disabled (dry-run mode)
- All trades recorded for audit
- Phase 5: Observability & Kill Switches
- Phase 8: Operational Alerts & Live Readiness
"""

import time
import logging
import os
import json
from pathlib import Path
from typing import Optional, Dict, Any, List

from datetime import datetime
from dataclasses import dataclass, replace, field
from enum import Enum

from core.events import SignalEvent, SignalType, TradeEvent, TradeStatus, OrderEvent, OrderType, OrderStatus
from core.clock import Clock
from core.brokers.base import BrokerAdapter
from core.alerts.alerter import alerter


class ExecutionMode(Enum):
    """Execution modes for safety."""
    DRY_RUN = "dry_run"      # Log only, no actual orders
    PAPER = "paper"          # Simulated fills
    LIVE = "live"            # Real broker API


@dataclass
class ExecutionConfig:
    """Configuration for execution handler."""
    mode: ExecutionMode = ExecutionMode.DRY_RUN
    default_quantity: float = 100.0  # Default position size
    max_position_size: float = 1000.0  # Max position limit
    slippage_model: str = "fixed"  # "fixed" or "percentage"
    slippage_value: float = 0.01  # Fixed $0.01 or 0.1%
    max_trades_per_day: int = 100
    max_drawdown_limit: float = 0.05 # 5%


@dataclass
class ExecutionMetrics:
    """Real-time observability metrics for execution."""
    signals_received: int = 0
    trades_executed: int = 0
    rejected_trades: int = 0
    start_time: float = field(default_factory=time.time)
    daily_pnl: float = 0.0
    max_equity: float = 100000.0
    current_equity: float = 100000.0
    
    def get_throughput(self) -> float:
        """Returns signals processed per second."""
        elapsed = time.time() - self.start_time
        return self.signals_received / elapsed if elapsed > 0 else 0.0

    def get_drawdown(self) -> float:
        """Calculates current drawdown percentage."""
        if self.max_equity == 0: return 0.0
        return (self.max_equity - self.current_equity) / self.max_equity


class ExecutionHandler:
    """
    Handles execution of strategy signals with Safety Kill Switches and Alerts.
    
    GUARANTEES:
    - Observability: Real-time throughput and error tracking.
    - Safety: Kill switches for max trades and manual stop.
    - Determinism: Identical behavior in same execution mode.
    """
    
    def __init__(self, clock: Clock, broker: BrokerAdapter, config: Optional[ExecutionConfig] = None, metrics_path: str = "logs/execution_metrics.json"):
        self.clock = clock
        self.broker = broker
        self.config = config or ExecutionConfig()
        self._position_tracker: Dict[str, float] = {}  # symbol → current position
        self._trade_history: List[TradeEvent] = []
        self._dry_run_orders: List[Dict] = []  # For testing
        self.metrics_path = Path(metrics_path)
        
        # Phase 5: Observability & Safety
        self.metrics = ExecutionMetrics()
        self._kill_switched = False
        self._trades_today = 0
        self.logger = logging.getLogger(__name__)
        
        # Phase 8: Alerting State
        self._consecutive_losses = 0
        self._last_alerted_loss_threshold = 0.0
        self._persist_metrics()

    def _persist_metrics(self):
        """Writes current execution metrics to disk for Flask."""
        try:
            self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "signals_received": self.metrics.signals_received,
                "trades_executed": self.metrics.trades_executed,
                "rejected_trades": self.metrics.rejected_trades,
                "throughput": self.metrics.get_throughput(),
                "current_equity": self.metrics.current_equity,
                "max_equity": self.metrics.max_equity,
                "drawdown": self.metrics.get_drawdown(),
                "kill_switched": self._kill_switched,
                "trades_today": self._trades_today,
                "last_update": self.clock.now().isoformat()
            }
            with open(self.metrics_path, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            self.logger.error(f"Failed to persist metrics: {e}")

    def process_signal(self, 
                      signal: SignalEvent, 
                      current_price: float) -> Optional[TradeEvent]:
        """
        Process a signal from a strategy and generate trade with safety checks and alerts.
        """
        # 0. Manual Kill Switch File Flag
        if not getattr(self, '_kill_switch_disabled', False) and os.path.exists("STOP"):
            self.activate_kill_switch("Manual STOP file detected.")
            return None

        # Phase D: Idempotency Guard
        signal_id = signal.metadata.get('signal_id')
        if signal_id and self._is_signal_already_executed(signal_id):
            self.logger.info(f"Signal {signal_id} already executed. Skipping.")
            return None

        # 1. Observability
        self.metrics.signals_received += 1

        # 2. Kill Switch Check
        if self._kill_switched:
            return None

        # 3. Daily Trade Limit Check (skip if disabled)
        if getattr(self, '_kill_switch_disabled', False):
            pass
        elif self._trades_today >= self.config.max_trades_per_day:
            self.activate_kill_switch(f"Max daily trades ({self.config.max_trades_per_day}) reached.")
            return None

        # 4. Drawdown Kill Switch
        if not getattr(self, '_kill_switch_disabled', False):
            dd = self.metrics.get_drawdown()
            if dd >= self.config.max_drawdown_limit:
                self.activate_kill_switch(f"Max drawdown ({dd*100:.1f}%) reached.")
                return None

            # 5. Loss Threshold Alerts (80%)
            if dd >= (self.config.max_drawdown_limit * 0.8) and self._last_alerted_loss_threshold < 0.8:
                alerter.warning(f"Daily drawdown reached 80% of limit ({dd*100:.1f}%)")
                self._last_alerted_loss_threshold = 0.8

        # 6. Standard Validation
        if not self._validate_signal(signal):
            self.metrics.rejected_trades += 1
            return None
        
        # 7. Risk Checks
        if not self._check_risk_limits(signal, current_price):
            self.metrics.rejected_trades += 1
            return self._create_rejected_trade(signal, "Risk limit exceeded")
        
        # 8. Execute Logic
        if signal.signal_type == SignalType.BUY:
            direction = "BUY"
            quantity = self._calculate_position_size(signal, current_price)
        elif signal.signal_type == SignalType.SELL:
            direction = "SELL"
            quantity = self._calculate_position_size(signal, current_price)
        elif signal.signal_type == SignalType.EXIT:
            current_pos = self._position_tracker.get(signal.symbol, 0.0)
            if current_pos > 0: 
                direction = "SELL"
            elif current_pos < 0: 
                direction = "BUY"
            else:
                self.metrics.rejected_trades += 1
                return self._create_rejected_trade(signal, "No position to exit")
            quantity = abs(current_pos)
        else:
            return None 

        executed_price = self._apply_slippage(current_price, direction)
        
        # Create Order
        order = OrderEvent(
            order_id=f"order_{self.clock.now().strftime('%Y%m%d%H%M%S%f')}_{signal.symbol}",
            signal_id_reference=signal.metadata.get('signal_id', str(id(signal))),
            timestamp=self.clock.now(),
            symbol=signal.symbol,
            order_type=OrderType.MARKET,
            side=direction,
            quantity=quantity,
            price=executed_price,
            stop_price=None,
            time_in_force="DAY",
            status=OrderStatus.CREATED
        )
        
        # Execute based on mode
        result = None
        if self.config.mode == ExecutionMode.DRY_RUN:
            self._log_dry_run_order(order)
            result = None
        else:
            # Dispatch to broker
            try:
                broker_order_id = self.broker.place_order(order)
                status = self.broker.get_order_status(broker_order_id)
                
                if status == OrderStatus.FILLED:
                    result = TradeEvent(
                        trade_id=f"trade_{broker_order_id}",
                        signal_id_reference=signal.metadata.get('signal_id', str(id(signal))),
                        timestamp=self.clock.now(),
                        symbol=signal.symbol,
                        status=TradeStatus.FILLED,
                        direction=direction,
                        quantity=quantity,
                        price=executed_price,
                        fees=self._calculate_fees(quantity, executed_price),
                        broker_reference_id=broker_order_id
                    )
                    self._update_position(result)
                    self._trade_history.append(result)
                    self._update_equity_metrics(result)
            except Exception as e:
                alerter.critical(f"Broker error on {signal.symbol}: {e}")
                self.logger.error(f"Broker error: {e}")
        
        if result:
            self.metrics.trades_executed += 1
            self._trades_today += 1
        
        self._persist_metrics()
        return result

    def reconcile_positions(self):
        """
        Phase 8: Broker ↔ internal position mismatch check.
        """
        try:
            broker_positions = self.broker.get_positions()
            for symbol, pos in broker_positions.items():
                internal_qty = self._position_tracker.get(symbol, 0.0)
                if abs(internal_qty - pos.quantity) > 0.001:
                    alerter.critical(f"POSITION MISMATCH for {symbol}: Internal={internal_qty}, Broker={pos.quantity}")
        except Exception as e:
            alerter.warning(f"Failed to reconcile positions: {e}")

    def activate_kill_switch(self, reason: str):
        """Trigger the system-wide kill switch with critical alert."""
        if not self._kill_switched:
            self._kill_switched = True
            alerter.critical(f"KILL SWITCH ACTIVATED: {reason}")
            self.logger.warning(f"Kill switch activated: {reason}")
            self._persist_metrics()

    def reset_safety_limits(self):
        """Reset daily counts and kill switch."""
        self._trades_today = 0
        self._kill_switched = False
        self._consecutive_losses = 0
        self._last_alerted_loss_threshold = 0.0
        alerter.info("Safety limits and kill switch reset.")
        self.logger.info("Safety limits and kill switch reset.")
        self._persist_metrics()

    def _log_dry_run_order(self, order: OrderEvent) -> None:
        """Log order in dry-run mode."""
        self._dry_run_orders.append({
            'timestamp': order.timestamp.isoformat(),
            'symbol': order.symbol,
            'side': order.side,
            'quantity': order.quantity,
            'price': order.price,
            'mode': 'DRY_RUN'
        })
        print(f"[DRY-RUN] Would place order: {order.side} {order.quantity} {order.symbol} @ {order.price} at {self.clock.now()}")

    def _validate_signal(self, signal: SignalEvent) -> bool:
        if not signal.symbol or not signal.strategy_id: return False
        if not 0 <= signal.confidence <= 1: return False
        return signal.signal_type in [SignalType.BUY, SignalType.SELL, SignalType.EXIT]
    
    def _check_risk_limits(self, signal: SignalEvent, current_price: float) -> bool:
        current_position = self._position_tracker.get(signal.symbol, 0.0)
        if signal.signal_type == SignalType.BUY:
            if current_position + self.config.default_quantity > self.config.max_position_size: return False
        elif signal.signal_type == SignalType.SELL:
            if abs(current_position - self.config.default_quantity) > self.config.max_position_size: return False
        return True
    
    def _calculate_position_size(self, signal: SignalEvent, current_price: float) -> float:
        return self.config.default_quantity * (0.5 + signal.confidence * 0.5)
    
    def _apply_slippage(self, price: float, direction: str) -> float:
        adj = self.config.slippage_value if self.config.slippage_model == "fixed" else price * self.config.slippage_value
        return price + adj if direction == "BUY" else price - adj
    
    def _calculate_fees(self, quantity: float, price: float) -> float:
        return quantity * price * 0.001
    
    def _create_rejected_trade(self, signal: SignalEvent, reason: str) -> TradeEvent:
        return TradeEvent(
            trade_id=f"rejected_{self.clock.now().strftime('%Y%m%d%H%M%S%f')}",
            signal_id_reference=str(id(signal)),
            timestamp=self.clock.now(),
            symbol=signal.symbol,
            status=TradeStatus.REJECTED,
            direction="NONE",
            quantity=0.0,
            price=0.0,
            fees=0.0,
            rejection_reason=reason
        )
    
    def _update_position(self, trade: TradeEvent) -> None:
        symbol = trade.symbol
        current = self._position_tracker.get(symbol, 0.0)
        if trade.direction == "BUY": self._position_tracker[symbol] = current + trade.quantity
        elif trade.direction == "SELL": self._position_tracker[symbol] = current - trade.quantity

    def _update_equity_metrics(self, trade: TradeEvent):
        cost = trade.quantity * trade.price + trade.fees
        if trade.direction == "BUY": self.metrics.current_equity -= cost
        else: self.metrics.current_equity += (trade.quantity * trade.price - trade.fees)
        self.metrics.max_equity = max(self.metrics.max_equity, self.metrics.current_equity)
    
    def get_position(self, symbol: str) -> float:
        return self._position_tracker.get(symbol, 0.0)
    
    def get_trade_history(self) -> List[TradeEvent]:
        return list(self._trade_history)
    
    def set_mode(self, mode: ExecutionMode) -> None:
        self.config.mode = mode
        self.logger.info(f"Execution mode changed to: {mode.value}")

    def _is_signal_already_executed(self, signal_id: str) -> bool:
        """
        Phase D: Check DuckDB to see if this signal_id was already processed.
        Prevents duplicate execution on restart.
        """
        from core.data.duckdb_client import db_cursor
        db_path = os.environ.get("TRADING_DB_PATH", "data/trading_bot.duckdb")
        try:
            with db_cursor(db_path) as conn:
                res = conn.execute("SELECT COUNT(*) FROM trades WHERE signal_id = ?", [signal_id]).fetchone()
                return res[0] > 0 if res else False
        except Exception as e:
            self.logger.error(f"Failed to check signal idempotency: {e}")
            return False
