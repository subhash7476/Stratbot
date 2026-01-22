# scripts/fetch_todays_data.py
"""
Fetch Today's Intraday Data - WORKING VERSION
Handles NSE_EQ|ISIN (request) vs NSE_EQ:SYMBOL (response) key mismatch
"""

import sys
import os
from pathlib import Path
from datetime import datetime, date, time as dt_time
import pandas as pd
import time as time_module
import requests
import json
from typing import List, Dict

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.config import get_access_token
from core.database import TradingDB

LIVE_CACHE_TABLE = "live_ohlcv_cache"

# =====================================================================
# FIXED API CLASS
# =====================================================================

class UpstoxMarketData:
    """
    Upstox Market Data API v2 - FIXED
    Handles key mismatch: NSE_EQ|INE... (request) vs NSE_EQ:SYMBOL (response)
    """
    
    BASE_URL = "https://api.upstox.com/v2"
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }
    
    def get_market_quote(self, instrument_keys: List[str], symbol_map: Dict[str, str]) -> Dict:
        """
        Get market quotes
        
        Args:
            instrument_keys: List of NSE_EQ|INE... keys
            symbol_map: Dict mapping instrument_key -> trading_symbol
        
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
                raise Exception(f"API Error: {data.get('message', 'Unknown')}")
            
            # Map response keys back to instrument keys using symbols
            return self._map_response(data.get('data', {}), instrument_keys, symbol_map)
            
        except Exception as e:
            raise Exception(f"Request failed: {e}")
    
    def _map_response(self, response_data: Dict, inst_keys: List[str], symbol_map: Dict[str, str]) -> Dict:
        """Map response (NSE_EQ:SYMBOL) back to instrument keys (NSE_EQ|ISIN)"""
        quotes = {}
        
        # Build reverse lookup: symbol -> inst_key
        symbol_to_key = {v: k for k, v in symbol_map.items()}
        
        for response_key, quote_data in response_data.items():
            if not isinstance(quote_data, dict):
                continue
            
            # Extract symbol from quote
            symbol = quote_data.get('symbol')
            
            if symbol and symbol in symbol_to_key:
                inst_key = symbol_to_key[symbol]
                quotes[inst_key] = quote_data
        
        return quotes
    
    def build_1min_candle(self, quote_data: Dict) -> Dict:
        """Convert quote to candle"""
        ohlc = quote_data.get('ohlc', {})
        now = datetime.now()
        timestamp = now.replace(second=0, microsecond=0)
        
        return {
            'timestamp': timestamp,
            'open': float(ohlc.get('open', 0)),
            'high': float(ohlc.get('high', 0)),
            'low': float(ohlc.get('low', 0)),
            'close': float(quote_data.get('last_price', ohlc.get('close', 0))),
            'volume': int(quote_data.get('volume', 0))
        }

# =====================================================================
# DATABASE FUNCTIONS
# =====================================================================

def init_live_cache_table(db: TradingDB):
    """Initialize live cache table"""
    db.con.execute(f"""
        CREATE TABLE IF NOT EXISTS {LIVE_CACHE_TABLE} (
            symbol VARCHAR,
            timestamp TIMESTAMP,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            PRIMARY KEY (symbol, timestamp)
        )
    """)
    print(f"✓ Initialized {LIVE_CACHE_TABLE} table")

def is_market_open() -> bool:
    """Check if market is open"""
    now = datetime.now()
    
    if now.weekday() > 4:
        return False
    
    market_start = dt_time(9, 15)
    market_end = dt_time(15, 30)
    current_time = now.time()
    
    return market_start <= current_time <= market_end

def get_fo_symbols(db: TradingDB) -> list:
    """Get all F&O symbols"""
    query = """
        SELECT DISTINCT i1.trading_symbol
        FROM instruments i1
        WHERE i1.segment = 'NSE_EQ'
          AND EXISTS (
              SELECT 1 FROM instruments i2 
              WHERE i2.name = i1.name 
              AND i2.segment = 'NSE_FO'
          )
        ORDER BY i1.trading_symbol
    """
    
    try:
        result = db.con.execute(query).fetchall()
        return [row[0] for row in result]
    except Exception as e:
        print(f"Error fetching F&O symbols: {e}")
        return []

def get_shortlisted_symbols() -> list:
    """Get symbols from daily analyzer results"""
    results_file = Path("data/daily_analyzer_results.csv")
    
    if not results_file.exists():
        return []
    
    try:
        df = pd.read_csv(results_file)
        if 'Symbol' in df.columns:
            return df['Symbol'].tolist()
    except Exception as e:
        print(f"Warning: Could not load shortlisted symbols: {e}")
    
    return []

def get_instrument_mappings(symbols: list, db: TradingDB) -> tuple:
    """
    Get instrument keys and symbol mappings
    
    Returns:
        (symbol_to_key dict, key_to_symbol dict)
    """
    symbol_to_key = {}
    
    for symbol in symbols:
        query = f"""
            SELECT instrument_key
            FROM instruments
            WHERE trading_symbol = '{symbol}'
              AND segment = 'NSE_EQ'
            LIMIT 1
        """
        
        try:
            result = db.con.execute(query).fetchone()
            if result:
                symbol_to_key[symbol] = result[0]
        except:
            continue
    
    key_to_symbol = {v: k for k, v in symbol_to_key.items()}
    
    return symbol_to_key, key_to_symbol

def save_candle_to_cache(symbol: str, candle: dict, db: TradingDB) -> bool:
    """Save candle to cache"""
    try:
        db.con.execute(f"""
            INSERT OR REPLACE INTO {LIVE_CACHE_TABLE}
            (symbol, timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            symbol,
            candle['timestamp'],
            candle['open'],
            candle['high'],
            candle['low'],
            candle['close'],
            candle['volume']
        ])
        return True
    except Exception as e:
        print(f"Error saving {symbol}: {e}")
        return False

