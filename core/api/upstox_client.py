import requests
import logging
from datetime import date, datetime
from typing import Dict, Optional, List
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

class UpstoxClient:
    """
    Wrapper for Upstox REST API.
    """

    BASE_URL_V2 = "https://api.upstox.com/v2"
    BASE_URL_V3 = "https://api.upstox.com/v3"

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
        Fetches historical OHLC candles (V2 API).
        """
        endpoint = f"/historical-candle/{instrument_key}/{timeframe}/{to_date}/{from_date}"
        url = f"{self.BASE_URL_V2}{endpoint}"

        try:
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Upstox V2 API error: {e}")
            return {"status": "error", "message": str(e)}

    def fetch_historical_candles_v3(
        self,
        instrument_key: str,
        unit: str,
        interval: int,
        to_date: str,
        from_date: Optional[str] = None
    ) -> List[Dict]:
        """
        Fetches historical candles using V3 API.

        Args:
            instrument_key: Format like "NSE_EQ|INE848E01016"
            unit: minutes, hours, days, weeks, months
            interval: 1-300 for minutes, 1-5 for hours, 1 for days/weeks/months
            to_date: YYYY-MM-DD format
            from_date: YYYY-MM-DD format (optional, defaults to to_date)

        Returns:
            List of candle dictionaries with timestamp, open, high, low, close, volume, open_interest
        """
        # Set default from_date to to_date if not provided
        if from_date is None:
            from_date = to_date

        endpoint = f"/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}"
        url = f"{self.BASE_URL_V3}{endpoint}"

        try:
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "success":
                error_code = data.get("errors", [{}])[0].get("code", "UNKNOWN_ERROR") if data.get("errors") else "UNKNOWN_ERROR"
                error_msg = data.get("errors", [{}])[0].get("message", "Unknown error") if data.get("errors") else "Unknown error"

                # Handle specific Upstox error codes
                if error_code in ["UDAPI1021", "UDAPI100011"]:
                    logger.error(f"Invalid instrument key: {instrument_key}. Error: {error_code} - {error_msg}")
                    raise ValueError(f"Invalid instrument key: {instrument_key}")
                elif error_code == "UDAPI1022":
                    logger.error(f"End date required. Error: {error_code} - {error_msg}")
                    raise ValueError("End date is required")
                elif error_code in ["UDAPI1146", "UDAPI1147", "UDAPI1148"]:
                    logger.error(f"Invalid parameters: {error_code} - {error_msg}")
                    raise ValueError(f"Invalid parameters: {error_msg}")
                else:
                    logger.error(f"Upstox V3 API error: {error_code} - {error_msg}")
                    raise Exception(f"Upstox API error: {error_code} - {error_msg}")

            # Parse candles into structured format
            candles = []
            ist_tz = ZoneInfo("Asia/Kolkata")

            for candle_data in data.get("data", {}).get("candles", []):
                if len(candle_data) >= 7:  # [timestamp, open, high, low, close, volume, open_interest]
                    # Convert timestamp to datetime (assuming it's in IST)
                    timestamp_str = candle_data[0]
                    timestamp_dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    # Convert to IST timezone
                    timestamp_ist = timestamp_dt.astimezone(ist_tz)

                    candle = {
                        "timestamp": timestamp_ist,
                        "open": float(candle_data[1]),
                        "high": float(candle_data[2]),
                        "low": float(candle_data[3]),
                        "close": float(candle_data[4]),
                        "volume": int(candle_data[5]),
                        "open_interest": int(candle_data[6]) if candle_data[6] is not None else None
                    }
                    candles.append(candle)

            return candles

        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                logger.error("Access token expired or invalid")
                raise ValueError("Access token expired or invalid")
            elif response.status_code == 429:
                logger.error("Rate limit exceeded")
                raise Exception("Rate limit exceeded")
            else:
                logger.error(f"HTTP error in Upstox V3 API: {e}")
                raise Exception(f"HTTP error: {e}")
        except Exception as e:
            logger.error(f"Upstox V3 API error: {e}")
            raise
