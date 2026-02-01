"""
Operations Facade
-----------------
Read-only bridge for the Flask Ops dashboard.
"""
from typing import Dict, Any, List
from core.execution.handler import ExecutionHandler
from core.execution.health_monitor import HealthMonitor

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
            "drawdown": self.execution.metrics.get_drawdown()
        }

    def get_health_status(self) -> Dict[str, Any]:
        return self.health.get_status()

    def get_confluence_matrix(self) -> List[Dict]:
        # Placeholder for real matrix data
        return []
