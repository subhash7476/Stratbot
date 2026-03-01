import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class UpstoxMarketData:
    """
    Thin wrapper around Upstox V2 Market Quote API.
    Used for fetching live LTP for specific option contracts.
    """
    BASE_URL = "https://api.upstox.com/v2"

    def fetch_ltp(self, instrument_key: str) -> Optional[float]:
        """
        Fetch last traded price for a single instrument.
        
        Args:
            instrument_key: Format 'NSE_FO|NIFTY26FEB2622000CE'
            
        Returns:
            LTP as float or None if failed
        """
        try:
            from core.auth.credentials import credentials
            token = credentials.get("access_token")
            if not token:
                logger.error("[UpstoxMarketData] No access token found in credentials")
                return None

            # Upstox returns key as "NSE_FO:SYMBOL" (colon) not "NSE_FO|SYMBOL"
            lookup_key = instrument_key.replace("|", ":")
            
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json"
            }
            params = {
                "instrument_key": instrument_key
            }
            
            resp = requests.get(
                f"{self.BASE_URL}/market-quote/quotes",
                headers=headers,
                params=params,
                timeout=5
            )
            
            if resp.status_code != 200:
                logger.error(f"[UpstoxMarketData] API Error {resp.status_code}: {resp.text}")
                return None
                
            data = resp.json().get("data", {})
            entry = data.get(lookup_key, {})
            
            ltp = entry.get("last_price") or entry.get("ltp")
            if ltp is not None:
                return float(ltp)
                
            return None

        except Exception as e:
            logger.error(f"[UpstoxMarketData] Exception fetching LTP for {instrument_key}: {e}")
            return None
