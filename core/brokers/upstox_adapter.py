import time
import logging
import requests
from typing import Dict, Optional, List
from core.brokers.base import BrokerAdapter
from core.events import OrderEvent, OrderStatus
from core.execution.position_tracker import Position
from core.clock import Clock

class UpstoxAdapter(BrokerAdapter):
    """
    Adapter for Upstox API v2.
    """
    
    BASE_URL = "https://api.upstox.com/v2"
    
    def __init__(self, api_key: str, api_secret: str, access_token: str, clock: Clock):
        self.api_key = api_key
        self.api_secret = api_secret
        self.access_token = access_token
        self.clock = clock
        self.logger = logging.getLogger(__name__)
        self._last_req_time = 0.0
        self._req_interval = 0.11 # Slightly more than 0.1 to be safe (9 req/sec)
        
    def _rate_limit(self):
        """Enforces Upstox rate limits."""
        now = time.time()
        elapsed = now - self._last_req_time
        if elapsed < self._req_interval:
            time.sleep(self._req_interval - elapsed)
        self._last_req_time = time.time()

    def _get_headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None, params: Optional[Dict] = None):
        self._rate_limit()
        url = f"{self.BASE_URL}{endpoint}"
        
        retries = 3
        backoff = 1.0 # seconds
        
        for attempt in range(retries):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=self._get_headers(),
                    json=data,
                    params=params,
                    timeout=10
                )
                
                if response.status_code in (401, 403):
                    self.logger.error(f"Authentication failed on {endpoint} (HTTP {response.status_code}).")
                    raise RuntimeError(f"Upstox token expired or invalid (HTTP {response.status_code})")

                if response.status_code == 429: # Rate limit
                    time.sleep(backoff * (2 ** attempt))
                    continue

                response.raise_for_status()
                return response.json()
                
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Upstox API error on {endpoint}: {e}")
                if attempt == retries - 1:
                    raise
                time.sleep(backoff * (2 ** attempt))
        
        return None

    def place_order(self, order: OrderEvent) -> str:
        payload = {
            "quantity": int(order.quantity),
            "product": "I", # INTRADAY
            "validity": "DAY",
            "price": order.price if order.order_type.value == "LIMIT" else 0,
            "tag": order.signal_id_reference,
            "instrument_token": order.symbol,
            "order_type": "MARKET" if order.order_type.value == "MARKET" else "LIMIT",
            "transaction_type": "BUY" if order.side == "BUY" else "SELL",
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False
        }
        
        try:
            response = self._make_request("POST", "/order/place", data=payload)
            if response and response.get('status') == 'success':
                order_id = response['data']['order_id']
                self.logger.info(f"Order placed successfully. ID: {order_id}")
                return order_id
            else:
                error_msg = response.get('errors', 'Unknown error') if response else "No response"
                raise RuntimeError(f"Failed to place order: {error_msg}")
        except Exception as e:
            self.logger.error(f"Critical failure in place_order: {e}")
            raise

    def get_order_status(self, order_id: str) -> OrderStatus:
        try:
            response = self._make_request("GET", "/order/details", params={"order_id": order_id})
            if response and response.get('status') == 'success':
                data = response['data']
                status_str = data.get('status', '')
                
                mapping = {
                    "completed": OrderStatus.FILLED,
                    "rejected": OrderStatus.REJECTED,
                    "cancelled": OrderStatus.CANCELLED,
                    "open": OrderStatus.OPEN,
                    "trigger pending": OrderStatus.OPEN
                }
                return mapping.get(status_str.lower(), OrderStatus.SUBMITTED)
            return OrderStatus.SUBMITTED
        except Exception:
            return OrderStatus.SUBMITTED

    def get_positions(self) -> Dict[str, Position]:
        try:
            response = self._make_request("GET", "/portfolio/net-positions")
            positions = {}
            if response and response.get('status') == 'success':
                for pos_data in response['data']:
                    symbol = pos_data['trading_symbol']
                    qty = float(pos_data['quantity'])
                    avg_price = float(pos_data['average_price'])
                    
                    positions[symbol] = Position(
                        symbol=symbol,
                        quantity=qty,
                        avg_entry_price=avg_price,
                        last_update=self.clock.now()
                    )
            return positions
        except Exception:
            return {}

    def cancel_order(self, order_id: str) -> bool:
        try:
            response = self._make_request("DELETE", "/order/cancel", params={"order_id": order_id})
            return bool(response and response.get('status') == 'success')
        except Exception:
            return False
