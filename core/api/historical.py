# core/api/historical.py
"""
Wrapper for historical fetch used by pages.
Delegates to data.data_manager.fetch_historical_range
Provides a stable import target: from core.api.historical import fetch_and_save_data
"""

import sys, os
# ensure project root is on sys.path (Streamlit runs pages from temp dirs)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from typing import List
from datetime import date
from data.data_manager import fetch_historical_range, preflight_plan  # type: ignore

def fetch_and_save_data(symbol: str,
                        segment: str,
                        interval: str,
                        from_date: date,
                        to_date: date,
                        force: bool = False) -> List[str] | None:
    """
    Backwards-compatible entry that pages call.
    Returns list of saved partition file paths or None on failure.
    """
    try:
        saved = fetch_historical_range(symbol=symbol,
                                       segment=segment,
                                       interval=interval,
                                       from_date=from_date,
                                       to_date=to_date,
                                       force=force)
        return saved
    except Exception as e:
        # bubble up; page will catch and present error
        raise
