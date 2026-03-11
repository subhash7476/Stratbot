"""
Options Facade
--------------
Bridge between Flask UI and core options logic.
All methods are read-only (no state mutation).

Usage:
    facade = OptionsFacade()
    data = facade.get_structural_data("NIFTY")
"""

from typing import List, Dict, Optional, Any
from datetime import datetime
from dataclasses import asdict
from pathlib import Path
import math
import logging

from core.logging import setup_logger
from core.data.options_provider import OptionsProvider, OptionChainRow
from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.analytics.options_analytics import (
    OptionsAnalytics,
    OptionsStructuralData,
    PCRResult,
    GEXResult,
    OIAnalysisResult,
    MaxPainResult,
)

logger = setup_logger("options_facade")


class OptionsFacade:
    """
    Bridge between UI and options structural engine.
    
    Responsibilities:
    - Fetch option chain from provider
    - Calculate structural metrics via analytics
    - Format data for UI consumption
    - Cache management for repeated requests
    """
    
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        from core.data.options_provider import OptionsProvider
        # Use poller database path to read cached data (read-only to avoid file lock with poller)
        self._provider = OptionsProvider(db_path=Path("data/market_data/options_poller.duckdb"), read_only=True)
        self._analytics = OptionsAnalytics()
        self._db = db_manager or DatabaseManager(Path("data"))
        self._market_query = MarketDataQuery(self._db)
        
        # Cache for structural data
        self._last_snapshot: Dict[str, OptionsStructuralData] = {}
        self._last_fetch_time: Dict[str, datetime] = {}
        self._rv_cache: Dict[str, Optional[float]] = {}
        self._rv_cache_time: Dict[str, datetime] = {}
    
    def get_structural_data(
        self,
        index: str,
        expiry: Optional[str] = None,
        force_refresh: bool = False
    ) -> OptionsStructuralData:
        """
        Get complete structural snapshot for an index.
        
        Args:
            index: "NIFTY" or "BANKNIFTY"
            expiry: YYYY-MM-DD format (default: nearest weekly)
            force_refresh: Force fresh fetch from API
            
        Returns:
            OptionsStructuralData with all metrics
        """
        # Map index to underlying
        underlying = self._provider.get_index(index.upper())
        if not underlying:
            raise ValueError(f"Unknown index: {index}")
        
        # Get expiry (default to nearest weekly)
        if not expiry:
            expiry = self._provider.get_weekly_expiry(underlying)
        
        cache_key = f"{underlying}:{expiry}"
        
        # Check cache (5 second freshness)
        if not force_refresh and cache_key in self._last_fetch_time:
            age = (datetime.now() - self._last_fetch_time[cache_key]).total_seconds()
            if age < 5:
                cached = self._last_snapshot.get(cache_key)
                if cached:
                    return cached
        
        # Fetch option chain
        option_chain = self._provider.fetch_option_chain(underlying, expiry, force_refresh)
        
        if not option_chain:
            # Try from cache
            option_chain = self._provider.get_cached_option_chain(underlying, expiry)
        
        if not option_chain:
            raise ValueError(f"No option chain data available for {index} {expiry}")
        
        # Get underlying LTP
        underlying_ltp = self._provider.get_underlying_ltp(underlying)
        if not underlying_ltp:
            # Fetch from first row if available
            underlying_ltp = option_chain[0].underlying_ltp if option_chain else 0
        
        if not underlying_ltp:
            underlying_ltp = self._fetch_underlying_ltp(underlying)
        
        # Get previous PCR for change calculation
        previous_pcr = self._get_previous_pcr(underlying, expiry)
        
        # Build structural snapshot
        snapshot = self._analytics.build_structural_snapshot(
            option_chain=option_chain,
            underlying=underlying,
            underlying_ltp=underlying_ltp,
            expiry=expiry,
            previous_pcr=previous_pcr,
            include_max_pain=False  # Max pain is secondary, skip by default
        )
        
        # Cache
        self._last_snapshot[cache_key] = snapshot
        self._last_fetch_time[cache_key] = datetime.now()
        
        return snapshot
    
    def get_option_chain(
        self,
        index: str,
        expiry: Optional[str] = None,
        strikes_range: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get option chain formatted for UI table.
        
        Args:
            index: "NIFTY" or "BANKNIFTY"
            expiry: YYYY-MM-DD format
            strikes_range: Number of strikes around ATM (default: all)
            
        Returns:
            List of dicts with CE | Strike | PE format
        """
        structural = self.get_structural_data(index, expiry)
        
        # Fetch raw chain
        underlying = self._provider.get_index(index.upper())
        chain = self._provider.get_cached_option_chain(underlying, expiry)

        if not chain:
            return []
        
        realized_vol = self._get_realized_volatility(underlying)
        
        # Group by strike
        strikes = sorted(set(row.strike for row in chain))
        
        # Filter to ATM ± range if specified
        if strikes_range and structural.atm_strike:
            atm_idx = strikes.index(structural.atm_strike) if structural.atm_strike in strikes else len(strikes) // 2
            start_idx = max(0, atm_idx - strikes_range)
            end_idx = min(len(strikes), atm_idx + strikes_range + 1)
            strikes = strikes[start_idx:end_idx]
        
        # Build CE/Strike/PE rows
        result = []
        for strike in strikes:
            ce_row = next((r for r in chain if r.strike == strike and r.option_type == "CE"), None)
            pe_row = next((r for r in chain if r.strike == strike and r.option_type == "PE"), None)
            
            result.append({
                "strike": strike,
                "ce_oi": ce_row.oi if ce_row else 0,
                "ce_oi_change": ce_row.oi_change if ce_row else 0,
                "ce_ltp": ce_row.ltp if ce_row else None,
                "ce_iv": ce_row.iv if ce_row else None,
                "ce_delta": ce_row.delta if ce_row else None,
                "ce_gamma": ce_row.gamma if ce_row else None,
                "pe_oi": pe_row.oi if pe_row else 0,
                "pe_oi_change": pe_row.oi_change if pe_row else 0,
                "pe_ltp": pe_row.ltp if pe_row else None,
                "pe_iv": pe_row.iv if pe_row else None,
                "pe_delta": pe_row.delta if pe_row else None,
                "pe_gamma": pe_row.gamma if pe_row else None,
                "realized_vol": realized_vol,
                "is_atm": strike == structural.atm_strike
            })
        
        return result
    
    def get_expiries(self, index: str, count: int = 4) -> List[str]:
        """
        Get available weekly expiry dates.
        
        Args:
            index: "NIFTY" or "BANKNIFTY"
            count: Number of expiries to return
            
        Returns:
            List of YYYY-MM-DD dates
        """
        underlying = self._provider.get_index(index.upper())
        if not underlying:
            return []
        
        return self._provider.get_available_expiries(underlying, count)
    
    def get_gex_distribution(
        self,
        index: str,
        expiry: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get Gamma Exposure distribution by strike.
        
        Args:
            index: "NIFTY" or "BANKNIFTY"
            expiry: YYYY-MM-DD format
            
        Returns:
            List of {strike, gamma_exposure, option_type} for charting
        """
        structural = self.get_structural_data(index, expiry)
        return structural.gex.gamma_distribution
    
    def get_oi_distribution(
        self,
        index: str,
        expiry: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get OI distribution for charting.

        Args:
            index: "NIFTY" or "BANKNIFTY"
            expiry: YYYY-MM-DD format

        Returns:
            Dict with CE and PE OI by strike
        """
        # Get structural data first — this populates the in-memory cache via API fetch
        structural = self.get_structural_data(index, expiry)

        underlying = self._provider.get_index(index.upper())
        effective_expiry = expiry or self._provider.get_weekly_expiry(underlying)
        chain = self._provider.get_cached_option_chain(underlying, effective_expiry)

        if not chain:
            return {"ce_by_strike": {}, "pe_by_strike": {}}

        ce_by_strike = {}
        pe_by_strike = {}

        for row in chain:
            # Use int keys to avoid JSON float serialization ("54000.0" vs "54000")
            strike_key = int(row.strike)
            if row.option_type == "CE":
                ce_by_strike[strike_key] = row.oi
            else:
                pe_by_strike[strike_key] = row.oi

        return {
            "ce_by_strike": ce_by_strike,
            "pe_by_strike": pe_by_strike,
            "highest_ce": structural.oi_analysis.highest_ce_oi_strikes,
            "highest_pe": structural.oi_analysis.highest_pe_oi_strikes
        }
    
    def get_summary(self, index: str, expiry: Optional[str] = None) -> Dict[str, Any]:
        """
        Get summary cards data.
        
        Args:
            index: "NIFTY" or "BANKNIFTY"
            expiry: YYYY-MM-DD format
            
        Returns:
            Dict with key metrics for summary cards
        """
        structural = self.get_structural_data(index, expiry)
        
        return {
            "underlying_ltp": structural.underlying_ltp,
            "atm_strike": structural.atm_strike,
            "pcr": structural.pcr.pcr,
            "pcr_sentiment": structural.pcr.sentiment,
            "pcr_change": structural.pcr.pcr_change,
            "net_gamma": structural.gex.net_gamma_total,
            "gex_regime": structural.gex.regime,
            "zero_gamma_level": structural.gex.zero_gamma_level,
            "resistance_strike": structural.oi_analysis.resistance_strike,
            "support_strike": structural.oi_analysis.support_strike,
            "total_strikes": structural.total_strikes,
            "last_update": structural.last_update.isoformat()
        }
    
    def _get_previous_pcr(
        self,
        underlying: str,
        expiry: str
    ) -> Optional[float]:
        """Get previous PCR from cache for change calculation."""
        cache_key = f"{underlying}:{expiry}"
        if cache_key in self._last_snapshot:
            return self._last_snapshot[cache_key].pcr.pcr
        return None
    
    def _fetch_underlying_ltp(self, underlying: str) -> float:
        """Fetch underlying LTP from Upstox."""
        from core.brokers.upstox_market_data import UpstoxMarketData
        
        ltp_key_map = {
            "NSE_INDEX|Nifty 50": "NSE_INDEX|IND-NIFTY 50",
            "NSE_INDEX|Nifty Bank": "NSE_INDEX|IND-Nifty Bank",
        }
        
        instrument_key = ltp_key_map.get(underlying)
        if not instrument_key:
            return 0.0
        
        market_data = UpstoxMarketData()
        return market_data.fetch_ltp(instrument_key) or 0.0
    
    def to_dict(self, data: OptionsStructuralData) -> Dict[str, Any]:
        """Convert structural data to JSON-serializable dict."""
        return {
            "underlying": data.underlying,
            "underlying_ltp": data.underlying_ltp,
            "expiry": data.expiry,
            "timestamp": data.timestamp.isoformat(),
            "atm_strike": data.atm_strike,
            "total_strikes": data.total_strikes,
            "pcr": {
                "pcr": data.pcr.pcr,
                "total_ce_oi": data.pcr.total_ce_oi,
                "total_pe_oi": data.pcr.total_pe_oi,
                "sentiment": data.pcr.sentiment,
                "pcr_change": data.pcr.pcr_change,
                "pcr_by_strike": data.pcr.pcr_by_strike
            },
            "gex": {
                "net_gamma_total": data.gex.net_gamma_total,
                "net_gamma_ce": data.gex.net_gamma_ce,
                "net_gamma_pe": data.gex.net_gamma_pe,
                "zero_gamma_level": data.gex.zero_gamma_level,
                "regime": data.gex.regime,
                "gamma_by_strike": data.gex.gamma_by_strike
            },
            "oi_analysis": {
                "highest_ce_oi_strikes": data.oi_analysis.highest_ce_oi_strikes,
                "highest_pe_oi_strikes": data.oi_analysis.highest_pe_oi_strikes,
                "resistance_strike": data.oi_analysis.resistance_strike,
                "support_strike": data.oi_analysis.support_strike,
                "ce_oi_buildup": data.oi_analysis.ce_oi_buildup,
                "pe_oi_buildup": data.oi_analysis.pe_oi_buildup,
                "analysis_by_strike": data.oi_analysis.analysis_by_strike
            },
            "max_pain": {
                "max_pain_strike": data.max_pain.max_pain_strike,
                "total_pain": data.max_pain.total_pain,
                "distance_from_spot": data.max_pain.distance_from_spot
            } if data.max_pain else None,
            "last_update": data.last_update.isoformat()
        }

    def _get_realized_volatility(self, underlying: str) -> Optional[float]:
        """
        Compute annualized realized volatility from recent 1m close history.
        Formula: stdev(log returns) * sqrt(252)
        """
        # Cache for 30 seconds to avoid repeated heavy reads across table refreshes
        if underlying in self._rv_cache_time:
            age = (datetime.now() - self._rv_cache_time[underlying]).total_seconds()
            if age < 30:
                return self._rv_cache.get(underlying)

        try:
            # 375 bars approximates one full Indian trading session (1m bars)
            candles = self._market_query.get_candles(
                symbol=underlying,
                exchange="nse",
                timeframe="1m",
                limit=375
            )
            closes = []
            if not candles.empty and "close" in candles.columns:
                closes = [float(v) for v in candles["close"].tolist() if v is not None and float(v) > 0]

            # Fallback: read recent historical files directly if query path returns sparse/no bars
            if len(closes) < 2:
                closes = self._load_recent_closes_from_files(underlying, days=5)

            rv = self._annualized_rv_from_closes(closes)
        except Exception as e:
            logger.warning(f"Failed to compute realized volatility for {underlying}: {e}")
            rv = None

        self._rv_cache[underlying] = rv
        self._rv_cache_time[underlying] = datetime.now()
        return rv

    def _annualized_rv_from_closes(self, closes: List[float]) -> Optional[float]:
        """Compute annualized RV from close series."""
        if len(closes) < 2:
            return None

        log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i] > 0 and closes[i - 1] > 0]
        if len(log_returns) < 2:
            return None

        mean_ret = sum(log_returns) / len(log_returns)
        variance = sum((r - mean_ret) ** 2 for r in log_returns) / (len(log_returns) - 1)
        return math.sqrt(max(variance, 0.0)) * math.sqrt(252)

    def _load_recent_closes_from_files(self, symbol: str, days: int = 5) -> List[float]:
        """Load recent 1m closes by scanning historical daily DuckDB files."""
        import duckdb

        data_dir = Path("data/market_data/nse/candles/1m")
        if not data_dir.exists():
            return []

        closes: List[float] = []
        files = sorted(data_dir.glob("*.duckdb"), reverse=True)
        days_used = 0
        for f in files:
            if days_used >= days:
                break
            try:
                conn = duckdb.connect(str(f), read_only=True)
                rows = conn.execute(
                    "SELECT close FROM candles WHERE symbol = ? AND timeframe = '1m' ORDER BY timestamp ASC",
                    [symbol],
                ).fetchall()
                conn.close()
                if rows:
                    closes.extend(float(r[0]) for r in rows if r[0] is not None and float(r[0]) > 0)
                    days_used += 1
            except Exception:
                continue
        return closes
