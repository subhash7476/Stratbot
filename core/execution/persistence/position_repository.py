import logging
from typing import Dict
from datetime import datetime
from core.execution.persistence.execution_store import ExecutionStore
from core.execution.position_models import Position, PositionSide


class PositionRepository:
    def __init__(self, store: ExecutionStore):
        self.store = store
        self.logger = logging.getLogger(__name__)

    def save_snapshot(self, position: Position):
        try:
            conn = self.store.get_connection()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO positions 
                    (symbol, side, quantity, avg_price, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        position.symbol,
                        position.side.value,
                        position.quantity,
                        position.avg_price,
                        position.last_updated.isoformat() if position.last_updated else datetime.now().isoformat(),
                    )
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(
                f"Failed to save position snapshot for {position.symbol}: {e}")

    def save(self, position: Position):
        """Backward-compatible alias used by PositionTracker."""
        self.save_snapshot(position)

    def load_all(self) -> Dict[str, Position]:
        positions = {}
        try:
            conn = self.store.get_connection()
            try:
                rows = conn.execute("SELECT * FROM positions").fetchall()
                for row in rows:
                    # Parse symbol into Instrument object
                    from core.instruments.instrument_parser import InstrumentParser
                    instrument = InstrumentParser.parse(row[0])

                    pos = Position(
                        instrument=instrument,
                        side=PositionSide(row[1]),
                        quantity=float(row[2]),
                        avg_price=float(row[3]),
                        last_updated=datetime.fromisoformat(row[4])
                    )
                    positions[pos.symbol] = pos
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Failed to load positions: {e}")
        return positions

