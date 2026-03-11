"""
Options Analytics Engine
------------------------
Calculates options structural metrics: Net Gamma (GEX), PCR, OI Analysis.

Focus: Institutional-level structural metrics, not retail indicators.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime, date
import logging

from core.logging import setup_logger
from core.data.options_provider import OptionChainRow

logger = setup_logger("options_analytics")


@dataclass
class PCRResult:
    """Put-Call Ratio result."""
    pcr: float
    total_ce_oi: int
    total_pe_oi: int
    pcr_by_strike: Dict[float, float] = field(default_factory=dict)
    sentiment: str = "Neutral"
    pcr_change: Optional[float] = None  # vs previous snapshot


@dataclass
class OIAnalysisResult:
    """Open Interest analysis result."""
    analysis_by_strike: Dict[float, Dict] = field(default_factory=dict)
    highest_ce_oi_strikes: List[Tuple[float, int]] = field(default_factory=list)  # (strike, oi)
    highest_pe_oi_strikes: List[Tuple[float, int]] = field(default_factory=list)
    ce_oi_buildup: int = 0  # Count of strikes with long buildup
    pe_oi_buildup: int = 0
    
    # Structural levels
    resistance_strike: Optional[float] = None  # Highest CE OI
    support_strike: Optional[float] = None     # Highest PE OI


@dataclass
class GEXResult:
    """Net Gamma Exposure result."""
    net_gamma_total: float
    net_gamma_ce: float
    net_gamma_pe: float
    gamma_by_strike: Dict[float, float] = field(default_factory=dict)
    zero_gamma_level: Optional[float] = None  # Strike where net gamma = 0
    gamma_distribution: List[Dict] = field(default_factory=list)  # For charting
    
    # Interpretation
    regime: str = "Neutral"  # Positive GEX = stable, Negative GEX = volatile


@dataclass
class MaxPainResult:
    """Max Pain calculation result."""
    max_pain_strike: float
    spot_price: float
    distance_from_spot: float
    total_pain: float = 0.0
    pain_by_strike: Dict[float, float] = field(default_factory=dict)


@dataclass
class OptionsStructuralData:
    """Complete structural snapshot for an underlying."""
    underlying: str
    underlying_ltp: float
    expiry: str
    timestamp: datetime
    
    # Core metrics
    pcr: PCRResult
    gex: GEXResult
    oi_analysis: OIAnalysisResult
    max_pain: Optional[MaxPainResult] = None
    
    # Metadata
    total_strikes: int = 0
    atm_strike: Optional[float] = None
    last_update: datetime = field(default_factory=datetime.now)


class OptionsAnalytics:
    """
    Calculates options structural analytics.
    
    Key Metrics:
    1. Net Gamma Exposure (GEX) - Dealer hedging flows
    2. PCR - Put/Call ratio with historical context
    3. OI Analysis - Buildup patterns, support/resistance
    4. Max Pain - Strike with minimum option buyer pain (secondary metric)
    """
    
    # PCR interpretation thresholds (can be tuned per underlying)
    PCR_BULLISH_THRESHOLD = 1.2
    PCR_BEARISH_THRESHOLD = 0.7
    
    # Lot sizes (can be overridden by API data)
    LOT_SIZE_MAP = {
        "NSE_INDEX|Nifty 50": 75,
        "NSE_INDEX|Nifty Bank": 15,
    }
    
    @staticmethod
    def calculate_pcr(
        option_chain: List[OptionChainRow],
        previous_pcr: Optional[float] = None
    ) -> PCRResult:
        """
        Calculate Put-Call Ratio.
        
        PCR = Total PE OI / Total CE OI
        
        Interpretation:
        - PCR > 1.2 → Bullish (more puts = hedging)
        - PCR < 0.7 → Bearish (more calls = speculative)
        - 0.7 - 1.2 → Neutral
        
        Args:
            option_chain: List of option contracts
            previous_pcr: Previous PCR for change calculation
            
        Returns:
            PCRResult with overall and by-strike PCR
        """
        total_ce_oi = sum(row.oi for row in option_chain if row.option_type == "CE")
        total_pe_oi = sum(row.oi for row in option_chain if row.option_type == "PE")
        
        pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 0.0
        
        # By-strike PCR
        pcr_by_strike = {}
        strikes = set(row.strike for row in option_chain)
        for strike in strikes:
            ce_oi = sum(row.oi for row in option_chain if row.option_type == "CE" and row.strike == strike)
            pe_oi = sum(row.oi for row in option_chain if row.option_type == "PE" and row.strike == strike)
            if ce_oi > 0:
                pcr_by_strike[strike] = pe_oi / ce_oi
        
        sentiment = OptionsAnalytics._interpret_pcr(pcr)
        
        # Calculate change vs previous
        pcr_change = None
        if previous_pcr is not None:
            pcr_change = pcr - previous_pcr
        
        return PCRResult(
            pcr=pcr,
            total_ce_oi=total_ce_oi,
            total_pe_oi=total_pe_oi,
            pcr_by_strike=pcr_by_strike,
            sentiment=sentiment,
            pcr_change=pcr_change
        )
    
    @staticmethod
    def analyze_oi_changes(
        option_chain: List[OptionChainRow],
        underlying_ltp: float
    ) -> OIAnalysisResult:
        """
        Analyze OI buildup patterns and structural levels.
        
        Patterns identified:
        - Long Buildup: OI ↑ + Price ↑ → New long positions
        - Short Buildup: OI ↑ + Price ↓ → New short positions
        - Long Unwinding: OI ↓ + Price ↓ → Longs exiting
        - Short Covering: OI ↓ + Price ↑ → Shorts exiting
        
        Args:
            option_chain: List of option contracts
            underlying_ltp: Current underlying price
            
        Returns:
            OIAnalysisResult with patterns and structural levels
        """
        strikes = sorted(set(row.strike for row in option_chain))
        
        analysis_by_strike = {}
        ce_oi_buildup = 0
        pe_oi_buildup = 0
        
        for strike in strikes:
            ce_rows = [r for r in option_chain if r.strike == strike and r.option_type == "CE"]
            pe_rows = [r for r in option_chain if r.strike == strike and r.option_type == "PE"]
            
            ce_row = ce_rows[0] if ce_rows else None
            pe_row = pe_rows[0] if pe_rows else None
            
            # Identify patterns using OI change and option LTP change
            # Note: This is simplified - ideally we'd use underlying movement
            ce_pattern = None
            pe_pattern = None
            
            if ce_row:
                # Use option price change as proxy (not ideal but available)
                ce_price_change_pct = 0.0
                if ce_row.close and ce_row.ltp:
                    ce_price_change_pct = (ce_row.ltp - ce_row.close) / ce_row.close * 100 if ce_row.close > 0 else 0
                
                ce_pattern = OptionsAnalytics._identify_pattern(ce_row.oi_change, ce_price_change_pct)
                if ce_pattern == "Long Buildup":
                    ce_oi_buildup += 1
            
            if pe_row:
                pe_price_change_pct = 0.0
                if pe_row.close and pe_row.ltp:
                    pe_price_change_pct = (pe_row.ltp - pe_row.close) / pe_row.close * 100 if pe_row.close > 0 else 0
                
                pe_pattern = OptionsAnalytics._identify_pattern(pe_row.oi_change, pe_price_change_pct)
                if pe_pattern == "Long Buildup":
                    pe_oi_buildup += 1
            
            analysis_by_strike[strike] = {
                "ce_oi": ce_row.oi if ce_row else 0,
                "ce_oi_change": ce_row.oi_change if ce_row else 0,
                "ce_pattern": ce_pattern,
                "pe_oi": pe_row.oi if pe_row else 0,
                "pe_oi_change": pe_row.oi_change if pe_row else 0,
                "pe_pattern": pe_pattern
            }
        
        # Find highest OI strikes (resistance/support)
        ce_oi_by_strike = {}
        pe_oi_by_strike = {}
        
        for row in option_chain:
            if row.option_type == "CE":
                ce_oi_by_strike[row.strike] = row.oi
            else:
                pe_oi_by_strike[row.strike] = row.oi
        
        # Top 5 CE OI strikes (resistance)
        highest_ce = sorted(ce_oi_by_strike.items(), key=lambda x: x[1], reverse=True)[:5]
        # Top 5 PE OI strikes (support)
        highest_pe = sorted(pe_oi_by_strike.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Primary resistance/support
        resistance_strike = highest_ce[0][0] if highest_ce else None
        support_strike = highest_pe[0][0] if highest_pe else None
        
        return OIAnalysisResult(
            analysis_by_strike=analysis_by_strike,
            highest_ce_oi_strikes=highest_ce,
            highest_pe_oi_strikes=highest_pe,
            ce_oi_buildup=ce_oi_buildup,
            pe_oi_buildup=pe_oi_buildup,
            resistance_strike=resistance_strike,
            support_strike=support_strike
        )
    
    @staticmethod
    def calculate_gex(
        option_chain: List[OptionChainRow],
        underlying_ltp: float,
        lot_size: Optional[int] = None
    ) -> GEXResult:
        """
        Calculate Net Gamma Exposure (GEX).
        
        GEX = Σ (Gamma × OI × LotSize)
        
        Positive GEX: Dealers are long gamma → market stability
        Negative GEX: Dealers are short gamma → volatility amplification
        
        Zero Gamma Level: Strike where cumulative gamma = 0
        - Above this level: market makers stabilize moves
        - Below this level: market makers amplify moves
        
        Args:
            option_chain: List of option contracts
            underlying_ltp: Current underlying price
            lot_size: Contract lot size (default: from LOT_SIZE_MAP)
            
        Returns:
            GEXResult with net gamma and distribution
        """
        # Calculate gamma exposure by strike
        gamma_by_strike: Dict[float, float] = {}
        gamma_distribution = []
        
        for row in option_chain:
            if row.gamma is None or row.gamma == 0:
                continue
            
            effective_lot_size = lot_size or row.lot_size or 75
            gamma_exposure = row.gamma * row.oi * effective_lot_size
            
            # CE gamma is positive, PE gamma is negative (for dealer positioning)
            if row.option_type == "PE":
                gamma_exposure = -gamma_exposure
            
            strike = row.strike
            if strike not in gamma_by_strike:
                gamma_by_strike[strike] = 0
            gamma_by_strike[strike] += gamma_exposure
            
            gamma_distribution.append({
                "strike": strike,
                "option_type": row.option_type,
                "gamma": row.gamma,
                "oi": row.oi,
                "gamma_exposure": gamma_exposure
            })
        
        # Calculate totals
        net_gamma_ce = sum(
            row.gamma * row.oi * (lot_size or row.lot_size or 75)
            for row in option_chain
            if row.gamma and row.option_type == "CE"
        )
        
        net_gamma_pe = sum(
            -row.gamma * row.oi * (lot_size or row.lot_size or 75)
            for row in option_chain
            if row.gamma and row.option_type == "PE"
        )
        
        net_gamma_total = net_gamma_ce + net_gamma_pe
        
        # Find zero gamma level (strike where cumulative gamma crosses zero)
        zero_gamma_level = OptionsAnalytics._find_zero_gamma_level(gamma_by_strike)
        
        # Interpret regime
        if net_gamma_total > 0:
            regime = "Positive GEX (Stable)"
        elif net_gamma_total < 0:
            regime = "Negative GEX (Volatile)"
        else:
            regime = "Neutral"
        
        return GEXResult(
            net_gamma_total=net_gamma_total,
            net_gamma_ce=net_gamma_ce,
            net_gamma_pe=net_gamma_pe,
            gamma_by_strike=gamma_by_strike,
            zero_gamma_level=zero_gamma_level,
            gamma_distribution=gamma_distribution,
            regime=regime
        )
    
    @staticmethod
    def calculate_max_pain(
        option_chain: List[OptionChainRow],
        spot_price: float
    ) -> MaxPainResult:
        """
        Calculate Max Pain strike.
        
        Max Pain = Strike where total option buyer pain is minimum.
        
        Pain for CE = max(0, spot - strike) × CE_OI
        Pain for PE = max(0, strike - spot) × PE_OI
        
        Note: This is a secondary metric for reference only.
        
        Args:
            option_chain: List of option contracts
            spot_price: Current underlying price
            
        Returns:
            MaxPainResult with strike and pain distribution
        """
        strikes = sorted(set(row.strike for row in option_chain))
        
        # Build OI lookup
        ce_oi = {row.strike: row.oi for row in option_chain if row.option_type == "CE"}
        pe_oi = {row.strike: row.oi for row in option_chain if row.option_type == "PE"}
        
        # Calculate pain for each strike
        pain_by_strike = {}
        for strike in strikes:
            ce_pain = max(0, spot_price - strike) * ce_oi.get(strike, 0)
            pe_pain = max(0, strike - spot_price) * pe_oi.get(strike, 0)
            total_pain = ce_pain + pe_pain
            pain_by_strike[strike] = total_pain
        
        # Find strike with minimum pain
        max_pain_strike = min(strikes, key=lambda k: pain_by_strike.get(k, float('inf')))
        
        return MaxPainResult(
            max_pain_strike=max_pain_strike,
            total_pain=pain_by_strike.get(max_pain_strike, 0),
            pain_by_strike=pain_by_strike,
            spot_price=spot_price,
            distance_from_spot=spot_price - max_pain_strike
        )
    
    def build_structural_snapshot(
        self,
        option_chain: List[OptionChainRow],
        underlying: str,
        underlying_ltp: float,
        expiry: str,
        previous_pcr: Optional[float] = None,
        include_max_pain: bool = False
    ) -> OptionsStructuralData:
        """
        Build complete structural snapshot.
        
        Args:
            option_chain: Full option chain
            underlying: Underlying symbol
            underlying_ltp: Current underlying price
            expiry: Expiry date (YYYY-MM-DD)
            previous_pcr: Previous PCR for change calculation
            include_max_pain: Whether to calculate max pain (default: False)
            
        Returns:
            OptionsStructuralData with all metrics
        """
        # Calculate all metrics
        pcr = self.calculate_pcr(option_chain, previous_pcr)
        gex = self.calculate_gex(option_chain, underlying_ltp)
        oi_analysis = self.analyze_oi_changes(option_chain, underlying_ltp)
        
        # Max pain (optional - secondary metric)
        max_pain = None
        if include_max_pain:
            max_pain = self.calculate_max_pain(option_chain, underlying_ltp)
        
        # Find ATM strike
        atm_strike = self._find_atm_strike(option_chain, underlying_ltp)
        
        return OptionsStructuralData(
            underlying=underlying,
            underlying_ltp=underlying_ltp,
            expiry=expiry,
            timestamp=datetime.now(),
            pcr=pcr,
            gex=gex,
            oi_analysis=oi_analysis,
            max_pain=max_pain,
            total_strikes=len(set(row.strike for row in option_chain)),
            atm_strike=atm_strike,
            last_update=datetime.now()
        )
    
    @staticmethod
    def _identify_pattern(oi_change: int, price_change_pct: float) -> str:
        """Identify OI buildup pattern."""
        if oi_change > 0 and price_change_pct > 0:
            return "Long Buildup"
        elif oi_change > 0 and price_change_pct < 0:
            return "Short Buildup"
        elif oi_change < 0 and price_change_pct < 0:
            return "Long Unwinding"
        elif oi_change < 0 and price_change_pct > 0:
            return "Short Covering"
        else:
            return "No Change"
    
    @staticmethod
    def _interpret_pcr(pcr: float) -> str:
        """Interpret PCR value."""
        if pcr > OptionsAnalytics.PCR_BULLISH_THRESHOLD:
            return "Bullish"
        elif pcr < OptionsAnalytics.PCR_BEARISH_THRESHOLD:
            return "Bearish"
        else:
            return "Neutral"
    
    @staticmethod
    def _find_zero_gamma_level(gamma_by_strike: Dict[float, float]) -> Optional[float]:
        """
        Find strike where cumulative gamma crosses zero.
        
        Uses linear interpolation between strikes.
        """
        if not gamma_by_strike:
            return None
        
        strikes = sorted(gamma_by_strike.keys())
        
        # Calculate cumulative gamma
        cumulative = 0
        prev_strike = None
        prev_cumulative = None
        
        for strike in strikes:
            cumulative += gamma_by_strike[strike]
            
            # Check if we crossed zero
            if prev_cumulative is not None:
                if (prev_cumulative < 0 and cumulative >= 0) or \
                   (prev_cumulative >= 0 and cumulative < 0):
                    # Linear interpolation
                    zero_strike = prev_strike + (0 - prev_cumulative) / (cumulative - prev_cumulative) * (strike - prev_strike)
                    return zero_strike
            
            prev_strike = strike
            prev_cumulative = cumulative
        
        return None
    
    @staticmethod
    def _find_atm_strike(option_chain: List[OptionChainRow], underlying_ltp: float) -> Optional[float]:
        """Find the ATM strike (closest to underlying LTP)."""
        if not option_chain:
            return None
        
        strikes = set(row.strike for row in option_chain)
        return min(strikes, key=lambda s: abs(s - underlying_ltp))
