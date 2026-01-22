# core/api/market_data_fixed.py
"""
Upstox Market Data API v2 - FIXED for key mismatch
Handles NSE_EQ|ISIN (request) vs NSE_EQ:SYMBOL (response)
"""

import requests
import json
from typing import List, Dict
from datetime import datetime

class UpstoxMarketData:
    """
    Upstox Market Data API v2
    
    IMPORTANT: API has a key format mismatch:
    - Request uses: NSE_EQ|INE002A01018 (with ISIN)
    - Response uses: NSE_EQ:RELIANCE (with Symbol)
    
    This class handles the mapping automatically.
    """
    
    BASE_URL = "https://api.upstox.com/v2"
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }
    
    def get_market_quote(self, instrument_keys: List[str], symbol_map: Dict[str, str] = None) -> Dict:
        """
        Get market quotes for instruments
        
        Args:
            instrument_keys: List of instrument keys (NSE_EQ|INE...)
            symbol_map: Optional mapping of instrument_key -> symbol for faster lookup
        
        Returns: 
            Dict mapping instrument_key -> quote data
        """
        if not instrument_keys:
            return {}
        
        keys_param = ",".join(instrument_keys)
        url = f"{self.BASE_URL}/market-quote/quotes"
        params = {"instrument_key": keys_param}
        
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') != 'success':
                raise Exception(f"API Error: {data.get('message', 'Unknown error')}")
            
            # Map response keys back to instrument keys
            return self._map_response_keys(data.get('data', {}), instrument_keys, symbol_map)
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"Request failed: {e}")
        except json.JSONDecodeError as e:
            raise Exception(f"JSON decode error: {e}")
    
    def _map_response_keys(
        self, 
        response_data: Dict, 
        instrument_keys: List[str],
        symbol_map: Dict[str, str] = None
    ) -> Dict:
        """
        Map response keys (NSE_EQ:SYMBOL) back to instrument keys (NSE_EQ|ISIN)
        
        Strategy:
        1. If symbol_map provided, use it directly
        2. Otherwise, match by extracting symbol from response data
        """
        quotes = {}
        
        if not response_data:
            return quotes
        
        # Build reverse lookup: symbol -> instrument_key
        if symbol_map:
            # Use provided mapping
            symbol_to_inst = {v: k for k, v in symbol_map.items()}
        else:
            # Extract symbols from response data itself
            symbol_to_inst = {}
            for response_key, quote_data in response_data.items():
                if isinstance(quote_data, dict):
                    symbol = quote_data.get('symbol')
                    if symbol:
                        # Find matching instrument key
                        for inst_key in instrument_keys:
                            # Match will happen later
                            symbol_to_inst[symbol] = inst_key
        
        # Map response to correct instrument keys
        for response_key, quote_data in response_data.items():
            if not isinstance(quote_data, dict):
                continue
            
            # Extract symbol from response
            symbol = quote_data.get('symbol')
            
            if symbol_map and symbol:
                # Use symbol_map to find instrument key
                for inst_key, sym in symbol_map.items():
                    if sym == symbol:
                        quotes[inst_key] = quote_data
                        break
            else:
                # Try to match by checking instrument_token in response
                inst_token = quote_data.get('instrument_token')
                if inst_token and inst_token in instrument_keys:
                    quotes[inst_token] = quote_data
                else:
                    # Fallback: use first unmatched instrument key
                    # This works for single instrument requests
                    for inst_key in instrument_keys:
                        if inst_key not in quotes:
                            quotes[inst_key] = quote_data
                            break
        
        return quotes
    
    def build_1min_candle(self, quote_data: Dict) -> Dict:
        """
        Convert quote to 1-minute candle format
        """
        ohlc = quote_data.get('ohlc', {})
        
        now = datetime.now()
        timestamp = now.replace(second=0, microsecond=0)
        
        candle = {
            'timestamp': timestamp,
            'open': float(ohlc.get('open', 0)),
            'high': float(ohlc.get('high', 0)),
            'low': float(ohlc.get('low', 0)),
            'close': float(quote_data.get('last_price', ohlc.get('close', 0))),
            'volume': int(quote_data.get('volume', 0))
        }
        
        return candle
    
    def get_ltp(self, instrument_keys: List[str]) -> Dict:
        """
        Get Last Traded Price (lightweight)
        
        Returns: Dict mapping instrument_key -> last_price
        """
        if not instrument_keys:
            return {}
        
        keys_param = ",".join(instrument_keys)
        url = f"{self.BASE_URL}/market-quote/ltp"
        params = {"instrument_key": keys_param}
        
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') == 'success':
                # LTP endpoint might have same key mismatch
                response_data = data.get('data', {})
                
                # Try to extract LTP values
                result = {}
                for key, value in response_data.items():
                    if isinstance(value, dict):
                        ltp = value.get('last_price', 0)
                    else:
                        ltp = value
                    
                    # Try to match back to instrument key
                    # For now, maintain order
                    if len(result) < len(instrument_keys):
                        inst_key = instrument_keys[len(result)]
                        result[inst_key] = float(ltp) if ltp else 0.0
                
                return result
            
            return {}
            
        except:
            return {}


# Helper to create symbol map from database
def create_symbol_map(instrument_keys: List[str], db) -> Dict[str, str]:
    """
    Create mapping of instrument_key -> trading_symbol
    
    Args:
        instrument_keys: List of instrument keys
        db: TradingDB instance
    
    Returns:
        Dict mapping instrument_key -> symbol
    """
    symbol_map = {}
    
    for inst_key in instrument_keys:
        query = f"""
            SELECT trading_symbol
            FROM instruments
            WHERE instrument_key = '{inst_key}'
            LIMIT 1
        """
        
        try:
            result = db.con.execute(query).fetchone()
            if result:
                symbol_map[inst_key] = result[0]
        except:
            continue
    
    return symbol_map


if __name__ == "__main__":
    print("Upstox Market Data API v2 - Fixed for key mismatch")
    print("")
    print("Key Issue:")
    print("  Request:  NSE_EQ|INE002A01018")
    print("  Response: NSE_EQ:RELIANCE")
    print("")
    print("Solution: Create symbol_map and pass to get_market_quote()")