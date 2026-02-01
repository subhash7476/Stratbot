"""
Backfill Models
---------------
Dataclasses for historical backfill data.
"""
from dataclasses import dataclass
from datetime import datetime

@dataclass
class BackfillTrade:
    symbol: str
    timestamp: datetime
    price: float
    qty: int
    direction: str
