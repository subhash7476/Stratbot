# debug_historical_api.py
"""
Debug Upstox Historical Candle API
"""

import sys
from pathlib import Path
from datetime import datetime, date, time as dt_time
import requests
import json

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.config import get_access_token
from core.database import TradingDB

def test_historical_api():
    """Test the historical candle API with RELIANCE"""
    
    print("=" * 70)
    print("🔍 UPSTOX HISTORICAL API DEBUG")
    print("=" * 70)
    
    token = get_access_token()
    print(f"\n✓ Token: {token[:20]}...")
    
    db = TradingDB()
    
    # Get RELIANCE instrument key
    query = """
        SELECT instrument_key, trading_symbol
        FROM instruments
        WHERE trading_symbol = 'RELIANCE'
          AND segment = 'NSE_EQ'
        LIMIT 1
    """
    
    result = db.con.execute(query).fetchone()
    inst_key, symbol = result
    
    print(f"✓ Testing with: {symbol}")
    print(f"✓ Instrument key: {inst_key}")
    
    # Try different API formats
    today = date.today()
    
    print("\n" + "=" * 70)
    print("TEST 1: Historical Candle API")
    print("=" * 70)
    
    # Format 1: /v2/historical-candle/{instrument_key}/{interval}/{to_date}
    # No from_date parameter (gets today's data)
    
    to_date = today.strftime('%Y-%m-%d')
    interval = '1minute'
    
    url = f"https://api.upstox.com/v2/historical-candle/{inst_key}/{interval}/{to_date}"
    
    print(f"\nURL: {url}")
    
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        
        print(f"\nStatus Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            print("\n📋 FULL RESPONSE:")
            print(json.dumps(data, indent=2)[:2000])  # First 2000 chars
            
            if data.get('status') == 'success':
                candles = data.get('data', {}).get('candles', [])
                
                print(f"\n✅ Success!")
                print(f"   Candles received: {len(candles)}")
                
                if candles:
                    print("\n📊 First 3 candles:")
                    for i, candle in enumerate(candles[:3]):
                        print(f"   {i+1}. {candle}")
                    
                    print("\n📊 Last 3 candles:")
                    for i, candle in enumerate(candles[-3:]):
                        print(f"   {i+1}. {candle}")
                else:
                    print("\n⚠️  Response has no candles!")
            else:
                print(f"\n❌ API Error: {data.get('message')}")
        else:
            print(f"\n❌ HTTP Error")
            print(f"   Response: {response.text[:500]}")
    
    except Exception as e:
        print(f"\n❌ Exception: {e}")
    
    # Test 2: Try with from_date
    print("\n" + "=" * 70)
    print("TEST 2: With from_date parameter")
    print("=" * 70)
    
    from_date = today.strftime('%Y-%m-%d')
    to_date = today.strftime('%Y-%m-%d')
    
    url = f"https://api.upstox.com/v2/historical-candle/{inst_key}/{interval}/{to_date}/{from_date}"
    
    print(f"\nURL: {url}")
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        
        print(f"\nStatus Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            print("\n📋 RESPONSE STRUCTURE:")
            print(f"   Keys: {list(data.keys())}")
            
            if 'data' in data:
                print(f"   data keys: {list(data['data'].keys()) if isinstance(data['data'], dict) else type(data['data'])}")
            
            if data.get('status') == 'success':
                candles = data.get('data', {}).get('candles', [])
                
                print(f"\n✅ Success!")
                print(f"   Candles received: {len(candles)}")
                
                if candles:
                    print("\n📊 Sample candle structure:")
                    print(f"   {candles[0]}")
                    print("\n   Format: [timestamp, open, high, low, close, volume, oi]")
            else:
                print(f"\n❌ API Error: {data.get('message')}")
                print(f"\n   Full response: {json.dumps(data, indent=2)}")
        else:
            print(f"\n❌ HTTP Error")
            print(f"   Response: {response.text[:500]}")
    
    except Exception as e:
        print(f"\n❌ Exception: {e}")
    
    # Test 3: Check if intraday endpoint exists
    print("\n" + "=" * 70)
    print("TEST 3: Check intraday endpoint")
    print("=" * 70)
    
    # Try the intraday endpoint
    url = f"https://api.upstox.com/v2/historical-candle/intraday/{inst_key}/{interval}"
    
    print(f"\nURL: {url}")
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        
        print(f"\nStatus Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get('status') == 'success':
                candles = data.get('data', {}).get('candles', [])
                
                print(f"\n✅ Success!")
                print(f"   Candles received: {len(candles)}")
                
                if candles:
                    print("\n📊 Sample:")
                    print(f"   {candles[0]}")
            else:
                print(f"\n❌ API Error: {data.get('message')}")
        else:
            print(f"\n❌ HTTP Error: {response.status_code}")
            print(f"   Response: {response.text[:300]}")
    
    except Exception as e:
        print(f"\n❌ Exception: {e}")

if __name__ == "__main__":
    test_historical_api()