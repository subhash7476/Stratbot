"""
Operations Facade
-----------------
Read-only bridge for the Flask Ops dashboard.
"""
from typing import Dict, Any, List, Optional
from core.execution.handler import ExecutionHandler
from core.execution.health_monitor import HealthMonitor
from core.data.duckdb_client import db_cursor
from core.data.market_hours import MarketHours

class OpsFacade:
    """
    Assembles metrics and health status for the UI.
    """
    
    def __init__(self, execution: ExecutionHandler, health: HealthMonitor):
        self.execution = execution
        self.health = health

    def get_live_metrics(self) -> Dict[str, Any]:
        return {
            "signals_received": self.execution.metrics.signals_received,
            "trades_executed": self.execution.metrics.trades_executed,
            "rejected_trades": self.execution.metrics.rejected_trades,
            "throughput": self.execution.metrics.get_throughput(),
            "drawdown": self.execution.metrics.get_drawdown(self.execution.metrics.max_equity or 0.0)
        }

    def get_health_status(self) -> Dict[str, Any]:
        return self.health.get_status()

    def get_confluence_matrix(self) -> List[Dict]:
        # Placeholder for real matrix data
        return []

    @staticmethod
    def get_websocket_status() -> Dict[str, Any]:
        """Reads current WebSocket status from DuckDB."""
        try:
            with db_cursor(read_only=True) as conn:
                row = conn.execute(
                    "SELECT status, updated_at, pid FROM websocket_status WHERE key = 'singleton'"
                ).fetchone()
                if row:
                    return {
                        "status": row[0],
                        "updated_at": row[1].isoformat() if row[1] else None,
                        "pid": row[2]
                    }
        except Exception:
            pass
        # No row: infer status from market hours
        fallback = "DISCONNECTED" if MarketHours.is_market_open(MarketHours.get_ist_now()) else "CLOSED"
        return {"status": fallback, "updated_at": None, "pid": None}
