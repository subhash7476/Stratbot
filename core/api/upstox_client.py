import requests
import logging
from datetime import date
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class UpstoxClient:
    """
    Wrapper for Upstox REST API.
    """
    
    BASE_URL = "https://api.upstox.com/v2"
    
    def __init__(self, access_token: str):
        self.access_token = access_token

    def _get_headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json"
        }

    def fetch_ohlc(
        self, 
        instrument_key: str, 
        timeframe: str, 
        interval_num: int, 
        from_date: date, 
        to_date: date
    ) -> Dict:
        """
        Fetches historical OHLC candles.
        """
        endpoint = f"/historical-candle/{instrument_key}/{timeframe}/{to_date}/{from_date}"
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Upstox API error: {e}")
            return {"status": "error", "message": str(e)}
