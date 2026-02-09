import logging
import time
from typing import Any, Dict, Optional
from core.messaging.zmq_handler import ZmqPublisher

logger = logging.getLogger(__name__)

class TelemetryPublisher:
    """
    Fire-and-forget telemetry publisher.
    Designed to be non-invasive and exception-safe.
    """
    def __init__(self, host: str, port: int, node_name: str, bind: bool = False):
        self.node_name = node_name
        self.publisher: Optional[ZmqPublisher] = None
        try:
            self.publisher = ZmqPublisher(host, port, bind=bind)
        except Exception as e:
            logger.error(f"Failed to initialize TelemetryPublisher for {node_name}: {e}")

    def publish_metrics(self, data: Dict[str, Any]):
        """Snapshot of node metrics (PnL, drawdown, etc.)"""
        self._safe_publish(f"telemetry.metrics.{self.node_name}", "metrics", data)

    def publish_positions(self, data: Dict[str, Any]):
        """Snapshot of current positions."""
        self._safe_publish(f"telemetry.positions.{self.node_name}", "positions", data)

    def publish_health(self, data: Dict[str, Any]):
        """Snapshot of node health (CPU, Memory, heartbeats)."""
        self._safe_publish(f"telemetry.health.{self.node_name}", "health", data)

    def publish_log(self, level: str, message: str):
        """Edge-triggered log forwarding (lossy)."""
        data = {
            "level": level,
            "msg": message,
            "node": self.node_name
        }
        self._safe_publish(f"telemetry.logs.{self.node_name}", "log", data)

    def _safe_publish(self, topic: str, msg_type: str, data: Dict[str, Any]):
        """Internal helper to swallow all errors and prevent blocking."""
        if not self.publisher:
            return
            
        try:
            # We use a separate version for telemetry if needed, currently 1
            self.publisher.publish(topic, msg_type, data)
        except Exception as e:
            # We log but do not raise. Telemetry failure must not break trading.
            # We use a throttled log or simple debug to avoid log spam if ZMQ is down.
            logger.debug(f"Telemetry publish failed: {e}")

    def close(self):
        if self.publisher:
            try:
                # Store ref and set to None first to avoid race conditions
                pub = self.publisher
                self.publisher = None
                pub.close()
            except:
                pass
