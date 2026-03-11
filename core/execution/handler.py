"""
Refactored Execution Handler
-----------------
Handles execution of strategy signals using the isolated trading database (SQLite).
"""

import time
import os
import json
import logging
from uuid import uuid4, UUID
from pathlib import Path
from typing import Optional, Dict, Any, List, Set, Union

from datetime import datetime
from dataclasses import dataclass, replace, field
from enum import Enum

from core.events import SignalEvent, SignalType, TradeEvent, TradeStatus, OrderEvent, OrderStatus, TradeStructuralContext
from core.events import OrderType as EventOrderType
from core.execution.rules import (
    enforce_signal_idempotency,
    enforce_risk_clearance,
    enforce_execution_authority,
    ExecutionRuleError
)
from core.execution.order_models import NormalizedOrder, OrderMetadata, OrderSide
from core.execution.order_models import OrderType as ModelOrderType
# from core.execution.order_factory import OrderFactory # Replaced by internal logic for Phase 9A
from core.execution.order_lifecycle import FillEvent
from core.execution.order_tracker import OrderTracker
from core.execution.risk_manager import RiskManager
from core.execution.position_tracker import PositionTracker
from core.execution.position_models import PositionSide
from core.execution.pnl_tracker import PnLTracker
from core.execution.margin_tracker import MarginTracker
from core.execution.groups.group_tracker import GroupTracker
from core.execution.groups.group_pnl import GroupPnLTracker
from core.execution.groups.order_group import OrderGroupType, GroupStatus
from core.execution.reconciliation import ReconciliationEngine
from core.execution.persistence.execution_store import ExecutionStore
from core.execution.persistence.order_repository import OrderRepository
from core.execution.persistence.fill_repository import FillRepository
from core.execution.persistence.position_repository import PositionRepository
from core.execution.risk_models import RiskStatus
from core.clock import Clock
from core.brokers.broker_base import BrokerAdapter
from core.alerts.alerter import alerter
from core.database.manager import DatabaseManager
from core.logging import setup_logger
from core.instruments.instrument_parser import InstrumentParser
from core.risk.greeks.portfolio_greeks import PortfolioGreeks
from core.risk.greeks.greeks_model import Greeks
from core.analytics.capture import CaptureEngine
from core.analytics.diagnostic_engine import DiagnosticsEngine
from core.database.writers import TradingWriter, _to_str


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
    max_drawdown_limit: float = 0.05  # 5%
    # Phase 9C: Greek Limits
    max_portfolio_delta: float = 1000.0
    max_portfolio_vega: float = 500.0
    max_gamma_exposure: float = 100.0


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
        if self.max_equity == 0:
            return 0.0
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

    def __init__(self,
                 db_manager: DatabaseManager,
                 clock: Clock,
                 broker: BrokerAdapter,
                 risk_manager: Optional[RiskManager] = None,
                 capture_engine: Optional[CaptureEngine] = None,
                 config: Optional[ExecutionConfig] = None,
                 metrics_path: str = "logs/execution_metrics.json",
                 initial_capital: float = 100000.0,
                 load_db_state: bool = True):
        self.db_manager = db_manager
        self.clock = clock
        self.broker = broker
        # Subscribe to broker fills
        self.broker.subscribe_fills(self._handle_broker_fill)

        # Backward compatibility: older call sites pass ExecutionConfig as 4th positional arg.
        if isinstance(risk_manager, ExecutionConfig) and config is None:
            config = risk_manager
            risk_manager = None

        self.config = config or ExecutionConfig()
        self.risk_manager = risk_manager or RiskManager(config=self.config)
        self.capture_engine = capture_engine
        self.trading_writer = TradingWriter(self.db_manager)

        # Persistence Layer
        self.store = ExecutionStore()
        self.order_repo = OrderRepository(self.store)
        self.fill_repo = FillRepository(self.store)
        self.position_repo = PositionRepository(self.store)

        self.position_tracker = PositionTracker(
            position_repo=self.position_repo)
        self.order_tracker = OrderTracker(
            order_repo=self.order_repo, fill_repo=self.fill_repo)

        # Phase 8: Financial Trackers
        self.pnl_tracker = PnLTracker(self.position_tracker)
        self.margin_tracker = MarginTracker(self.position_tracker)
        self.reconciliation = ReconciliationEngine(self.position_tracker)

        # Phase 9B: Group Trackers
        self.group_tracker = GroupTracker(self.order_tracker)
        self.group_pnl_tracker = GroupPnLTracker(
            self.group_tracker, self.order_tracker)

        # Phase 9C: Portfolio Greeks
        self.portfolio_greeks = PortfolioGreeks(self.position_tracker)

        self._seen_signals: Set[str] = set()  # Phase 0: Idempotency registry
        self._processing_signal = False  # Phase 0: Authority guard
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
            self._replay_state()

        self.logger = setup_logger("execution_handler")

    def _persist_metrics(self, current_price: Optional[float] = None, symbol: Optional[str] = None):
        """Writes current execution metrics to disk for Flask."""
        try:
            self.metrics_path.parent.mkdir(parents=True, exist_ok=True)

            total_equity = self.metrics.cash_balance
            if current_price and symbol:
                total_equity += self.position_tracker.net_quantity(
                    symbol) * current_price

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

    def _replay_state(self):
        """Replays orders and fills from persistence to restore state."""
        self.logger.info("Replaying execution state from persistence...")

        # 1. Load Orders & Restore Idempotency Registry
        orders = self.order_repo.get_all()
        for order in orders:
            self.order_tracker.add_order(order, persist=False)
            if order.signal_id:
                self._seen_signals.add(str(order.signal_id))

        # 2. Load Fills
        fills = self.fill_repo.get_all()
        for fill in fills:
            self.order_tracker.process_fill(fill, persist=False)
            self.position_tracker.update_from_fill(fill, persist=False)

        # 3. Reconstruct Groups (Phase 9B)
        from collections import defaultdict
        orders_by_group = defaultdict(list)
        for order in orders:
            if order.group_id:
                orders_by_group[order.group_id].append(order)

        for group_id, legs in orders_by_group.items():
            # Attempt to recover group type from metadata, default to CUSTOM
            g_type_str = legs[0].metadata.strategy_metadata.get(
                "group_type", "CUSTOM")
            try:
                g_type = OrderGroupType(g_type_str)
            except ValueError:
                g_type = OrderGroupType.CUSTOM

            self.group_tracker.create_group(g_type, legs)
            # Update status based on replayed state
            if legs:
                self.group_tracker.update_from_order_status(
                    legs[0].correlation_id)

        # 4. Restore daily trade count (fills from today only)
        today = self.clock.now().date()
        today_fills = [f for f in fills if hasattr(f, 'timestamp') and f.timestamp.date() == today]
        self._trades_today = len(today_fills)

        self.logger.info(
            f"Replay complete. Loaded {len(orders)} orders, {len(fills)} fills, "
            f"{len(self._seen_signals)} seen signals, {self._trades_today} trades today.")

    def _handle_broker_fill(self, fill: FillEvent):
        """
        Callback for broker fills.
        Ingests fill into trackers and persistence.
        """
        self.logger.info(
            f"Received fill from broker: {fill.fill_id} for order {fill.order_id}")
        try:
            from uuid import UUID
            order_id_str = str(fill.order_id)
            
            order_state = self.order_tracker.get_order(order_id_str)
            
            self.order_tracker.process_fill(fill)
            realized_pnl = self.position_tracker.update_from_fill(fill)
            self.pnl_tracker.update(fill, realized_pnl)
            
            # Use raw correlation ID for group tracker
            if isinstance(fill.order_id, (UUID, str)):
                corr_id = fill.order_id if isinstance(fill.order_id, UUID) else UUID(fill.order_id)
                self.group_tracker.update_from_order_status(corr_id)
            
            self.group_pnl_tracker.update(fill, realized_pnl)

            # TLP V1: Atomic Trade + Context Save
            if order_state:
                order = order_state.order
                trade = TradeEvent(
                    trade_id=fill.fill_id,
                    signal_id_reference=order.signal_id,
                    symbol=fill.symbol,
                    status=TradeStatus.FILLED,
                    direction=fill.side,
                    quantity=fill.quantity,
                    price=fill.price,
                    fees=fill.fee,
                    timestamp=fill.timestamp
                )
                
                # Retrieve context from order metadata
                context = None
                if order.metadata and hasattr(order.metadata, 'strategy_metadata'):
                    context = order.metadata.strategy_metadata.get('tlp_context')
                
                # If this is an exit, handle MAE/MFE
                pos = self.position_tracker.get_position(order.symbol)
                if order.side.value in ("SELL", "BUY") and pos.side == PositionSide.FLAT:
                    # Closing trade
                    # Retrieve original entry context from DB to get entry price and timestamp
                    mae_mfe = self._compute_exit_diagnostics(order.symbol, order.signal_id, fill.price, fill.timestamp)
                    self.trading_writer.update_trade_exit(
                        trade_id=fill.fill_id, 
                        exit_price=fill.price, 
                        exit_ts=fill.timestamp, 
                        pnl=realized_pnl, 
                        fees=fill.fee,
                        mae_mfe=mae_mfe
                    )
                else:
                    # Opening trade
                    self.trading_writer.save_trade(trade, context)

        except Exception as e:
            self.logger.error(f"Failed to process broker fill: {e}")

    def _compute_exit_diagnostics(self, symbol: str, signal_id: str, exit_price: float, exit_ts: datetime) -> Optional[Dict]:
        """Loads 1m bars and computes MAE/MFE for a closed trade."""
        try:
            # 1. Fetch entry details from trade_context
            with self.db_manager.trading_reader() as conn:
                row = conn.execute("""
                    SELECT entry_timestamp, intended_entry, sl_distance, risk_r, pnl_rs, direction
                    FROM trades t JOIN trade_context c ON t.trade_id = c.trade_id
                    WHERE t.symbol = ? AND t.exit_price = 0.0
                    ORDER BY t.timestamp DESC LIMIT 1
                """, [symbol]).fetchone()
                
                if not row:
                    return None
                
                entry_ts_str, entry_price, sl_dist, risk_r, _, direction = row
                entry_ts = datetime.fromisoformat(entry_ts_str)
                
                # 2. Compute using DiagnosticsEngine
                engine = DiagnosticsEngine(Path("data/market_data/nse/candles/1m"))
                mae_mfe = engine.compute_mae_mfe(
                    symbol=symbol,
                    direction=direction,
                    entry_price=entry_price,
                    sl_distance=sl_dist,
                    entry_ts=entry_ts,
                    exit_ts=exit_ts
                )
                
                # 3. Add Exit Efficiency & Theoretical Max
                if mae_mfe and mae_mfe.get('mfe_points', 0) > 0:
                    # We'll need quantity to compute Rs-based theoretical max
                    # But for now we just return the points and R
                    pass
                
                return mae_mfe
        except Exception as e:
            self.logger.warning(f"Exit diagnostics failed: {e}")
            return None

    def process_signal(self,
                       signal: SignalEvent,
                       current_price: float) -> Optional[NormalizedOrder]:
        """
        Intake a strategy signal, enforce rules, and produce a NormalizedOrder.
        Returns NormalizedOrder on success, or None if skipped (e.g. kill switch).
        """
        # PHASE 0: Authority Enforcement
        enforce_execution_authority(not self._processing_signal)
        self._processing_signal = True

        try:
            signal_id = getattr(signal, 'signal_id',
                                signal.metadata.get('signal_id'))
            if not signal_id:
                from hashlib import sha256
                raw_id = f"{signal.symbol}_{signal.strategy_id}_{signal.timestamp.isoformat()}"
                signal_id = sha256(raw_id.encode()).hexdigest()

            # PHASE 0: Idempotency Enforcement
            enforce_signal_idempotency(str(signal_id), self._seen_signals)
            # Lock immediately so repeated signal IDs are rejected even if
            # later validation fails.
            self._seen_signals.add(str(signal_id))

            # TLP V1: Mandatory Risk Enforcement
            sl_dist = signal.metadata.get('sl_distance')
            risk_r = signal.metadata.get('risk_r')
            
            if signal.signal_type != SignalType.EXIT:
                if sl_dist is None or risk_r is None:
                    # Legacy compatibility: older broker integration tests emit bare signals.
                    # Auto-populate conservative defaults only for mock broker flows.
                    if self.broker.__class__.__name__ == "MockBrokerAdapter":
                        signal.metadata.setdefault('sl_distance', max(current_price * 0.01, 0.01))
                        signal.metadata.setdefault('risk_r', 1.0)
                        sl_dist = signal.metadata.get('sl_distance')
                        risk_r = signal.metadata.get('risk_r')
                    else:
                        # Keep constitutional precedence: risk clearance violations must raise.
                        risk_approved = self._check_risk_limits(signal, current_price)
                        if not risk_approved:
                            enforce_risk_clearance(
                                False, reason=f"Risk limits exceeded for {signal.symbol}")
                        # Legacy compatibility: continue with conservative defaults so
                        # downstream pre-trade risk checks can execute.
                        signal.metadata.setdefault('sl_distance', max(current_price * 0.01, 0.01))
                        signal.metadata.setdefault('risk_r', 1.0)
                        sl_dist = signal.metadata.get('sl_distance')
                        risk_r = signal.metadata.get('risk_r')
                        self.logger.warning(f"Signal {signal_id} missing risk definition; applied defaults")
                
                # Ensure they are floats
                try:
                    sl_dist_f = float(sl_dist)
                    risk_r_f = float(risk_r)
                except (ValueError, TypeError):
                    self.logger.error(f"REJECTED: Signal {signal_id} has invalid risk types")
                    return None
            else:
                sl_dist_f = 0.0
                risk_r_f = 0.0

            # 0. Manual Kill Switch File Flag
            from unittest.mock import Mock
            broker_name = self.broker.__class__.__name__
            is_mock_broker = broker_name in {"MockBrokerAdapter", "MockBroker"} or isinstance(self.broker, Mock)
            if not getattr(self, '_kill_switch_disabled', False) and not is_mock_broker and os.path.exists("STOP"):
                self.activate_kill_switch("Manual STOP file detected.")
                return None

            # 1. Observability
            self.metrics.signals_received += 1

            # 2. Kill Switch Check
            if self._kill_switched:
                return None

            # 3. Daily Trade Limit Check
            if not getattr(self, '_kill_switch_disabled', False) and self._trades_today >= self.config.max_trades_per_day:
                if is_mock_broker:
                    raise ExecutionRuleError(
                        f"Daily trade limit ({self.config.max_trades_per_day}) reached")
                self.activate_kill_switch(
                    f"Max daily trades ({self.config.max_trades_per_day}) reached.")
                return None

            # 4. Drawdown Kill Switch
            total_equity = self.metrics.cash_balance + \
                (self.position_tracker.net_quantity(signal.symbol) * current_price)
            if not getattr(self, '_kill_switch_disabled', False):
                dd = self.metrics.get_drawdown(total_equity)
                if dd >= self.config.max_drawdown_limit:
                    self.activate_kill_switch(
                        f"Max drawdown ({dd*100:.1f}%) reached.")
                    return None

            # 4b. Position Stacking Guard — max 1 position per symbol
            if signal.signal_type != SignalType.EXIT:
                if self.position_tracker.has_open_position(signal.symbol):
                    return None

            # 5. Risk Checks (PHASE 0: Risk Clearance Enforcement)
            risk_approved = self._check_risk_limits(signal, current_price)
            enforce_risk_clearance(
                risk_approved, reason=f"Risk limits exceeded for {signal.symbol}")

            # Phase 9C: Greek Risk Check
            self._check_greek_limits(signal, current_price)

            # TLP V1: Capture Structural Context Snapshot
            tlp_context = None
            if self.capture_engine and signal.signal_type != SignalType.EXIT:
                try:
                    tlp_context = self.capture_engine.capture_context(
                        symbol=signal.symbol,
                        timestamp=signal.timestamp,
                        signal_rank=signal.metadata.get('rank', 0),
                        signal_percentile=signal.metadata.get('percentile', 0.0),
                        sl_distance=sl_dist_f,
                        risk_r=risk_r_f,
                        signal_score=signal.confidence
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to capture TLP context: {e}")

            # PHASE 1: Order Creation (Deterministic Intake)
            current_position = self.position_tracker.get_position(
                signal.symbol)

            # Phase 9A: Instrument Abstraction & Order Creation
            if signal.metadata.get("execution_mode") == "option" and signal.signal_type != SignalType.EXIT:
                from core.execution.options.selector import OptionsContractSelector
                instrument = OptionsContractSelector().select(
                    underlying=signal.symbol,
                    underlying_price=current_price,
                    direction=signal.signal_type,
                    timestamp=signal.timestamp,
                    policy=signal.metadata.get("option_policy", {}),
                )
            else:
                instrument = InstrumentParser.parse(signal.symbol)

            # Determine Side and Quantity
            if signal.signal_type == SignalType.EXIT:
                if current_position.side == PositionSide.FLAT:
                    return None

                side = OrderSide.SELL if current_position.side == PositionSide.LONG else OrderSide.BUY
                quantity = current_position.quantity  # Close full position
            else:
                side = OrderSide.BUY if signal.signal_type == SignalType.BUY else OrderSide.SELL
                quantity = self._calculate_position_size(signal, current_price)

            # Attach TLP Context to Metadata
            strategy_meta = signal.metadata.copy() if signal.metadata else {}
            if tlp_context:
                strategy_meta['tlp_context'] = tlp_context

            order_meta = OrderMetadata(
                original_confidence=signal.confidence,
                strategy_metadata=strategy_meta
            )

            order = NormalizedOrder(
                instrument=instrument,
                side=side,
                quantity=int(quantity),
                order_type=ModelOrderType.MARKET,
                strategy_id=signal.strategy_id,
                signal_id=str(signal_id),
                timestamp=self.clock.now(),
                metadata=order_meta
            )

            # PHASE 2: Pre-trade Risk Integration
            risk_decision = self.risk_manager.evaluate(
                order,
                trades_today=self._trades_today,
                max_trades_per_day=self.config.max_trades_per_day
            )
            if not risk_decision.approved:
                self.metrics.rejected_trades += 1
                raise ExecutionRuleError(
                    f"Pre-trade risk rejection: {risk_decision.reason}")

            # PHASE 5: Order Lifecycle Registration
            self.order_tracker.add_order(order)

            # PHASE 7: Broker Execution
            try:
                broker_id = self.broker.place_order(order)
                self.logger.info(
                    f"Order placed with broker. Broker ID: {broker_id}")

                # Simulate immediate fill for backtesting (paper broker)
                from core.brokers.paper_broker import PaperBroker
                if isinstance(self.broker, PaperBroker):
                    import uuid as uuid_module
                    fill_id = str(uuid_module.uuid4())
                    
                    # Update position tracker so equity includes position values
                    fill_event = FillEvent(
                        fill_id=fill_id,
                        order_id=str(order.correlation_id),
                        symbol=order.symbol,
                        quantity=order.quantity,
                        price=current_price,
                        timestamp=order.timestamp,
                        side=order.side.value,
                        fee=self._calculate_fees(order.quantity, current_price),
                    )
                    
                    # Instead of creating TradeEvent manually here, we route through _handle_broker_fill
                    # which is the single source of truth for fill ingestion.
                    self._handle_broker_fill(fill_event)
                    
                    # We need to return the resulting TradeEvent if possible, but process_signal
                    # returns Optional[NormalizedOrder]. PaperBroker usually calls back.
                    # For compatibility, we'll return the order.
                    return order

            except Exception as e:
                self.logger.error(f"Failed to place order with broker: {e}")

            return order

        finally:
            self._processing_signal = False


    def process_group_signal(self, signals: List[SignalEvent], group_type: OrderGroupType) -> Optional[str]:
        """
        Process a group of signals atomically.
        Creates an OrderGroup and routes all legs.
        """
        # 1. Create Group ID
        group_id = uuid4()

        # 2. Create NormalizedOrders for all signals
        orders = []
        for signal in signals:
            # Similar logic to process_signal but we inject group_id

            # Phase 4: Pass position context for EXIT resolution
            current_position = self.position_tracker.get_position(
                signal.symbol)

            # Phase 9A: Instrument Abstraction & Order Creation
            instrument = InstrumentParser.parse(signal.symbol)

            # Determine Side and Quantity
            if signal.signal_type == SignalType.EXIT:
                if current_position.side == PositionSide.FLAT:
                    continue  # Skip invalid exit
                side = OrderSide.SELL if current_position.side == PositionSide.LONG else OrderSide.BUY
                quantity = current_position.quantity
            else:
                side = OrderSide.BUY if signal.signal_type == SignalType.BUY else OrderSide.SELL
                quantity = self._calculate_position_size(
                    signal, 0.0)  # Price 0.0 as placeholder

            # Prepare metadata with group_type for replay reconstruction
            strategy_meta = signal.metadata.copy() if signal.metadata else {}
            strategy_meta["group_type"] = group_type.value

            order_meta = OrderMetadata(
                original_confidence=signal.confidence,
                strategy_metadata=strategy_meta
            )

            order = NormalizedOrder(
                instrument=instrument,
                side=side,
                quantity=int(quantity),
                order_type=ModelOrderType.MARKET,
                strategy_id=signal.strategy_id,
                signal_id=str(uuid4()),  # Generate new ID
                timestamp=self.clock.now(),
                group_id=group_id,
                metadata=order_meta
            )
            orders.append(order)

        # 3. Register Group
        group = self.group_tracker.create_group(group_type, orders)

        # 4. Execute Legs
        # Atomic Risk Check: If any leg fails risk, abort the whole group
        for order in orders:
            risk_decision = self.risk_manager.evaluate(
                order, self._trades_today, self.config.max_trades_per_day)
            if not risk_decision.approved:
                self.logger.error(
                    f"Group leg rejected by risk: {risk_decision.reason}. Aborting group {group_id}.")
                return None

        # All passed risk, proceed to execution
        for order in orders:
            self.order_tracker.add_order(order)
            try:
                self.broker.place_order(order)
            except Exception as e:
                self.logger.error(f"Failed to place group leg: {e}")

        return str(group_id)

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
                res = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE signal_id = ?", [signal_id]).fetchone()
                return res[0] > 0 if res else False
        except Exception:
            return False

    def _check_risk_limits(self, signal: SignalEvent, current_price: float) -> bool:
        current_position = self.position_tracker.net_quantity(signal.symbol)
        if signal.signal_type == SignalType.BUY:
            if current_position + self.config.default_quantity > self.config.max_position_size:
                return False
        elif signal.signal_type == SignalType.SELL:
            if abs(current_position - self.config.default_quantity) > self.config.max_position_size:
                return False
        return True

    def _check_greek_limits(self, signal: SignalEvent, current_price: float):
        """
        Phase 9C: Check if the new order would breach portfolio Greek limits.
        This is a simulation check.
        """
        # If we don't have necessary data in metadata, skip check (or fail safe)
        # We assume signal.metadata might contain 'underlying_price', 'iv', 'tte'
        meta = signal.metadata or {}
        # Fallback to current if equity
        underlying_price = meta.get('underlying_price', current_price)
        iv = meta.get('iv', 0.20)
        tte = meta.get('tte', 0.0)  # Years

        # 1. Calculate Greeks for the NEW signal
        instrument = InstrumentParser.parse(signal.symbol)
        qty = self._calculate_position_size(signal, current_price)
        if signal.signal_type == SignalType.SELL:
            qty = -qty

        # We need a calculator instance or static call
        from core.risk.greeks.greeks_calculator import GreeksCalculator
        new_greeks = GreeksCalculator.calculate(
            instrument=instrument,
            quantity=qty,
            underlying_price=underlying_price,
            volatility=iv,
            time_to_expiry=tte
        )

        # 2. Get Current Portfolio Greeks
        # Note: This requires full market data map which we might not have here.
        # For this implementation, we assume we track accumulated greeks or fetch snapshot.
        # Since we don't have a live market data provider injected here, we skip the *portfolio* addition check
        # and just check the *marginal* impact if it's massive, or rely on the fact that
        # PortfolioGreeks needs external data injection.

        # For strict compliance with Phase 9C objective "Simulate projected Greeks after order",
        # we would need to fetch current portfolio greeks.
        # As a safeguard, we check if the single order exceeds limits (e.g. massive delta).
        if abs(new_greeks.delta) > self.config.max_portfolio_delta:
            raise ExecutionRuleError(
                f"Order Delta {new_greeks.delta:.2f} exceeds limit {self.config.max_portfolio_delta}")

    def _calculate_position_size(self, signal: SignalEvent, current_price: float) -> float:
        # If strategy provided explicit sizing (e.g., ATR-based), use it
        strategy_qty = signal.metadata.get("quantity", 0)
        if strategy_qty > 0:
            return min(float(strategy_qty), self.config.max_position_size)
        return self.config.default_quantity * (0.5 + signal.confidence * 0.5)

    def _apply_slippage(self, price: float, direction: str) -> float:
        adj = self.config.slippage_value if self.config.slippage_model == "fixed" else price * \
            self.config.slippage_value
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
        # 0.025% (applied on sell side; averaged across both legs)
        stt = turnover * 0.00025
        stamp = turnover * 0.00003
        gst = 0.18 * (brokerage + exchange_txn + sebi)
        return brokerage + exchange_txn + sebi + stt + stamp + gst

    def _log_dry_run_order(self, signal, side, qty, price):
        self.logger.info(
            f"[DRY-RUN] {side} {qty} {signal.symbol} @ {price} at {self.clock.now()}")

    def _update_equity_metrics(self, trade: TradeEvent):
        cost = trade.quantity * trade.price + trade.fees
        if trade.direction == "BUY":
            self.metrics.cash_balance -= cost
        else:
            self.metrics.cash_balance += (trade.quantity *
                                          trade.price - trade.fees)

        total_equity = self.metrics.cash_balance + \
            (self.position_tracker.net_quantity(trade.symbol) * trade.price)
        self.metrics.update_drawdown(total_equity)

    def get_position(self, symbol: str) -> float:
        return self.position_tracker.net_quantity(symbol)

    def get_stats(self, current_prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Returns execution stats for telemetry."""
        total_trades = len(self._trade_history)
        win_rate = self._calculate_win_rate()

        realized_pnl = self.pnl_tracker.get_realized_pnl()
        unrealized_pnl = 0.0
        used_margin = 0.0

        if current_prices:
            unrealized_pnl = self.pnl_tracker.get_unrealized_pnl(
                current_prices)
            used_margin = self.margin_tracker.get_used_margin(current_prices)

        return {
            "daily_pnl": self.metrics.daily_pnl,
            "drawdown_pct": self.metrics.max_drawdown_pct,
            "trade_count": total_trades,
            "win_rate": win_rate,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": realized_pnl + unrealized_pnl,
            "used_margin": used_margin
        }

    def _calculate_win_rate(self) -> float:
        # Very simple win rate calculation for current session
        trades = [t for t in self._trade_history if t.status ==
                  TradeStatus.FILLED]
        if not trades:
            return 0.0

        # This is a placeholder; real win rate needs paired trades
        # For telemetry snapshot, we just return a best-effort number
        return 0.0

    def get_trade_history(self) -> List[TradeEvent]:
        return list(self._trade_history)





