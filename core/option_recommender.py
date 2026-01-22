# core/option_recommender.py
"""
Option Recommendation Engine
=============================
Analyzes option chains and recommends best options for underlying signals.

Uses Greeks-based scoring to rank options by:
- Delta appropriateness (directional exposure)
- Liquidity (OI, volume, bid-ask spread)
- Theta efficiency (time decay)
- Capital efficiency (leverage)
- IV levels (avoid over/under-priced)

Author: Trading Bot Pro
Version: 1.0
Date: 2026-01-17
"""

from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass, field
from datetime import datetime, date
import pandas as pd
import numpy as np
import logging

from core.greeks_calculator import GreeksCalculator
from core.option_chain_provider import OptionChainProvider
from core.option_selector import UnderlyingSignal
from core.database import get_db

logger = logging.getLogger(__name__)


@dataclass
class OptionRecommendation:
    """
    Single option recommendation with complete analysis

    All information needed to trade an option based on underlying signal.
    """
    # Identification
    symbol: str
    strike: float
    option_type: str  # CE or PE
    expiry_date: str
    instrument_key: str = ''

    # Pricing
    premium: float = 0.0
    lot_size: int = 0

    # Greeks
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    iv: float = 0.0

    # Liquidity metrics
    oi: int = 0
    volume: int = 0
    bid_ask_spread_pct: float = 0.0

    # Position sizing
    capital_required: float = 0.0
    potential_return: float = 0.0
    potential_return_pct: float = 0.0

    # Ranking
    rank_score: float = 0.0  # 0-100
    rank_reason: str = ''

    # Additional metadata
    moneyness: str = ''  # ITM, ATM, OTM
    distance_from_atm: int = 0  # Number of strikes from ATM
    underlying_entry: float = 0.0
    underlying_target: float = 0.0
    signal_type: str = ''  # LONG or SHORT

    def to_dict(self) -> dict:
        """Convert to dictionary for DataFrame creation"""
        return {
            'Symbol': self.symbol,
            'Strike': self.strike,
            'Type': self.option_type,
            'Expiry': self.expiry_date,
            'Premium': self.premium,
            'Delta': self.delta,
            'IV': self.iv,
            'OI': self.oi,
            'Capital': self.capital_required,
            'Potential': self.potential_return,
            'ROI%': self.potential_return_pct,
            'Score': self.rank_score,
            'Moneyness': self.moneyness,
            'Reason': self.rank_reason
        }


