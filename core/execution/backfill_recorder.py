"""
Backfill Recorder
-----------------
Captures results during systematic strategy backfills.
"""
from typing import List, Dict, Any
from core.execution.backfill_models import BackfillTrade

class BackfillRecorder:
    def __init__(self):
        self.trades: List[Dict[str, Any]] = []

    def record_trade(self, trade: Dict[str, Any]):
        self.trades.append(trade)

    def get_summary(self) -> Dict[str, Any]:
        return {
            "total_trades": len(self.trades)
        }
