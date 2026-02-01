"""
Analytics Facade
----------------
Read-only bridge for analytical data in the UI.
"""
from typing import List, Dict, Optional
from core.data.analytics_persistence import get_latest_insights

class AnalyticsFacade:
    def get_insights(self, symbol: Optional[str] = None, limit: int = 50) -> List[Dict]:
        return get_latest_insights(symbol, limit)
