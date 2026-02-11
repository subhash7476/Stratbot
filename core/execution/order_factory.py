from datetime import datetime
from uuid import uuid4
from typing import Optional
from core.events import SignalEvent, SignalType
from core.execution.order_models import (
    NormalizedOrder,
    OrderSide,
    OrderType,
    InstrumentType,
    OrderMetadata
)
from core.execution.position_models import Position, PositionSide


class OrderFactoryError(Exception):
    """Raised when signal to order conversion fails."""
    pass


class OrderFactory:
    """
    Pure transformation layer: SignalEvent -> NormalizedOrder.
    No side effects. No broker or position knowledge.
    """

    @staticmethod
    def create_order(signal: SignalEvent, current_position: Optional[Position] = None) -> NormalizedOrder:
        """
        Converts a SignalEvent into a NormalizedOrder.
        Validates required fields and enforces instrument/type constraints.
        Resolves EXIT signals using current_position context.
        """
        # 1. Validate Instrument (Only EQUITY supported in Phase 1)
        # We assume EQUITY as default for now as per constraints
        instrument = InstrumentType.EQUITY

        # 2. Map Side & Reject EXIT
        quantity = 0

        if signal.signal_type == SignalType.EXIT:
            if not current_position or current_position.side == PositionSide.FLAT:
                raise OrderFactoryError(
                    f"Received EXIT signal for {signal.symbol} but position is FLAT.")

            # Resolve EXIT to closing order
            side = OrderSide.SELL if current_position.side == PositionSide.LONG else OrderSide.BUY
            quantity = int(current_position.quantity)

        elif signal.signal_type == SignalType.BUY:
            side = OrderSide.BUY
            quantity = int(signal.metadata.get("quantity", 0))
        elif signal.signal_type == SignalType.SELL:
            side = OrderSide.SELL
            quantity = int(signal.metadata.get("quantity", 0))
        else:
            raise OrderFactoryError(
                f"Unsupported SignalType: {signal.signal_type}")

        # 3. Extract Signal ID
        signal_id = getattr(signal, 'signal_id',
                            signal.metadata.get('signal_id'))
        if not signal_id:
            # Fallback deterministic ID if missing (should be handled by handler, but factory ensures it exists)
            from hashlib import sha256
            raw_id = f"{signal.symbol}_{signal.strategy_id}_{signal.timestamp.isoformat()}"
            signal_id = sha256(raw_id.encode()).hexdigest()

        return NormalizedOrder(
            symbol=signal.symbol,
            instrument_type=instrument,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            strategy_id=signal.strategy_id,
            signal_id=str(signal_id),
            timestamp=datetime.now(),  # Execution-time timestamp
            correlation_id=uuid4(),
            metadata=OrderMetadata(
                original_confidence=signal.confidence,
                strategy_metadata=signal.metadata
            )
        )
