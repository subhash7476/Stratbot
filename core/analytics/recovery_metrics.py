"""
Recovery Metrics
----------------
Analyzes system recovery performance.
"""
class RecoveryMetricsAnalyzer:
    def calculate_recovery_time(self, downtime_start, uptime_start) -> float:
        return (uptime_start - downtime_start).total_seconds()
