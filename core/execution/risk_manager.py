from typing import Set, Optional, Any
from core.execution.order_models import NormalizedOrder
from core.execution.risk_models import RiskDecision, RiskStatus


class RiskManager:
    """
    Pre-trade Risk Manager.
    Evaluates NormalizedOrders against safety and compliance limits.
    """

    def __init__(self, config: Any = None):
        self.max_order_quantity = 1000.0
        self.denied_symbols: Set[str] = set()
        self.allowed_symbols: Set[str] = set()
        self.global_kill_switch = False

        if config:
            # Support both object (ExecutionConfig) and dict access
            if hasattr(config, "max_position_size"):
                self.max_order_quantity = float(config.max_position_size)
            elif isinstance(config, dict) and "max_position_size" in config:
                self.max_order_quantity = float(config["max_position_size"])

            if isinstance(config, dict):
                self.denied_symbols = set(config.get("denied_symbols", []))
                self.allowed_symbols = set(config.get("allowed_symbols", []))
                self.global_kill_switch = config.get(
                    "global_kill_switch", False)

    def evaluate(self, order: NormalizedOrder, trades_today: int = 0, max_trades_per_day: int = 100) -> RiskDecision:
        """
        Pure logic evaluation of an order.
        Returns RiskDecision(APPROVED) or RiskDecision(REJECTED).
        """
        # 1. Global Kill Switch
        if self.global_kill_switch:
            return RiskDecision(RiskStatus.REJECTED, "Global kill switch is active.")

        # 2. Daily Trade Limit (Stubbed check based on provided count)
        if trades_today >= max_trades_per_day:
            return RiskDecision(RiskStatus.REJECTED, f"Daily trade limit ({max_trades_per_day}) reached.")

        # 3. Max Quantity per Order
        if order.quantity > self.max_order_quantity:
            return RiskDecision(
                RiskStatus.REJECTED,
                f"Order quantity {order.quantity} exceeds limit {self.max_order_quantity}."
            )

        # 4. Symbol Filtering
        if self.allowed_symbols and order.symbol not in self.allowed_symbols:
            return RiskDecision(
                RiskStatus.REJECTED,
                f"Symbol {order.symbol} is not in the allow list."
            )

        if order.symbol in self.denied_symbols:
            return RiskDecision(
                RiskStatus.REJECTED,
                f"Symbol {order.symbol} is in the deny list."
            )

        return RiskDecision(RiskStatus.APPROVED)
