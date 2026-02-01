"""
Analytics Reporting
-----------------
Generates human-readable summaries of analytical data.
"""
from typing import Dict, Any
from core.analytics.models import ConfluenceInsight

class AnalyticsReporter:
    def summarize_insight(self, insight: ConfluenceInsight) -> str:
        summary = f"Bias: {insight.bias.value}, Confidence: {insight.confidence_score*100:.1f}%\n"
        for r in insight.indicator_results:
            summary += f"- {r.name}: {r.bias.value} ({r.value:.2f})\n"
        return summary