def fetch_and_cache_live_data(symbols: list, access_token: str, db: TradingDB) -> dict:
    """Fetch and cache live data"""
    api = UpstoxMarketData(access_token)
    
    # Get mappings
    print(f"\nMapping {len(symbols)} symbols to instrument keys...")
    symbol_to_key, key_to_symbol = get_instrument_mappings(symbols, db)
    
    if not symbol_to_key:
        print("❌ No valid instrument keys found")
        return {'success': 0, 'errors': len(symbols)}
    
    print(f"✓ Mapped {len(symbol_to_key)} symbols")
    
    # Fetch in batches
    batch_size = 50
    inst_keys_list = list(symbol_to_key.values())
    
    success_count = 0
    error_count = 0
    
    print("\nFetching market data...")
    
    for i in range(0, len(inst_keys_list), batch_size):
        batch_keys = inst_keys_list[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(inst_keys_list) + batch_size - 1) // batch_size
        
        print(f"  Batch {batch_num}/{total_batches}: {len(batch_keys)} instruments...")
        
        try:
            # Create symbol map for this batch
            batch_symbol_map = {k: key_to_symbol[k] for k in batch_keys}
            
            # Fetch quotes
            quotes = api.get_market_quote(batch_keys, batch_symbol_map)
            
            # Process quotes
            for inst_key, quote in quotes.items():
                symbol = key_to_symbol[inst_key]
                candle = api.build_1min_candle(quote)
                
                if save_candle_to_cache(symbol, candle, db):
                    success_count += 1
                    print(f"    ✓ {symbol:12s}: ₹{candle['close']:8.2f} (Vol: {candle['volume']:,})")
                else:
                    error_count += 1
            
            # Rate limiting
            if i + batch_size < len(inst_keys_list):
                time_module.sleep(1)
        
        except Exception as e:
            print(f"    ❌ Batch error: {e}")
            error_count += len(batch_keys)
    
    return {
        'success': success_count,
        'errors': error_count,
        'total': len(symbols)
    }

def get_cache_stats(db: TradingDB) -> dict:
    """Get cache stats"""
    today = date.today()
    
    query = f"""
        SELECT 
            COUNT(DISTINCT symbol) as symbol_count,
            COUNT(*) as candle_count,
            MIN(timestamp) as first_update,
            MAX(timestamp) as last_update
        FROM {LIVE_CACHE_TABLE}
        WHERE DATE(timestamp) = '{today}'
    """
    
    try:
        result = db.con.execute(query).fetchone()
        return {
            'symbols': result[0] if result else 0,
            'candles': result[1] if result else 0,
            'first_update': result[2] if result else None,
            'last_update': result[3] if result else None
        }
    except Exception as e:
        print(f"Error getting stats: {e}")
        return {'symbols': 0, 'candles': 0}

