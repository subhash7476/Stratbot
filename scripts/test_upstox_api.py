# debug_api_response.py
"""
Debug script to see actual API response structure
"""

import sys
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.config import get_access_token
from core.database import TradingDB
import requests
import json

def debug_api_response():
    """Show raw API response"""
    
    print("=" * 70)
    print("🔍 API RESPONSE DEBUG")
    print("=" * 70)
    
    token = get_access_token()
    print(f"\n✓ Token loaded")
    
    # Get RELIANCE
    db = TradingDB()
    
    query = """
        SELECT instrument_key, trading_symbol
        FROM instruments
        WHERE trading_symbol = 'RELIANCE'
          AND segment = 'NSE_EQ'
        LIMIT 1
    """
    
    result = db.con.execute(query).fetchone()
    inst_key, symbol = result
    
    print(f"✓ Testing with: {symbol} ({inst_key})")
    
    # Call API
    url = "https://api.upstox.com/v2/market-quote/quotes"
    params = {"instrument_key": inst_key}
    
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    
    print(f"\n🔄 Calling API...")
    
    response = requests.get(url, headers=headers, params=params, timeout=10)
    
    print(f"\n✓ Status: {response.status_code}")
    
    # Parse JSON
    data = response.json()
    
    print("\n" + "=" * 70)
    print("📋 FULL JSON RESPONSE:")
    print("=" * 70)
    print(json.dumps(data, indent=2))
    
    print("\n" + "=" * 70)
    print("🔍 STRUCTURE ANALYSIS:")
    print("=" * 70)
    
    print(f"\nTop-level keys: {list(data.keys())}")
    
    if 'data' in data:
        print(f"\ndata type: {type(data['data'])}")
        print(f"data keys: {list(data['data'].keys()) if isinstance(data['data'], dict) else 'Not a dict'}")
        
        if isinstance(data['data'], dict):
            for key, value in data['data'].items():
                print(f"\n  '{key}':")
                print(f"    type: {type(value)}")
                if isinstance(value, dict):
                    print(f"    keys: {list(value.keys())}")
                    
                    # Show first few values
                    for k, v in list(value.items())[:5]:
                        print(f"      {k}: {v}")

if __name__ == "__main__":
    debug_api_response()