class OptionRecommender:
    """
    Recommend best options for underlying signals

    Main Features:
    1. Fetches option chain from Upstox API
    2. Calculates Greeks if not available from API
    3. Filters options by liquidity and moneyness
    4. Ranks options using multi-factor scoring
    5. Returns top N recommendations

    Usage:
        >>> recommender = OptionRecommender()
        >>> recommendations = recommender.recommend_for_signal(
        ...     signal=underlying_signal,
        ...     max_recommendations=3,
        ...     capital_per_trade=50000
        ... )
        >>> for rec in recommendations:
        ...     print(f"{rec.strike} {rec.option_type}: Score {rec.rank_score}/100")
    """

    def __init__(self):
        self.greeks_calc = GreeksCalculator()
        self.chain_provider = OptionChainProvider()
        self.db = get_db()

    def recommend_for_signal(
        self,
        signal: UnderlyingSignal,
        max_recommendations: int = 3,
        capital_per_trade: float = 50000,
        prefer_weekly: bool = True
    ) -> List[OptionRecommendation]:
        """
        Find best option contracts for an underlying signal

        Args:
            signal: UnderlyingSignal from strategy (Squeeze, EHMA, etc.)
            max_recommendations: Number of options to return
            capital_per_trade: Max capital to allocate per trade
            prefer_weekly: Prefer weekly expiry over monthly (for short-term signals)

        Returns:
            List of OptionRecommendation sorted by rank_score (best first)

        Steps:
            1. Fetch option chain from API
            2. Filter chain by option type (CE for LONG, PE for SHORT)
            3. Filter by strike range (ATM ± N strikes)
            4. Filter by liquidity (min OI, volume)
            5. Calculate/verify Greeks
            6. Rank each option using scoring algorithm
            7. Return top N ranked options
        """
        logger.info(f"Finding options for {signal.symbol} {signal.side} signal")

        # 1. Fetch option chain
        chain_dict = self.chain_provider.fetch_option_chain(signal)

        if not chain_dict or (not chain_dict.get('CE') and not chain_dict.get('PE')):
            logger.warning(f"No option chain data for {signal.symbol}")
            return []

        # 2. Select CE or PE based on signal direction
        option_type = 'CE' if signal.side == 'LONG' else 'PE'
        chain_list = chain_dict.get(option_type, [])

        if not chain_list:
            logger.warning(f"No {option_type} options found")
            return []

        logger.info(f"Found {len(chain_list)} {option_type} options")

        # Convert to DataFrame for easier filtering
        chain_df = pd.DataFrame(chain_list)

        # 3. Filter chain
        filtered_df = self._filter_chain(chain_df, signal, option_type)

        if filtered_df.empty:
            logger.warning("No options passed filters")
            return []

        logger.info(f"{len(filtered_df)} options passed filters")

        # 4. Get lot size from database
        lot_size = self._get_lot_size(signal.symbol)

        # 5. Analyze and rank each option
        recommendations = []
        for _, opt_row in filtered_df.iterrows():
            try:
                rec = self._analyze_option(
                    opt_row, signal, lot_size, capital_per_trade
                )
                recommendations.append(rec)
            except Exception as e:
                logger.error(f"Error analyzing option {opt_row.get('strike')}: {e}")
                continue

        # 6. Sort by rank score
        recommendations.sort(key=lambda x: x.rank_score, reverse=True)

        # 7. Return top N
        top_recs = recommendations[:max_recommendations]
        logger.info(f"Returning top {len(top_recs)} recommendations")

        return top_recs

    def _filter_chain(
        self,
        chain_df: pd.DataFrame,
        signal: UnderlyingSignal,
        option_type: str
    ) -> pd.DataFrame:
        """
        Filter option chain to relevant strikes

        Filters applied:
        1. Option type (CE/PE)
        2. Strike range (ATM ± 5 strikes)
        3. Minimum OI (liquidity)
        4. Minimum volume
        5. Valid premium (> 0)
        """
        if chain_df.empty:
            return chain_df

        # Filter 1: Ensure option_type column matches
        if 'option_type' in chain_df.columns:
            chain_df = chain_df[chain_df['option_type'] == option_type].copy()

        # Filter 2: Strike range (ATM ± 5 strikes)
        atm_strike = self._find_atm_strike(chain_df, signal.entry)
        strike_gap = self._estimate_strike_gap(chain_df)

        min_strike = atm_strike - (5 * strike_gap)
        max_strike = atm_strike + (5 * strike_gap)

        chain_df = chain_df[
            (chain_df['strike'] >= min_strike) &
            (chain_df['strike'] <= max_strike)
        ].copy()

        logger.info(f"Strike range: {min_strike} to {max_strike} (ATM: {atm_strike})")

        # Filter 3: Minimum liquidity
        min_oi = 1000  # Minimum open interest
        min_volume = 100  # Minimum daily volume

        chain_df = chain_df[
            (chain_df['oi'].fillna(0) >= min_oi) |
            (chain_df['volume'].fillna(0) >= min_volume)
        ].copy()

        # Filter 4: Valid premium
        chain_df = chain_df[chain_df['ltp'] > 0].copy()

        return chain_df

    def _analyze_option(
        self,
        opt_row: pd.Series,
        signal: UnderlyingSignal,
        lot_size: int,
        capital: float
    ) -> OptionRecommendation:
        """
        Analyze single option and create recommendation

        Steps:
        1. Calculate/verify Greeks
        2. Calculate position sizing
        3. Estimate potential returns
        4. Rank option
        5. Create OptionRecommendation object
        """
        strike = float(opt_row['strike'])
        premium = float(opt_row['ltp'])
        expiry = opt_row.get('expiry', '')

        # 1. Calculate Greeks (use API Greeks if available, else calculate)
        greeks = self._get_greeks(opt_row, signal, expiry)

        # 2. Calculate position sizing
        lots_possible = capital / (premium * lot_size) if premium > 0 else 0
        lots_to_trade = max(1, int(lots_possible))
        capital_required = premium * lot_size * lots_to_trade

        # 3. Estimate potential returns
        potential_return, return_pct = self._estimate_returns(
            signal, strike, premium, lot_size, lots_to_trade
        )

        # 4. Rank option
        rank_score, rank_reason = self._rank_option(
            opt_row, greeks, signal, capital, lots_possible
        )

        # 5. Determine moneyness
        moneyness = self._calculate_moneyness(signal.entry, strike)
        distance_from_atm = self._distance_from_atm(
            strike, signal.entry, self._estimate_strike_gap(pd.DataFrame([opt_row]))
        )

        # 6. Create recommendation
        return OptionRecommendation(
            symbol=signal.symbol,
            strike=strike,
            option_type=opt_row.get('option_type', 'CE' if signal.side == 'LONG' else 'PE'),
            expiry_date=expiry,
            instrument_key=opt_row.get('instrument_key', ''),
            premium=premium,
            lot_size=lot_size,
            delta=greeks['delta'],
            gamma=greeks['gamma'],
            theta=greeks['theta'],
            vega=greeks['vega'],
            iv=greeks['iv'],
            oi=int(opt_row.get('oi', 0)),
            volume=int(opt_row.get('volume', 0)),
            bid_ask_spread_pct=0.0,  # Calculate if bid/ask available
            capital_required=capital_required,
            potential_return=potential_return,
            potential_return_pct=return_pct,
            rank_score=rank_score,
            rank_reason=rank_reason,
            moneyness=moneyness,
            distance_from_atm=distance_from_atm,
            underlying_entry=signal.entry,
            underlying_target=signal.target,
            signal_type=signal.side
        )

    def _get_greeks(
        self,
        opt_row: pd.Series,
        signal: UnderlyingSignal,
        expiry: str
    ) -> Dict[str, float]:
        """
        Get Greeks - use API data if available, else calculate

        Upstox API sometimes provides Greeks, sometimes doesn't.
        """
        # Check if API provided Greeks
        api_delta = opt_row.get('delta')
        api_iv = opt_row.get('iv')

        if api_delta is not None and api_iv is not None:
            # Use API Greeks
            return {
                'delta': float(api_delta),
                'gamma': float(opt_row.get('gamma', 0)),
                'theta': float(opt_row.get('theta', 0)),
                'vega': float(opt_row.get('vega', 0)),
                'iv': float(api_iv)
            }

        # Calculate Greeks ourselves
        days_to_expiry = self._calculate_days_to_expiry(expiry)
        option_type = 'c' if opt_row.get('option_type') == 'CE' else 'p'

        # If no IV from API, calculate it from market price
        if api_iv is None or api_iv <= 0:
            api_iv = self.greeks_calc.calculate_implied_volatility(
                market_price=float(opt_row['ltp']),
                spot_price=signal.entry,
                strike_price=float(opt_row['strike']),
                time_to_expiry_days=days_to_expiry,
                option_type=option_type
            )

        return self.greeks_calc.calculate_greeks(
            spot_price=signal.entry,
            strike_price=float(opt_row['strike']),
            time_to_expiry_days=days_to_expiry,
            implied_volatility=api_iv if api_iv else 0.25,
            option_type=option_type
        )

    def _estimate_returns(
        self,
        signal: UnderlyingSignal,
        strike: float,
        entry_premium: float,
        lot_size: int,
        lots: int
    ) -> Tuple[float, float]:
        """
        Estimate potential returns if underlying hits target

        Simple estimate: If underlying moves to target, option premium
        should increase by similar percentage (conservative estimate).

        For more accurate: Could recalculate option price at target using BS model.
        """
        # Calculate underlying move percentage
        underlying_move_pct = abs((signal.target - signal.entry) / signal.entry)

        # Conservative estimate: option moves 70% of underlying move
        # (due to delta < 1.0)
        option_move_pct = underlying_move_pct * 0.7

        exit_premium = entry_premium * (1 + option_move_pct)
        profit_per_lot = (exit_premium - entry_premium) * lot_size
        total_profit = profit_per_lot * lots

        profit_pct = ((exit_premium - entry_premium) / entry_premium) * 100

        return round(total_profit, 2), round(profit_pct, 2)

    def _rank_option(
        self,
        opt_row: pd.Series,
        greeks: Dict[str, float],
        signal: UnderlyingSignal,
        capital: float,
        lots_possible: float
    ) -> Tuple[float, str]:
        """
        Rank option using multi-factor scoring (0-100)

        Scoring breakdown:
        - Delta appropriateness: 30 points
        - Liquidity: 20 points
        - Theta efficiency: 20 points
        - Capital efficiency: 15 points
        - IV level: 15 points
        """
        score = 0
        reasons = []

        # 1. Delta score (30 points) - prefer 0.5-0.7 absolute value
        abs_delta = abs(greeks['delta'])
        if 0.5 <= abs_delta <= 0.7:
            delta_score = 30
            reasons.append("Optimal delta")
        elif 0.4 <= abs_delta < 0.5 or 0.7 < abs_delta <= 0.8:
            delta_score = 20
            reasons.append("Good delta")
        elif 0.3 <= abs_delta < 0.4:
            delta_score = 15
            reasons.append("Moderate delta")
        else:
            delta_score = 10
            reasons.append("Suboptimal delta")
        score += delta_score

        # 2. Liquidity score (20 points)
        oi = opt_row.get('oi', 0)
        volume = opt_row.get('volume', 0)

        if oi > 50000 and volume > 1000:
            liq_score = 20
            reasons.append("Excellent liquidity")
        elif oi > 10000 and volume > 500:
            liq_score = 15
            reasons.append("Good liquidity")
        elif oi > 5000:
            liq_score = 10
            reasons.append("Moderate liquidity")
        else:
            liq_score = 5
            reasons.append("Low liquidity")
        score += liq_score

        # 3. Theta efficiency (20 points) - lower theta decay is better
        premium = opt_row['ltp']
        if premium > 0:
            daily_theta_pct = abs(greeks['theta']) / premium * 100
            if daily_theta_pct < 1:
                theta_score = 20
                reasons.append("Low theta decay")
            elif daily_theta_pct < 2:
                theta_score = 15
                reasons.append("Moderate theta")
            elif daily_theta_pct < 3:
                theta_score = 10
                reasons.append("Acceptable theta")
            else:
                theta_score = 5
                reasons.append("High theta decay")
        else:
            theta_score = 5
        score += theta_score

        # 4. Capital efficiency (15 points)
        if lots_possible >= 2:
            cap_score = 15
            reasons.append("Good capital efficiency")
        elif lots_possible >= 1:
            cap_score = 10
            reasons.append("Moderate capital use")
        else:
            cap_score = 5
            reasons.append("High capital requirement")
        score += cap_score

        # 5. IV level (15 points) - prefer reasonable IV (15-30%)
        iv_pct = greeks['iv']
        if 15 <= iv_pct <= 30:
            iv_score = 15
            reasons.append("Optimal IV")
        elif 10 <= iv_pct < 15 or 30 < iv_pct <= 40:
            iv_score = 10
            reasons.append("Acceptable IV")
        else:
            iv_score = 5
            reasons.append("Extreme IV")
        score += iv_score

        return round(score, 1), ", ".join(reasons)

    def _find_atm_strike(self, chain_df: pd.DataFrame, spot_price: float) -> float:
        """Find closest strike to spot price (ATM)"""
        if chain_df.empty:
            return spot_price

        chain_df['diff'] = abs(chain_df['strike'] - spot_price)
        atm_strike = chain_df.loc[chain_df['diff'].idxmin(), 'strike']
        return float(atm_strike)

    def _estimate_strike_gap(self, chain_df: pd.DataFrame) -> float:
        """Estimate gap between strikes"""
        if len(chain_df) < 2:
            return 50.0  # Default

        strikes = sorted(chain_df['strike'].unique())
        gaps = [strikes[i+1] - strikes[i] for i in range(len(strikes)-1)]
        return np.median(gaps) if gaps else 50.0

    def _calculate_moneyness(self, spot: float, strike: float) -> str:
        """Calculate if option is ITM, ATM, or OTM"""
        diff_pct = abs((spot - strike) / spot) * 100

        if diff_pct < 1:
            return 'ATM'
        elif spot > strike:
            return 'ITM'
        else:
            return 'OTM'

    def _distance_from_atm(self, strike: float, spot: float, gap: float) -> int:
        """Calculate number of strikes from ATM"""
        if gap == 0:
            return 0
        return int(abs(strike - spot) / gap)

    def _calculate_days_to_expiry(self, expiry_str: str) -> float:
        """Calculate days to expiry"""
        if not expiry_str:
            return 7.0  # Default to 7 days

        try:
            if isinstance(expiry_str, str):
                expiry_date = datetime.strptime(expiry_str.split()[0], '%Y-%m-%d').date()
            else:
                expiry_date = expiry_str

            return self.greeks_calc.calculate_days_to_expiry(expiry_date)
        except:
            return 7.0

    def _get_lot_size(self, symbol: str) -> int:
        """Get lot size from database"""
        symbol_upper = symbol.upper()

        # First try fo_stocks_master table
        try:
            result = self.db.con.execute("""
                SELECT lot_size
                FROM fo_stocks_master
                WHERE trading_symbol = ?
                LIMIT 1
            """, [symbol_upper]).fetchone()

            if result and result[0] and int(result[0]) > 0:
                return int(result[0])
        except:
            pass

        # Then try instruments table with option type
        try:
            result = self.db.con.execute("""
                SELECT lot_size
                FROM instruments
                WHERE trading_symbol LIKE ? || '%'
                  AND (instrument_type = 'CE' OR instrument_type = 'PE')
                  AND lot_size > 0
                LIMIT 1
            """, [symbol_upper]).fetchone()

            if result and result[0] and int(result[0]) > 0:
                return int(result[0])
        except:
            pass

        # Default lot sizes for common instruments
        defaults = {
            'NIFTY': 25,
            'BANKNIFTY': 15,
            'FINNIFTY': 25,
            'MIDCPNIFTY': 50,
            # Common F&O stocks with typical lot sizes
            'RELIANCE': 250,
            'TCS': 150,
            'HDFCBANK': 550,
            'INFY': 300,
            'ICICIBANK': 700,
            'SBIN': 750,
            'BHARTIARTL': 475,
            'ITC': 1600,
            'KOTAKBANK': 400,
            'LT': 150,
            'AXISBANK': 600,
            'MARUTI': 50,
            'TATAMOTORS': 575,
            'TATASTEEL': 625,
            'BAJFINANCE': 125,
            'WIPRO': 1500,
            'HCLTECH': 350,
            'ASIANPAINT': 200,
            'POWERGRID': 2700,
            'NTPC': 1750,
            'ONGC': 1925,
            'ADANIENT': 250,
            'ADANIPORTS': 625,
            'COALINDIA': 1050,
            'HINDALCO': 1075,
            'JSWSTEEL': 375,
            'ULTRACEMCO': 50,
            'TITAN': 175,
            'SUNPHARMA': 350,
            'DIVISLAB': 100,
            'DRREDDY': 125,
            'CIPLA': 325,
            'APOLLOHOSP': 125,
            'TECHM': 300,
            'INDUSINDBK': 450,
            'BAJAJFINSV': 125,
            'HDFC': 300,
            'NESTLEIND': 25,
            'M&M': 350,
            'HEROMOTOCO': 150,
            'EICHERMOT': 15,
            'GRASIM': 250,
            'UPL': 650,
            'TATACONSUM': 300,
            'BPCL': 900,
            'BRITANNIA': 100,
            'SBILIFE': 375,
            'HDFCLIFE': 550,
            'PAGEIND': 10,
            'EXIDEIND': 2000,
            'LTIM': 150,
            'POWERINDIA': 50,
        }
        return defaults.get(symbol_upper, 1)


if __name__ == "__main__":
    print("=== Option Recommender Test ===")
    print("This requires valid API token and option chain data")
    print("Run from Streamlit UI for full functionality")
