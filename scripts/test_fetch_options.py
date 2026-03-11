import sys
import os
import datetime

# Add the root directory to the path so we can import core modules
ROOT = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, ROOT)

from core.api.upstox_client import UpstoxClient
from core.auth.credentials import credentials

access_token = credentials.get('access_token')
if not access_token:
    print("No access token")
    sys.exit(1)

client = UpstoxClient(access_token)

# Try fetching a known symbol to see if the key works
symbol = "NSE_FO|64850"
try:
    print(f"Fetching {symbol}")
    candles = client.fetch_historical_candles_v3(
        instrument_key=symbol,
        unit='minutes',
        interval=1,
        to_date='2026-02-24',
        from_date='2026-02-16'
    )
    if candles:
        print(f"Success! Found {len(candles)} candles")
        print(candles[0])
    else:
        print("Empty results")
except Exception as e:
    print(f"Error: {e}")
