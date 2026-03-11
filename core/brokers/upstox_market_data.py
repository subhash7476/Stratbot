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

    @staticmethod
    def _extract_ltp_for_key(data: dict, instrument_key: str) -> Optional[float]:
        """Extract LTP for a requested instrument key from Upstox quote payload."""
        if not isinstance(data, dict) or not data:
            return None

        # Preferred: match by instrument_token field (stable for numeric keys like NSE_FO|45493)
        for _, entry in data.items():
            if isinstance(entry, dict) and entry.get("instrument_token") == instrument_key:
                ltp = entry.get("last_price") or entry.get("ltp")
                return float(ltp) if ltp is not None else None

        # Fallback: direct key lookup for payloads keyed by transformed instrument key
        lookup_key = instrument_key.replace("|", ":")
        entry = data.get(lookup_key, {})
        ltp = entry.get("last_price") or entry.get("ltp")
        return float(ltp) if ltp is not None else None

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
            ltp = self._extract_ltp_for_key(data, instrument_key)
            if ltp is not None:
                return ltp
            logger.warning(
                "[UpstoxMarketData] LTP missing in response for %s. Response keys=%s",
                instrument_key,
                list(data.keys())[:5],
            )
            return None

        except Exception as e:
            logger.error(f"[UpstoxMarketData] Exception fetching LTP for {instrument_key}: {e}")
            return None

    def fetch_ltp_batch(self, instrument_keys: list[str]) -> dict[str, float]:
        """Fetch LTP for multiple instruments in a single API call.
        Returns {instrument_key: ltp} for keys that returned valid prices."""
        if not instrument_keys:
            return {}
        try:
            from core.auth.credentials import credentials
            token = credentials.get("access_token")
            if not token:
                logger.error("[UpstoxMarketData] No access token found in credentials")
                return {}

            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json"
            }
            params = {
                "instrument_key": ",".join(instrument_keys)
            }

            resp = requests.get(
                f"{self.BASE_URL}/market-quote/quotes",
                headers=headers,
                params=params,
                timeout=5
            )

            if resp.status_code != 200:
                logger.error(f"[UpstoxMarketData] Batch API Error {resp.status_code}: {resp.text}")
                return {}

            data = resp.json().get("data", {})
            result = {}
            for key in instrument_keys:
                ltp = self._extract_ltp_for_key(data, key)
                if ltp is not None:
                    result[key] = ltp
            return result

        except Exception as e:
            logger.error(f"[UpstoxMarketData] Batch exception: {e}")
            return {}
