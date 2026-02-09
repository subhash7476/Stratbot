"""
Refactored Execution Handler
-----------------
Handles execution of strategy signals using the isolated trading database (SQLite).
"""

import time
import os
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

from datetime import datetime
from dataclasses import dataclass, replace, field
from enum import Enum

from core.events import SignalEvent, SignalType, TradeEvent, TradeStatus, OrderEvent, OrderType, OrderStatus
from core.clock import Clock
from core.brokers.base import BrokerAdapter
from core.alerts.alerter import alerter
from core.database.manager import DatabaseManager
from core.logging import setup_logger


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
    cash_balance: float = 100000.0
    max_drawdown_pct: float = 0.0
    
    def get_throughput(self) -> float:
        """Returns signals processed per second."""
        elapsed = time.time() - self.start_time
        return self.signals_received / elapsed if elapsed > 0 else 0.0

    def update_drawdown(self, total_equity: float) -> float:
        """Updates and returns current drawdown percentage based on total equity."""
        self.max_equity = max(self.max_equity, total_equity)
        if self.max_equity == 0: return 0.0
        current_dd = (self.max_equity - total_equity) / self.max_equity
        self.max_drawdown_pct = max(self.max_drawdown_pct, current_dd)
        return current_dd

    def get_drawdown(self, total_equity: float) -> float:
        return self.update_drawdown(total_equity)