def clear_old_cache(db: TradingDB, days_to_keep: int = 2):
    """Clear old cache"""
    try:
        count_query = f"""
            SELECT COUNT(*) FROM {LIVE_CACHE_TABLE}
            WHERE timestamp < NOW() - INTERVAL '{days_to_keep} days'
        """
        
        deleted = db.con.execute(count_query).fetchone()[0]
        
        if deleted > 0:
            delete_query = f"""
                DELETE FROM {LIVE_CACHE_TABLE}
                WHERE timestamp < NOW() - INTERVAL '{days_to_keep} days'
            """
            db.con.execute(delete_query)
            print(f"✓ Cleared {deleted} old records")
    except Exception as e:
        print(f"Error clearing cache: {e}")

# =====================================================================
# MAIN
# =====================================================================

def main():
    """Main function"""
    print("=" * 70)
    print("📊 FETCH TODAY'S INTRADAY DATA")
    print("=" * 70)
    
    db = TradingDB()
    print(f"✓ Connected to database: {db.db_path}")
    
    init_live_cache_table(db)
    
    if not is_market_open():
        print("\n⚠️  MARKET IS CLOSED")
        print("Market hours: 9:15 AM - 3:30 PM (Mon-Fri)")
        
        stats = get_cache_stats(db)
        if stats['symbols'] > 0:
            print(f"\n📊 Cached Data:")
            print(f"  Symbols: {stats['symbols']}")
            print(f"  Candles: {stats['candles']}")
            if stats['last_update']:
                print(f"  Last Update: {stats['last_update']}")
        return
    
    print("✓ Market is OPEN")
    
    token = get_access_token()
    if not token:
        print("\n❌ NO ACCESS TOKEN")
        print("Run Login page first")
        return
    
    print("✓ Access token loaded")
    
    print("\n" + "=" * 70)
    print("📋 SYMBOL SELECTION")
    print("=" * 70)
    
    shortlisted = get_shortlisted_symbols()
    
    if shortlisted:
        print(f"✓ Found {len(shortlisted)} shortlisted symbols:")
        for symbol in shortlisted:
            print(f"  - {symbol}")
        
        choice = input("\nFetch:\n  1. Only shortlisted\n  2. All F&O\nChoice [1/2]: ").strip()
        
        if choice == "2":
            symbols = get_fo_symbols(db)
            print(f"\n✓ Fetching all {len(symbols)} F&O stocks")
        else:
            symbols = shortlisted
            print(f"\n✓ Fetching {len(symbols)} shortlisted stocks")
    else:
        print("⚠️  No shortlisted symbols")
        print("Fetching all F&O stocks...")
        symbols = get_fo_symbols(db)
        print(f"✓ Found {len(symbols)} F&O stocks")
    
    if not symbols:
        print("\n❌ No symbols to fetch")
        return
    
    print("\n" + "=" * 70)
    print("🔄 FETCHING LIVE DATA")
    print("=" * 70)
    
    stats = fetch_and_cache_live_data(symbols, token, db)
    
    print("\n" + "=" * 70)
    print("📊 RESULTS")
    print("=" * 70)
    print(f"  ✅ Success: {stats['success']:,}")
    print(f"  ❌ Errors:  {stats['errors']:,}")
    print(f"  📋 Total:   {stats['total']:,}")
    
    if stats['success'] > 0:
        rate = (stats['success'] / stats['total']) * 100
        print(f"  📈 Success Rate: {rate:.1f}%")
    
    cache_stats = get_cache_stats(db)
    print(f"\n📦 CACHE STATISTICS")
    print(f"  Symbols: {cache_stats['symbols']:,}")
    print(f"  Candles: {cache_stats['candles']:,}")
    if cache_stats['last_update']:
        print(f"  Last Update: {cache_stats['last_update']}")
    
    print("\n🧹 Cleaning old cache...")
    clear_old_cache(db, days_to_keep=2)
    
    print("\n" + "=" * 70)
    print("✅ DONE!")
    print("=" * 70)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted")
    except Exception as e:
        print(f"\n\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()