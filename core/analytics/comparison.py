"""
Analytics Comparison
--------------------
Compares sets of indicator facts for similarity or deviation.
"""
from typing import Dict, List
from core.analytics.models import ConfluenceInsight

class AnalyticsComparator:
    def compare_insights(self, insight_a: ConfluenceInsight, insight_b: ConfluenceInsight) -> float:
        """Returns similarity score between 0.0 and 1.0."""
        # Simple placeholder
        return 1.0 if insight_a.bias == insight_b.bias else 0.0