class ExecutionHandler:
    """
    Handles execution of strategy signals with Safety Kill Switches and Alerts.
    Uses the isolated trading database for state tracking.
    """
    
    def __init__(self, db_manager: DatabaseManager, clock: Clock, broker: BrokerAdapter, config: Optional[ExecutionConfig] = None, metrics_path: str = "logs/execution_metrics.json", initial_capital: float = 100000.0, load_db_state: bool = True):
        self.db_manager = db_manager
        self.clock = clock
        self.broker = broker
        self.config = config or ExecutionConfig()
        self._position_tracker: Dict[str, float] = {}  # symbol → current position
        self._trade_history: List[TradeEvent] = []
        self._dry_run_orders: List[Dict] = []  # For testing
        self.metrics_path = Path(metrics_path)
        
        self.metrics = ExecutionMetrics(
            max_equity=initial_capital,
            cash_balance=initial_capital
        )
        self._kill_switched = False
        self._trades_today = 0
        self.logger = logging.getLogger(__name__)
        
        self._consecutive_losses = 0
        self._last_alerted_loss_threshold = 0.0
        self._persist_metrics()
        
        if load_db_state:
            self._load_positions_from_db()
            
        self.logger = setup_logger("execution_handler")

    def _load_positions_from_db(self):
        """Sync internal position tracker with trading database."""
        try:
            with self.db_manager.trading_reader() as conn:
                rows = conn.execute("SELECT symbol, quantity FROM positions").fetchall()
                for symbol, qty in rows:
                    self._position_tracker[symbol] = qty
        except Exception as e:
            self.logger.warning(f"Could not load positions from DB: {e}")

    def _persist_metrics(self, current_price: Optional[float] = None, symbol: Optional[str] = None):
        """Writes current execution metrics to disk for Flask."""
        try:
            self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
            
            total_equity = self.metrics.cash_balance
            if current_price and symbol:
                total_equity += self._position_tracker.get(symbol, 0.0) * current_price

            data = {
                "signals_received": self.metrics.signals_received,
                "trades_executed": self.metrics.trades_executed,
                "rejected_trades": self.metrics.rejected_trades,
                "throughput": self.metrics.get_throughput(),
                "cash_balance": self.metrics.cash_balance,
                "total_equity": total_equity,
                "max_equity": self.metrics.max_equity,
                "drawdown": self.metrics.get_drawdown(total_equity),
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

        # Idempotency Guard (using trading.db)
        signal_id = getattr(signal, 'signal_id', signal.metadata.get('signal_id'))
        if not signal_id:
            from hashlib import sha256
            raw_id = f"{signal.symbol}_{signal.strategy_id}_{signal.timestamp.isoformat()}"
            signal_id = sha256(raw_id.encode()).hexdigest()

        if self._is_signal_already_executed(str(signal_id)):
            self.logger.info(f"Signal {signal_id} already executed. Skipping.")
            return None

        # 1. Observability
        self.metrics.signals_received += 1

        # 2. Kill Switch Check
        if self._kill_switched:
            return None

        # 3. Daily Trade Limit Check
        if not getattr(self, '_kill_switch_disabled', False) and self._trades_today >= self.config.max_trades_per_day:
            self.activate_kill_switch(f"Max daily trades ({self.config.max_trades_per_day}) reached.")
            return None

        # 4. Drawdown Kill Switch
        total_equity = self.metrics.cash_balance + (self._position_tracker.get(signal.symbol, 0.0) * current_price)
        if not getattr(self, '_kill_switch_disabled', False):
            dd = self.metrics.get_drawdown(total_equity)
            if dd >= self.config.max_drawdown_limit:
                self.activate_kill_switch(f"Max drawdown ({dd*100:.1f}%) reached.")
                return None

        # 5. Risk Checks
        if not self._check_risk_limits(signal, current_price):
            self.metrics.rejected_trades += 1
            return None
        
        # 6. Direction and Quantity
        if signal.signal_type == SignalType.BUY:
            direction = "BUY"
            quantity = self._calculate_position_size(signal, current_price)
        elif signal.signal_type == SignalType.SELL:
            direction = "SELL"
            quantity = self._calculate_position_size(signal, current_price)
        elif signal.signal_type == SignalType.EXIT:
            current_pos = self._position_tracker.get(signal.symbol, 0.0)
            if current_pos == 0: return None
            direction = "SELL" if current_pos > 0 else "BUY"
            quantity = abs(current_pos)
        else:
            return None 

        executed_price = self._apply_slippage(current_price, direction)
        
        # 7. Execute based on mode
        result = None
        if self.config.mode == ExecutionMode.DRY_RUN:
            self._log_dry_run_order(signal, direction, quantity, executed_price)
            return None
        else:
            try:
                # Actual broker execution logic here...
                # For brevity, we simulate a successful fill
                import uuid
                broker_order_id = f"sim_{uuid.uuid4().hex[:12]}"
                
                result = TradeEvent(
                    trade_id=f"trade_{broker_order_id}",
                    signal_id_reference=signal_id,
                    timestamp=self.clock.now(),
                    symbol=signal.symbol,
                    status=TradeStatus.FILLED,
                    direction=direction,
                    quantity=quantity,
                    price=executed_price,
                    fees=self._calculate_fees(quantity, executed_price),
                    broker_reference_id=broker_order_id
                )
                self._record_trade_in_db(result, signal_id)
                self._update_position(result)
                self._trade_history.append(result)
                self._update_equity_metrics(result)
                
            except Exception as e:
                alerter.critical(f"Broker error on {signal.symbol}: {e}")
                self.logger.error(f"Broker error: {e}")
        
        if result:
            self.metrics.trades_executed += 1
            self._trades_today += 1
        
        self._persist_metrics(current_price, signal.symbol)
        return result

    def _record_trade_in_db(self, trade: TradeEvent, signal_id: str):
        """Persist trade and update position in trading database."""
        try:
            with self.db_manager.trading_writer() as conn:
                # 1. Record Trade
                conn.execute("""
                    INSERT INTO trades (trade_id, signal_id, timestamp, symbol, side, entry_price, quantity, fees)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, [trade.trade_id, signal_id, str(trade.timestamp) if hasattr(trade.timestamp, 'isoformat') else trade.timestamp, trade.symbol, trade.direction, trade.price, trade.quantity, trade.fees])
                
                # 2. Update Position
                conn.execute("""
                    INSERT INTO positions (symbol, quantity, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT (symbol) DO UPDATE SET
                        quantity = excluded.quantity,
                        updated_at = excluded.updated_at
                """, [trade.symbol, self.get_position(trade.symbol), str(self.clock.now())])
        except Exception as e:
            self.logger.error(f"Failed to record trade in DB: {e}")

    def activate_kill_switch(self, reason: str):
        if not self._kill_switched:
            self._kill_switched = True
            alerter.critical(f"KILL SWITCH ACTIVATED: {reason}")
            self.logger.warning(f"Kill switch activated: {reason}")
            self._persist_metrics()

    def _is_signal_already_executed(self, signal_id: str) -> bool:
        """Check trading.db for existing execution of this signal."""
        try:
            with self.db_manager.trading_reader() as conn:
                res = conn.execute("SELECT COUNT(*) FROM trades WHERE signal_id = ?", [signal_id]).fetchone()
                return res[0] > 0 if res else False
        except Exception:
            return False

    def _check_risk_limits(self, signal: SignalEvent, current_price: float) -> bool:
        current_position = self._position_tracker.get(signal.symbol, 0.0)
        if signal.signal_type == SignalType.BUY:
            if current_position + self.config.default_quantity > self.config.max_position_size: return False
        elif signal.signal_type == SignalType.SELL:
            if abs(current_position - self.config.default_quantity) > self.config.max_position_size: return False
        return True
    
    def _calculate_position_size(self, signal: SignalEvent, current_price: float) -> float:
        # If strategy provided explicit sizing (e.g., ATR-based), use it
        strategy_qty = signal.metadata.get("quantity", 0)
        if strategy_qty > 0:
            return min(float(strategy_qty), self.config.max_position_size)
        return self.config.default_quantity * (0.5 + signal.confidence * 0.5)
    
    def _apply_slippage(self, price: float, direction: str) -> float:
        adj = self.config.slippage_value if self.config.slippage_model == "fixed" else price * self.config.slippage_value
        return price + adj if direction == "BUY" else price - adj
    
    def _calculate_fees(self, quantity: float, price: float) -> float:
        """Realistic NSE equity intraday costs per leg.

        Brokerage: Rs 20 flat (discount broker)
        STT: 0.025% of sell-side turnover (handled in aggregate)
        Exchange txn: 0.00345% of turnover
        SEBI fee: 0.0001% of turnover
        GST: 18% on (brokerage + exchange + SEBI)
        Stamp duty: 0.003% of buy-side turnover
        """
        turnover = quantity * price
        brokerage = 20.0
        exchange_txn = turnover * 0.0000345
        sebi = turnover * 0.000001
        stt = turnover * 0.00025  # 0.025% (applied on sell side; averaged across both legs)
        stamp = turnover * 0.00003
        gst = 0.18 * (brokerage + exchange_txn + sebi)
        return brokerage + exchange_txn + sebi + stt + stamp + gst
    
    def _log_dry_run_order(self, signal, side, qty, price):
        self.logger.info(f"[DRY-RUN] {side} {qty} {signal.symbol} @ {price} at {self.clock.now()}")

    def _update_position(self, trade: TradeEvent) -> None:
        symbol = trade.symbol
        current = self._position_tracker.get(symbol, 0.0)
        if trade.direction == "BUY": self._position_tracker[symbol] = current + trade.quantity
        elif trade.direction == "SELL": self._position_tracker[symbol] = current - trade.quantity

    def _update_equity_metrics(self, trade: TradeEvent):
        cost = trade.quantity * trade.price + trade.fees
        if trade.direction == "BUY": self.metrics.cash_balance -= cost
        else: self.metrics.cash_balance += (trade.quantity * trade.price - trade.fees)
        
        total_equity = self.metrics.cash_balance + (self._position_tracker.get(trade.symbol, 0.0) * trade.price)
        self.metrics.max_equity = max(self.metrics.max_equity, total_equity)
    
    def get_position(self, symbol: str) -> float:
        return self._position_tracker.get(symbol, 0.0)

    def get_stats(self) -> Dict[str, Any]:
        """Returns execution stats for telemetry."""
        total_trades = len(self._trade_history)
        win_rate = self._calculate_win_rate()
        
        return {
            "daily_pnl": self.metrics.daily_pnl,
            "drawdown_pct": self.metrics.max_drawdown_pct,
            "trade_count": total_trades,
            "win_rate": win_rate
        }

    def _calculate_win_rate(self) -> float:
        # Very simple win rate calculation for current session
        trades = [t for t in self._trade_history if t.status == TradeStatus.FILLED]
        if not trades: return 0.0
        
        # This is a placeholder; real win rate needs paired trades
        # For telemetry snapshot, we just return a best-effort number
        return 0.0 

    def get_trade_history(self) -> List[TradeEvent]:
        return list(self._trade_history)
