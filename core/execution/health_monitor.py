"""
Health Monitor
--------------
Tracks system-wide operational health metrics (latency, connectivity).
"""
import time
from typing import Dict, Any

class HealthMonitor:
    """
    Monitors system health and performance.
    """
    
    def __init__(self):
        self._start_time = time.time()
        self._last_error_time = 0
        self._error_count = 0

    def record_error(self):
        self._error_count += 1
        self._last_error_time = time.time()

    def get_status(self) -> Dict[str, Any]:
        return {
            "uptime": time.time() - self._start_time,
            "error_count": self._error_count,
            "last_error": self._last_error_time,
            "status": "healthy" if self._error_count < 10 else "degraded"
        }
