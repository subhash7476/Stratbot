"""
Multi-Stock Scanner - Core ₹500 Scalping Engine
Scans 20 stocks in parallel, runs all 5 filters, outputs "₹500 PROBABLE" signals

Architecture:
1. Load stock universe from database
2. For each stock, run filters in sequence (fail-fast)
3. Log all scan results
4. Return only stocks passing ALL filters
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Any
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import uuid
import sys, os
from pathlib import Path


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


from core.filters.filter_1_index_regime import IndexRegimeFilter
from core.filters.filter_2_stock_eligibility import StockEligibilityFilter
from core.filters.filter_3_impulse_detector import ImpulseDetector
from core.filters.filter_4_option_response import OptionResponseFilter
from core.filters.filter_5_feasibility import FeasibilityFilter


class MultiStockScanner:
    """
    Parallel scanner for 20-stock universe
    Returns only "₹500 PROBABLE - EXECUTE" signals
    """
    
    def __init__(self, db_path: str = "data/trading_bot.db"):
        self.db_path = db_path
        
        # Initialize all 5 filters
        self.filters = {
            1: IndexRegimeFilter(),
            2: StockEligibilityFilter(),
            3: ImpulseDetector(),
            4: OptionResponseFilter(),
            5: FeasibilityFilter()
        }
        
        # Load stock universe
        self.stock_universe = self._load_universe()
        
        # Scan statistics
        self.stats = {
            'total_scans': 0,
            'signals_generated': 0,
            'last_scan_time': None,
            'last_scan_duration_ms': 0
        }
    
    def _load_universe(self) -> List[str]:
        """
        Load active stock universe from database
        
        Returns:
            List of symbols to scan
        """
        try:
            import duckdb
            conn = duckdb.connect(self.db_path)
            
            result = conn.execute("""
                SELECT symbol 
                FROM stock_universe 
                WHERE enabled = TRUE 
                ORDER BY tier ASC
            """).fetchall()
            
            conn.close()
            
            symbols = [row[0] for row in result]
            print(f"📊 Loaded {len(symbols)} stocks from universe")
            return symbols
            
        except Exception as e:
            print(f"⚠️ Error loading universe: {e}")
            # Fallback to default 20 stocks
            return [
                "PNB", "SBIN", "ICICIBANK", "HDFCBANK", "AXISBANK",
                "TATASTEEL", "TATAMOTORS", "JSWSTEEL", "HINDALCO",
                "INFY", "WIPRO", "TCS", "COALINDIA", "ONGC",
                "RELIANCE", "BHARTIARTL", "ITC", "SUNPHARMA",
                "M&M", "LT"
            ]
    
    def scan_single_stock(self, symbol: str, market_data: Dict) -> Dict:
        """
        Run all 5 filters on a single stock (fail-fast)
        
        Args:
            symbol: Stock symbol
            market_data: Dictionary containing all market data
        
        Returns:
            {
                'symbol': str,
                'passed_all': bool,
                'failed_at': int (filter number) or None,
                'filter_results': dict,
                'signal_data': dict (if passed all)
            }
        """
        scan_start = time.time()
        
        filter_results = {}
        
        # Prepare data for filters
        stock_data = market_data.get('stocks', {}).get(symbol, {})
        index_data = market_data.get('index', {})
        
        # Filter 1: Index Regime
        result_1 = self.filters[1].check(symbol, {
            'index_df': index_data.get('df'),
            'index_name': index_data.get('name', 'NIFTY')
        })
        filter_results[1] = result_1
        
        if not result_1['passed']:
            return {
                'symbol': symbol,
                'passed_all': False,
                'failed_at': 1,
                'filter_results': filter_results,
                'scan_time_ms': (time.time() - scan_start) * 1000
            }
        
        # Filter 2: Stock Eligibility
        result_2 = self.filters[2].check(symbol, {
            'df': stock_data.get('df'),
            'lot_size': stock_data.get('lot_size'),
            'spot_price': stock_data.get('spot_price')
        })
        filter_results[2] = result_2
        
        if not result_2['passed']:
            return {
                'symbol': symbol,
                'passed_all': False,
                'failed_at': 2,
                'filter_results': filter_results,
                'scan_time_ms': (time.time() - scan_start) * 1000
            }
        
        # Filter 3: Impulse Detector
        result_3 = self.filters[3].check(symbol, {
            'df': stock_data.get('df'),
            'vwap': stock_data.get('vwap'),
            'opening_high': stock_data.get('opening_high'),
            'opening_low': stock_data.get('opening_low')
        })
        filter_results[3] = result_3
        
        if not result_3['passed']:
            return {
                'symbol': symbol,
                'passed_all': False,
                'failed_at': 3,
                'filter_results': filter_results,
                'scan_time_ms': (time.time() - scan_start) * 1000
            }
        
        # Filter 4: Option Response
        option_data = stock_data.get('option', {})
        result_4 = self.filters[4].check(symbol, {
            'option_ltp': option_data.get('ltp'),
            'option_ltp_prev': option_data.get('ltp_prev'),
            'option_bid': option_data.get('bid'),
            'option_bid_prev': option_data.get('bid_prev'),
            'option_ask': option_data.get('ask'),
            'tick_size': option_data.get('tick_size', 0.05),
            'underlying_move_pct': result_3['metrics'].get('move_pct', 0)
        })
        filter_results[4] = result_4
        
        if not result_4['passed']:
            return {
                'symbol': symbol,
                'passed_all': False,
                'failed_at': 4,
                'filter_results': filter_results,
                'scan_time_ms': (time.time() - scan_start) * 1000
            }
        
        # Filter 5: Feasibility
        result_5 = self.filters[5].check(symbol, {
            'spot_price': stock_data.get('spot_price'),
            'stock_atr': stock_data.get('atr'),
            'option_premium': option_data.get('ltp'),
            'option_atr': option_data.get('atr'),
            'lot_size': stock_data.get('lot_size')
        })
        filter_results[5] = result_5
        
        if not result_5['passed']:
            return {
                'symbol': symbol,
                'passed_all': False,
                'failed_at': 5,
                'filter_results': filter_results,
                'scan_time_ms': (time.time() - scan_start) * 1000
            }
        
        # ALL FILTERS PASSED - Generate signal
        signal_data = self._generate_signal(symbol, stock_data, option_data, filter_results)
        
        return {
            'symbol': symbol,
            'passed_all': True,
            'failed_at': None,
            'filter_results': filter_results,
            'signal_data': signal_data,
            'scan_time_ms': (time.time() - scan_start) * 1000
        }
    
    def _generate_signal(self, symbol: str, stock_data: Dict, 
                         option_data: Dict, filter_results: Dict) -> Dict:
        """
        Generate actionable trade signal from filter results
        
        Returns:
            Complete signal with entry, SL, targets
        """
        spot_price = stock_data['spot_price']
        option_ltp = option_data['ltp']
        lot_size = stock_data['lot_size']
        
        # Calculate entry
        entry_premium = option_ltp
        
        # Calculate SL (−4% from entry)
        sl_premium = entry_premium * 0.96
        
        # Calculate targets
        target1_premium = entry_premium * 1.05  # +5%
        target2_premium = entry_premium * 1.08  # +8%
        
        # Calculate expected profit
        target1_profit = (target1_premium - entry_premium) * lot_size
        target2_profit = (target2_premium - entry_premium) * lot_size
        
        # Select strike (ATM from option_data)
        strike = option_data.get('strike', int(round(spot_price / 10) * 10))
        
        return {
            'signal_id': str(uuid.uuid4()),
            'symbol': symbol,
            'timestamp': datetime.now(),
            'entry_type': 'MARKET',
            'entry_candle_close': True,
            'strike': strike,
            'option_type': 'CE',  # Assume CE for now
            'entry_premium': round(entry_premium, 2),
            'lot_size': lot_size,
            'capital_required': round(entry_premium * lot_size, 0),
            'sl_premium': round(sl_premium, 2),
            'target1_premium': round(target1_premium, 2),
            'target2_premium': round(target2_premium, 2),
            'target1_profit': round(target1_profit, 0),
            'target2_profit': round(target2_profit, 0),
            'time_sl_seconds': 90,
            'expected_profit': round(filter_results[5]['metrics']['expected_profit'], 0),
            'signal_strength': 1.0  # All filters passed = max strength
        }
    
    def scan_all_stocks_parallel(self, market_data: Dict, max_workers: int = 10) -> List[Dict]:
        """
        Scan all stocks in parallel
        
        Args:
            market_data: Complete market data for all stocks
            max_workers: Max parallel threads
        
        Returns:
            List of signals (only stocks passing all filters)
        """
        scan_start = time.time()
        
        signals = []
        scan_results = []
        
        # Parallel scanning
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.scan_single_stock, symbol, market_data): symbol 
                for symbol in self.stock_universe
            }
            
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    result = future.result()
                    scan_results.append(result)
                    
                    if result['passed_all']:
                        signals.append(result['signal_data'])
                        print(f"🚀 SIGNAL: {symbol} - {result['signal_data']['expected_profit']}")
                    
                except Exception as e:
                    print(f"⚠️ Error scanning {symbol}: {e}")
        
        # Update statistics
        scan_duration = (time.time() - scan_start) * 1000
        self.stats['total_scans'] += len(self.stock_universe)
        self.stats['signals_generated'] += len(signals)
        self.stats['last_scan_time'] = datetime.now()
        self.stats['last_scan_duration_ms'] = scan_duration
        
        # Log filter performance
        self._log_filter_performance(scan_results)
        
        print(f"\n✅ Scan complete: {len(signals)} signals in {scan_duration:.0f}ms")
        
        return signals
    
    def _log_filter_performance(self, scan_results: List[Dict]):
        """Log which filters are blocking most stocks"""
        failed_at = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        
        for result in scan_results:
            if not result['passed_all'] and result['failed_at']:
                failed_at[result['failed_at']] += 1
        
        print("\n📊 Filter Performance:")
        for filter_num, count in failed_at.items():
            filter_name = self.filters[filter_num].name
            print(f"  Filter {filter_num} ({filter_name}): Blocked {count} stocks")
    
    def get_stats(self) -> Dict:
        """Get scanner statistics"""
        return {
            **self.stats,
            'universe_size': len(self.stock_universe),
            'filter_stats': {i: f.get_stats() for i, f in self.filters.items()}
        }
    
    def reset_daily_stats(self):
        """Reset statistics at end of day"""
        self.stats = {
            'total_scans': 0,
            'signals_generated': 0,
            'last_scan_time': None,
            'last_scan_duration_ms': 0
        }
        
        for filter in self.filters.values():
            filter.reset_stats()
        
        print("✅ Daily stats reset")

if __name__ == "__main__":
    # Test the scanner
    print("🧪 Testing Multi-Stock Scanner...")
    
    scanner = MultiStockScanner()
    
    # Create mock market data
    mock_market_data = {
        'index': {
            'name': 'NIFTY',
            'df': pd.DataFrame({
                'Open': [22000] * 30,
                'High': [22050] * 30,
                'Low': [21950] * 30,
                'Close': [22020] * 30,
                'ATR': [50] * 30
            })
        },
        'stocks': {}
    }
    
    # Add mock stock data
    for symbol in scanner.stock_universe[:3]:  # Test first 3
        mock_market_data['stocks'][symbol] = {
            'spot_price': 120.0,
            'lot_size': 5000,
            'atr': 0.5,
            'df': pd.DataFrame({
                'Open': [119] * 20,
                'High': [121] * 20,
                'Low': [118] * 20,
                'Close': [120.5] * 20,
                'Volume': [100000] * 20,
                'ATR': [0.5] * 20
            }),
            'vwap': 119.5,
            'opening_high': 120.0,
            'opening_low': 119.0,
            'option': {
                'strike': 120,
                'ltp': 0.56,
                'ltp_prev': 0.54,
                'bid': 0.55,
                'bid_prev': 0.53,
                'ask': 0.57,
                'atr': 0.08,
                'tick_size': 0.05
            }
        }
    
    # Run scan
    signals = scanner.scan_all_stocks_parallel(mock_market_data)
    
    print(f"\nGenerated {len(signals)} signals")
    print(f"Scanner stats: {scanner.get_stats()}") 
