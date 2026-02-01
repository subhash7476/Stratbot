import sys
import os
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.data.analytics_persistence import save_insight, save_regime_snapshot
from core.analytics.models import ConfluenceInsight, Bias, ConfluenceSignal, IndicatorResult

def mock_update():
    print("Simulating offline analytics update...")
    # (In a real script, this would run heavy technical analysis)
    # and save results to DuckDB via save_insight
    print("✅ Analytics updated.")

if __name__ == "__main__":
    mock_update()
