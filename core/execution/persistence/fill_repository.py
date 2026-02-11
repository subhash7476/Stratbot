import logging
from typing import List
from datetime import datetime
from core.execution.persistence.execution_store import ExecutionStore
from core.execution.order_lifecycle import FillEvent


class FillRepository:
    def __init__(self, store: ExecutionStore):
        self.store = store
        self.logger = logging.getLogger(__name__)

    def save(self, fill: FillEvent):
        try:
            with self.store.get_connection() as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO fills 
                    (fill_id, order_id, symbol, quantity, price, side, fee, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fill.fill_id,
                        fill.order_id,
                        fill.symbol,
                        fill.quantity,
                        fill.price,
                        fill.side,
                        fill.fee,
                        fill.timestamp.isoformat()
                    )
                )
        except Exception as e:
            self.logger.error(f"Failed to save fill {fill.fill_id}: {e}")
            raise

    def get_all(self) -> List[FillEvent]:
        fills = []
        try:
            with self.store.get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM fills ORDER BY timestamp ASC").fetchall()
                for row in rows:
                    fill = FillEvent(
                        fill_id=row[0],
                        order_id=row[1],
                        symbol=row[2],
                        quantity=float(row[3]),
                        price=float(row[4]),
                        side=row[5],
                        fee=float(row[6]),
                        timestamp=datetime.fromisoformat(row[7])
                    )
                    fills.append(fill)
        except Exception as e:
            self.logger.error(f"Failed to load fills: {e}")
        return fills
