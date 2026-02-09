"""
Refactored Operations Facade
---------------------------
Read-only bridge for the Flask Ops dashboard using isolated databases.
"""
from typing import Dict, Any, List, Optional
from core.execution.handler import ExecutionHandler
from core.execution.health_monitor import HealthMonitor
from core.database.manager import DatabaseManager
from core.database.utils.market_hours import MarketHours
from pathlib import Path

class OpsFacade:
    """
    Assembles metrics and health status for the UI.
    """
    
    def __init__(self, execution: ExecutionHandler, health: HealthMonitor, db_manager: Optional[DatabaseManager] = None):
        self.execution = execution
        self.health = health
        self.db = db_manager or DatabaseManager(Path("data"))

    def get_live_metrics(self) -> Dict[str, Any]:
        return {
            "signals_received": self.execution.metrics.signals_received,
            "trades_executed": self.execution.metrics.trades_executed,
            "rejected_trades": self.execution.metrics.rejected_trades,
            "throughput": self.execution.metrics.get_throughput(),
            "drawdown": self.execution.metrics.get_drawdown(self.execution.metrics.max_equity or 0.0)
        }

    def get_health_status(self) -> Dict[str, Any]:
        status = self.health.get_status()
        
        # Add connectivity indicators expected by UI
        trading_db = self.db.data_root / 'trading' / 'trading.db'
        config_db = self.db.data_root / 'config' / 'config.db'
        
        # Get market status from ingestor if available
        import json
        market_status = "Open"
        ingestor_status_path = Path("logs/market_ingestor_status.json")
        if ingestor_status_path.exists():
            try:
                with open(ingestor_status_path, "r") as f:
                    ingestor_data = json.load(f)
                    market_status = ingestor_data.get("status", "Open")
            except Exception:
                pass

        status.update({
            "db_connected": trading_db.exists() and config_db.exists(),
            "broker_connected": True, # Assume connected for paper/dry-run
            "market_status": market_status
        })
        return status

    def get_confluence_matrix(self) -> List[Dict]:
        return []

    def get_websocket_status(self) -> Dict[str, Any]:
        """Reads current WebSocket status from config database."""
        try:
            with self.db.config_reader() as conn:
                row = conn.execute(
                    "SELECT status, updated_at, pid FROM websocket_status WHERE key = 'singleton'"
                ).fetchone()
                if row:
                    return {
                        "status": row[0],
                        "updated_at": row[1] if isinstance(row[1], str) else row[1].isoformat() if row[1] else None,
                        "pid": row[2]
                    }
        except Exception:
            pass
        # No row: infer status from market hours
        fallback = "DISCONNECTED" if MarketHours.is_market_open(MarketHours.get_ist_now()) else "CLOSED"
        return {"status": fallback, "updated_at": None, "pid": None}
