# core/option_analyzer.py
"""
Option Chain Analysis & Strike Selection Engine

Features:
1. Fetch option chain from Upstox (V2 API - Working implementation)
2. Filter strikes based on Greeks (Delta, IV, Theta)
3. Score and rank strikes
4. Calculate optimal TP/SL based on premium and Greeks
5. Position sizing with lot size consideration
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
import requests
from datetime import datetime
import os
from pathlib import Path


# ========== INSTRUMENT KEY MAPPING ==========
INSTRUMENT_KEY_MAP = {
    # Indices
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|Nifty Midcap Select",
}


def get_expiries_for_underlying(symbol: str, segment: str = "NSE_FO") -> List[str]:
    """
    Get available expiries for a symbol (from instruments file or API)
    Based on upstox_hybrid_wrapper.py implementation
    
    Args:
        symbol: Underlying symbol (e.g., 'TCS', 'NIFTY')
        segment: 'NSE_FO' or 'NSE_INDEX'
    
    Returns:
        List of expiry dates as strings (YYYY-MM-DD)
    """
    
    # Try to load from instruments file (fast & offline)
    try:
        # Import here to avoid circular dependency
        try:
            from core.api.instruments import load_segment_instruments
        except:
            # Fallback path
            import sys
            ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            if ROOT not in sys.path:
                sys.path.insert(0, ROOT)
            from core.api.instruments import load_segment_instruments
        
        df = load_segment_instruments(segment)
        
        if df is not None and not df.empty and 'expiry' in df.columns:
            # For NIFTY, trading_symbol is like "NIFTY 25 JAN 23000 CE"
            # Use strict match with space to avoid "NIFTYIT" matching "NIFTY"
            mask = df['trading_symbol'].astype(str).str.startswith(f"{symbol} ")
            
            filtered = df[mask]
            
            if not filtered.empty:
                exps = filtered['expiry'].dropna().unique().tolist()
                
                # Normalize to strings (handle timestamps)
                readable_dates = set()
                for exp in exps:
                    try:
                        # Handle milliseconds vs seconds timestamp
                        if isinstance(exp, (int, float)):
                            exp_val = float(exp)
                            if exp_val > 100000000000:  # Milliseconds
                                exp_val = exp_val / 1000
                            date_str = datetime.fromtimestamp(exp_val).strftime('%Y-%m-%d')
                        else:
                            # String format
                            date_str = str(exp).split(" ")[0]
                        readable_dates.add(date_str)
                    except:
                        continue
                
                # Sort and filter future dates
                sorted_dates = sorted(list(readable_dates))
                today = datetime.now().strftime("%Y-%m-%d")
                future_dates = [d for d in sorted_dates if d >= today]
                
                if future_dates:
                    return future_dates
    except Exception as e:
        print(f"Could not load expiries from instruments file: {e}")
    
    # Fallback: calculate next Thursday
    from datetime import timedelta
    today = datetime.now()
    days_ahead = 3 - today.weekday()  # Thursday = 3
    if days_ahead <= 0:
        days_ahead += 7
    next_thursday = today + timedelta(days=days_ahead)
    
    return [next_thursday.strftime("%Y-%m-%d")]


def get_instrument_key(symbol: str, segment: str = "NSE_FO") -> Optional[str]:
    """
    Get instrument key for a symbol
    
    Args:
        symbol: Stock/Index symbol (e.g., 'TCS', 'NIFTY')
        segment: 'NSE_FO', 'NSE_EQ', 'NSE_INDEX'
    
    Returns:
        Instrument key string (e.g., 'NSE_FO|TCS')
    """
    
    # Check if it's an index
    if symbol.upper() in INSTRUMENT_KEY_MAP:
        return INSTRUMENT_KEY_MAP[symbol.upper()]
    
    # For stocks, construct the key
    if segment == "NSE_FO":
        return f"NSE_FO|{symbol.upper()}"
    elif segment == "NSE_EQ":
        return f"NSE_EQ|{symbol.upper()}"
    
    return None


def get_nearest_expiry(symbol: str = "NIFTY") -> str:
    """
    Get nearest expiry date
    For now returns a default, but should be enhanced to fetch from instruments
    
    Returns:
        Expiry date in YYYY-MM-DD format
    """
    # TODO: Implement proper expiry fetching from instruments file
    # For now, return next Thursday (common for indices)
    from datetime import timedelta
    
    today = datetime.now()
    # Find next Thursday
    days_ahead = 3 - today.weekday()  # Thursday = 3
    if days_ahead <= 0:
        days_ahead += 7
    next_thursday = today + timedelta(days=days_ahead)
    
    return next_thursday.strftime("%Y-%m-%d")


class OptionAnalyzer:
    """
    Analyzes option chain and selects optimal strike for trading
    Uses working Upstox V2 API implementation
    """
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://api.upstox.com/v2"
    
    def fetch_option_chain(
        self, 
        symbol: str, 
        expiry: str,  # Now always a real date (YYYY-MM-DD)
        segment: str = "NSE_FO"
    ) -> pd.DataFrame:
        """
        Fetch option chain from Upstox using WORKING implementation
        
        Args:
            symbol: Underlying symbol (e.g., 'TCS', 'NIFTY')
            expiry: Expiry date 'YYYY-MM-DD' (already resolved)
            segment: 'NSE_FO' or 'NSE_INDEX'
        
        Returns:
            DataFrame with strikes, CE/PE data, Greeks
        """
        
        # Get instrument key
        inst_key = get_instrument_key(symbol, segment)
        
        if not inst_key:
            print(f"Could not resolve instrument key for {symbol}")
            return pd.DataFrame()
        
        expiry_date = expiry  # Already resolved by caller
        
        print(f"API Call: {symbol} | Expiry: {expiry_date} | Key: {inst_key}")
        
        # API endpoint (from working code)
        url = f"{self.base_url}/option/chain"
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Accept': 'application/json'
        }
        
        params = {
            'instrument_key': inst_key,
            'expiry_date': expiry_date
        }
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            
            if response.status_code != 200:
                print(f"API Error: {response.status_code}")
                print(f"Response: {response.text}")
                return pd.DataFrame()
            
            data = response.json()
            
            # Extract chain data (from working code structure)
            chain_data = data.get('data', [])
            
            if not chain_data:
                print("No option chain data returned")
                print(f"Full response: {data}")
                return pd.DataFrame()
            
            # Parse into DataFrame
            rows = []
            
            for contract in chain_data:
                strike = contract.get('strike_price', 0)
                
                # Call options (CE)
                if 'call_options' in contract and contract['call_options']:
                    ce_data = contract['call_options'].get('market_data', {})
                    
                    rows.append({
                        'strike': strike,
                        'option_type': 'CE',
                        'premium': ce_data.get('ltp', 0),
                        'delta': ce_data.get('delta', 0.5),  # Default if not available
                        'gamma': ce_data.get('gamma', 0),
                        'theta': ce_data.get('theta', 0),
                        'vega': ce_data.get('vega', 0),
                        'iv': ce_data.get('iv', 0.20),  # Default IV 20%
                        'oi': ce_data.get('oi', 0),
                        'volume': ce_data.get('volume', 0),
                        'bid': ce_data.get('bid', 0),
                        'ask': ce_data.get('ask', 0)
                    })
                
                # Put options (PE)
                if 'put_options' in contract and contract['put_options']:
                    pe_data = contract['put_options'].get('market_data', {})
                    
                    rows.append({
                        'strike': strike,
                        'option_type': 'PE',
                        'premium': pe_data.get('ltp', 0),
                        'delta': pe_data.get('delta', -0.5),  # Default if not available
                        'gamma': pe_data.get('gamma', 0),
                        'theta': pe_data.get('theta', 0),
                        'vega': pe_data.get('vega', 0),
                        'iv': pe_data.get('iv', 0.20),
                        'oi': pe_data.get('oi', 0),
                        'volume': pe_data.get('volume', 0),
                        'bid': pe_data.get('bid', 0),
                        'ask': pe_data.get('ask', 0)
                    })
            
            df = pd.DataFrame(rows)
            
            print(f"✅ Fetched {len(df)} option contracts for {symbol} expiry {expiry_date}")
            
            return df
        
        except Exception as e:
            print(f"Error fetching option chain: {e}")
            import traceback
            traceback.print_exc()
            return pd.DataFrame()
    
    def filter_strikes(
        self,
        option_chain: pd.DataFrame,
        spot_price: float,
        direction: str = 'CE',  # 'CE' for calls, 'PE' for puts
        min_delta: float = 0.40,
        max_delta: float = 0.80,
        max_iv: float = 0.30,
        min_oi: int = 500
    ) -> pd.DataFrame:
        """
        Filter option strikes based on criteria
        
        Args:
            option_chain: Full option chain data
            spot_price: Current underlying price
            direction: 'CE' or 'PE'
            min_delta: Minimum delta (0.40 = 40%)
            max_delta: Maximum delta (0.80 = 80%)
            max_iv: Maximum implied volatility
            min_oi: Minimum open interest (liquidity)
        
        Returns:
            Filtered DataFrame with viable strikes
        """
        
        if option_chain.empty:
            return pd.DataFrame()
        
        # Filter by direction (CE or PE)
        chain = option_chain[option_chain['option_type'] == direction].copy()
        
        # Filter criteria
        filtered = chain[
            (chain['delta'] >= min_delta) &
            (chain['delta'] <= max_delta) &
            (chain['iv'] <= max_iv) &
            (chain['oi'] >= min_oi) &
            (chain['premium'] > 0)  # Valid premium
        ]
        
        # Additional filters for affordability
        # Premium should be 1-5% of spot price
        min_premium = spot_price * 0.01
        max_premium = spot_price * 0.05
        
        filtered = filtered[
            (filtered['premium'] >= min_premium) &
            (filtered['premium'] <= max_premium)
        ]
        
        return filtered
    
    def score_strike(
        self,
        strike_data: pd.Series,
        spot_price: float,
        confidence: str = 'MEDIUM'  # 'HIGH', 'MEDIUM', 'LOW'
    ) -> float:
        """
        Score a strike based on multiple factors
        
        Args:
            strike_data: Single row from option chain
            spot_price: Current underlying price
            confidence: Signal confidence level
        
        Returns:
            Score (0-100)
        """
        
        score = 0.0
        
        # 1. Delta Score (40 points max)
        delta = strike_data['delta']
        
        if confidence == 'HIGH':
            # For high confidence, prefer ATM (Delta 0.45-0.60)
            if 0.50 <= delta <= 0.60:
                score += 40
            elif 0.45 <= delta < 0.50 or 0.60 < delta <= 0.65:
                score += 30
            else:
                score += 20
        
        elif confidence == 'MEDIUM':
            # For medium confidence, prefer slightly ITM (Delta 0.55-0.70)
            if 0.60 <= delta <= 0.70:
                score += 40
            elif 0.55 <= delta < 0.60:
                score += 35
            else:
                score += 25
        
        else:  # LOW confidence
            # For low confidence, prefer more ITM (Delta 0.65-0.75)
            if 0.65 <= delta <= 0.75:
                score += 40
            elif 0.60 <= delta < 0.65:
                score += 30
            else:
                score += 20
        
        # 2. IV Score (30 points max)
        # Lower IV is better for option buying
        iv = strike_data['iv']
        
        if iv < 0.15:  # Very low IV
            score += 30
        elif iv < 0.20:  # Low IV
            score += 25
        elif iv < 0.25:  # Moderate IV
            score += 15
        else:  # High IV
            score += 5
        
        # 3. Liquidity Score (20 points max)
        oi = strike_data['oi']
        
        if oi > 5000:
            score += 20
        elif oi > 2000:
            score += 15
        elif oi > 1000:
            score += 10
        elif oi > 500:
            score += 5
        
        # 4. Premium Affordability (10 points max)
        premium = strike_data['premium']
        premium_pct = (premium / spot_price) * 100
        
        if 1.5 <= premium_pct <= 3.0:  # Sweet spot
            score += 10
        elif 1.0 <= premium_pct < 1.5 or 3.0 < premium_pct <= 4.0:
            score += 7
        else:
            score += 3
        
        return score
    
    def select_best_strike(
        self,
        option_chain: pd.DataFrame,
        spot_price: float,
        direction: str = 'CE',
        confidence: str = 'MEDIUM'
    ) -> Optional[Dict]:
        """
        Select the best strike from option chain
        
        Args:
            option_chain: Full option chain
            spot_price: Current underlying price
            direction: 'CE' or 'PE'
            confidence: Signal confidence level
        
        Returns:
            Dict with selected strike details
        """
        
        # Filter strikes
        filtered = self.filter_strikes(
            option_chain,
            spot_price,
            direction=direction
        )
        
        if filtered.empty:
            return None
        
        # Score each strike
        filtered['score'] = filtered.apply(
            lambda row: self.score_strike(row, spot_price, confidence),
            axis=1
        )
        
        # Get best strike
        best = filtered.loc[filtered['score'].idxmax()]
        
        return {
            'strike': best['strike'],
            'premium': best['premium'],
            'delta': best['delta'],
            'gamma': best.get('gamma', 0),
            'theta': best.get('theta', 0),
            'vega': best.get('vega', 0),
            'iv': best['iv'],
            'oi': best['oi'],
            'score': best['score'],
            'option_type': direction
        }
    
    def calculate_tp_sl(
        self,
        entry_premium: float,
        strategy: str = 'CONSERVATIVE',  # 'CONSERVATIVE', 'BALANCED', 'AGGRESSIVE'
        holding_period: str = 'INTRADAY',  # 'INTRADAY', 'SWING'
        theta: float = 0,  # Daily theta decay
        days_to_expiry: int = 5
    ) -> Dict:
        """
        Calculate Take Profit and Stop Loss for options
        
        Args:
            entry_premium: Entry premium price
            strategy: Risk appetite
            holding_period: Intended holding period
            theta: Option theta (time decay per day)
            days_to_expiry: Days until expiry
        
        Returns:
            Dict with TP, SL, and P&L projections
        """
        
        if strategy == 'CONSERVATIVE':
            if holding_period == 'INTRADAY':
                tp_pct = 0.20  # 20% gain
                sl_pct = 0.25  # 25% loss
            else:  # SWING
                tp_pct = 0.25  # 25% gain
                sl_pct = 0.30  # 30% loss
        
        elif strategy == 'BALANCED':
            if holding_period == 'INTRADAY':
                tp_pct = 0.25  # 25% gain
                sl_pct = 0.30  # 30% loss
            else:  # SWING
                tp_pct = 0.30  # 30% gain
                sl_pct = 0.35  # 35% loss
        
        else:  # AGGRESSIVE
            tp_pct = 0.50  # 50% gain
            sl_pct = 0.40  # 40% loss
        
        # Calculate TP and SL premiums
        tp_premium = entry_premium * (1 + tp_pct)
        sl_premium = entry_premium * (1 - sl_pct)
        
        # Adjust SL for theta decay if holding overnight
        if holding_period == 'SWING' and theta != 0:
            theta_decay = abs(theta) * min(days_to_expiry, 3)  # Assume max 3-day hold
            sl_premium = max(sl_premium, entry_premium - theta_decay - (entry_premium * sl_pct))
        
        # Time-based stop
        if days_to_expiry <= 2:
            # Tighten SL near expiry
            sl_premium = entry_premium * (1 - sl_pct * 0.8)
        
        return {
            'entry_premium': round(entry_premium, 2),
            'tp_premium': round(tp_premium, 2),
            'sl_premium': round(sl_premium, 2),
            'tp_gain_pct': tp_pct * 100,
            'sl_loss_pct': sl_pct * 100,
            'risk_reward_ratio': round(tp_pct / sl_pct, 2)
        }
    
    def calculate_position_size(
        self,
        entry_premium: float,
        lot_size: int,
        account_capital: float,
        risk_pct: float = 0.01,  # 1% risk per trade
        sl_pct: float = 0.25  # 25% SL
    ) -> Dict:
        """
        Calculate optimal position size considering lot size constraints
        
        Args:
            entry_premium: Option entry price
            lot_size: Contract lot size (e.g., 175 for TCS)
            account_capital: Total trading capital
            risk_pct: Risk per trade as % of capital (default 1%)
            sl_pct: Stop loss percentage (default 25%)
        
        Returns:
            Dict with quantity, investment, risk metrics
        """
        
        # Maximum risk amount
        max_risk = account_capital * risk_pct
        
        # Risk per lot
        risk_per_lot = entry_premium * lot_size * sl_pct
        
        # Calculate max lots
        max_lots = int(max_risk / risk_per_lot)
        
        if max_lots < 1:
            max_lots = 1  # Minimum 1 lot
        
        # Calculate actual investment
        lots_to_trade = max_lots
        total_investment = entry_premium * lot_size * lots_to_trade
        total_risk = risk_per_lot * lots_to_trade
        
        # Check if investment exceeds 20-30% of capital
        max_deployment = account_capital * 0.25
        
        if total_investment > max_deployment:
            # Reduce lots to fit deployment limit
            lots_to_trade = int(max_deployment / (entry_premium * lot_size))
            total_investment = entry_premium * lot_size * lots_to_trade
            total_risk = risk_per_lot * lots_to_trade
        
        return {
            'lots': lots_to_trade,
            'quantity': lots_to_trade * lot_size,
            'investment': round(total_investment, 2),
            'max_risk': round(total_risk, 2),
            'risk_pct_of_capital': round((total_risk / account_capital) * 100, 2),
            'deployment_pct': round((total_investment / account_capital) * 100, 2)
        }


def generate_option_signal(
    symbol: str,
    spot_price: float,
    signal_direction: str,  # 'LONG' or 'SHORT'
    option_chain: pd.DataFrame,
    lot_size: int,
    confidence: str = 'MEDIUM',
    account_capital: float = 100000,
    strategy: str = 'CONSERVATIVE'
) -> Dict:
    """
    Generate complete option trading signal
    
    Args:
        symbol: Stock symbol
        spot_price: Current stock price
        signal_direction: 'LONG' (buy CE) or 'SHORT' (buy PE)
        option_chain: Option chain data
        lot_size: Contract lot size
        confidence: Signal confidence
        account_capital: Trading capital
        strategy: Risk strategy
    
    Returns:
        Complete trade signal with all details
    """
    
    analyzer = OptionAnalyzer(access_token="")  # Token will be fetched elsewhere
    
    # Determine option type
    option_type = 'CE' if signal_direction == 'LONG' else 'PE'
    
    # Select best strike
    best_strike = analyzer.select_best_strike(
        option_chain,
        spot_price,
        direction=option_type,
        confidence=confidence
    )
    
    if not best_strike:
        return {'error': 'No suitable strike found'}
    
    # Calculate TP/SL
    tp_sl = analyzer.calculate_tp_sl(
        best_strike['premium'],
        strategy=strategy,
        holding_period='INTRADAY',
        theta=best_strike.get('theta', 0)
    )
    
    # Calculate position size
    position = analyzer.calculate_position_size(
        best_strike['premium'],
        lot_size,
        account_capital,
        sl_pct=tp_sl['sl_loss_pct'] / 100
    )
    
    # Calculate P&L projections
    entry_value = position['investment']
    tp_value = tp_sl['tp_premium'] * position['quantity']
    sl_value = tp_sl['sl_premium'] * position['quantity']
    
    tp_profit = tp_value - entry_value
    sl_loss = entry_value - sl_value
    
    return {
        'symbol': symbol,
        'spot_price': spot_price,
        'signal': signal_direction,
        'option_type': option_type,
        'strike': best_strike['strike'],
        'entry_premium': best_strike['premium'],
        'delta': best_strike['delta'],
        'iv': best_strike['iv'],
        'oi': best_strike['oi'],
        'lot_size': lot_size,
        'lots': position['lots'],
        'quantity': position['quantity'],
        'investment': position['investment'],
        'tp_premium': tp_sl['tp_premium'],
        'sl_premium': tp_sl['sl_premium'],
        'tp_value': round(tp_value, 2),
        'sl_value': round(sl_value, 2),
        'tp_profit': round(tp_profit, 2),
        'sl_loss': round(sl_loss, 2),
        'risk_reward': f"1:{round(tp_profit/sl_loss, 2)}",
        'score': best_strike['score']
    }