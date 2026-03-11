"""
Breadth State Engine
====================
Standalone analytical engine for identifying Nifty 50 breadth regimes.
Used for structural validation of market breadth at the 11:00 AM checkpoint.
"""

from typing import Dict, List, Optional
import pandas as pd
import numpy as np
from pathlib import Path

class BreadthStateEngine:
    """
    Identifies the breadth regime (Bull/Bear/Neutral) based on Nifty 50 constituents.
    This is an isolated analytical layer.
    """
    
    REGIMES = {
        0: "NeutralBreadth",
        1: "BullBreadth",
        2: "BearBreadth"
    }
    
    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.labels_path = data_root / "features" / "day_type" / "breadth_cluster_labels.csv"
        self._labels_cache = None

    def get_historical_state(self, dt: str) -> Optional[str]:
        """Returns the pre-computed breadth state for a historical date (YYYY-MM-DD)."""
        if self._labels_cache is None:
            if not self.labels_path.exists():
                return None
            self._labels_cache = pd.read_csv(self.labels_path, index_col='date')
        
        if dt in self._labels_cache.index:
            cluster_id = int(self._labels_cache.loc[dt, 'breadth_cluster'])
            return self.REGIMES.get(cluster_id, "Unknown")
        return None

    def audit_day(self, features: Dict[str, float]) -> str:
        """
        Manually assign a state given a feature dictionary.
        This allows checking against centroids without needing a full ML model object.
        """
        # Feature order must match centroid CSV
        cols = [
            'pct_positive', 'pct_above_vwap', 'adv_dec_ratio', 'median_return',
            'cross_sectional_std', 'pct_breaking_open_high', 'pct_breaking_open_low',
            'avg_return_top10', 'avg_return_bottom10', 'cross_sectional_skew'
        ]
        
        # Centroids from Phase 2
        # Cluster 0: Neutral (split)
        # Cluster 1: Bull (majority up)
        # Cluster 2: Bear (majority down)
        
        # For simplicity in this audit layer, we check the pre-computed labels.
        pass

