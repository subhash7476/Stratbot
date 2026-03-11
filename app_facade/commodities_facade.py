from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.database.manager import DatabaseManager
from services.commodity_strategy_orchestrator import CommodityStrategyOrchestrator


class CommoditiesFacade:
    """Orchestration access layer for commodities dashboard."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        execution_handler: Optional[Any] = None,
    ):
        self.db = db_manager or DatabaseManager(Path("data"))
        self.orchestrator = CommodityStrategyOrchestrator(
            db_manager=self.db,
            execution_handler=execution_handler,
        )

    def get_strategy_snapshot(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Generate or retrieve latest orchestrated strategy snapshot."""
        ts = now or datetime.now()
        snap = self.orchestrator.get_latest_snapshot(ts)
        return snap.to_dict()

    def get_strategy_metrics(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Get deterministic metrics payload from latest snapshot."""
        snap = self.get_strategy_snapshot(now)
        return {
            "snapshot_id": snap["snapshot_id"],
            "metrics": snap["metrics"],
            "data_freshness": snap["data_freshness"],
            "execution_status": snap["execution_status"],
        }

    def get_rejection_timeline(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Recent rejected snapshots for audit timeline."""
        try:
            with self.db.trading_reader() as conn:
                rows = conn.execute(
                    """
                    SELECT timestamp, snapshot_id, rejection_reason, selected_strike, regime
                    FROM commodity_strategy_snapshots
                    WHERE decision = 'REJECT'
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    [limit],
                ).fetchall()
            return [
                {
                    "timestamp": r[0],
                    "snapshot_id": r[1],
                    "rejection_reason": r[2],
                    "selected_strike": r[3],
                    "regime": r[4],
                }
                for r in rows
            ]
        except Exception:
            return []

    def get_usdinr_attribution(self, run_id_without: str, run_id_with: str) -> Dict[str, Any]:
        """Get with/without USDINR filter attribution report."""
        return self.orchestrator.build_usdinr_attribution(run_id_without, run_id_with)

    def get_state(self) -> Dict[str, Any]:
        """Deterministic state payload for commodities page."""
        snapshot = self.get_strategy_snapshot()
        return {
            "strategy_snapshot": snapshot,
            "audit_meta": {
                "snapshot_id": snapshot.get("snapshot_id"),
                "execution_status": snapshot.get("execution_status"),
                "rejection_timeline": self.get_rejection_timeline(limit=20),
            },
        }
