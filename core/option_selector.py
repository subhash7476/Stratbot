# core/option_selector.py
"""
Option Selector - Unified option selection for all strategies (EHMA, VCB, Supertrend, etc.)

Consolidates:
- UnderlyingSignal dataclass (formerly in signal_options_vcb.py)
- OptionSelection result dataclass
- OptionSelector with Greek-based filtering and ranking

Usage:
    from core.option_selector import (
        UnderlyingSignal,
        OptionSelection,
        OptionSelector,
        OptionSelectorConfig
    )

Author: Trading Bot Pro
Version: 2.0 (Consolidated)
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, time
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from core.database import get_db


# ============================================
# SIGNAL DATACLASS (Universal for all strategies)
# ============================================

@dataclass
class UnderlyingSignal:
    """
    Universal signal format for all strategies.
    
    Used by: EHMA MTF, VCB, Supertrend, Regime-based strategies
    
    Attributes:
        instrument_key: Upstox instrument key (e.g., "NSE_EQ|INE002A01018")
        symbol: Trading symbol (e.g., "RELIANCE")
        side: Trade direction - "LONG" or "SHORT"
        timeframe: Signal timeframe (e.g., "15minute", "5minute")
        entry: Entry price for underlying
        stop: Stop loss price for underlying
        target: Target price for underlying
        strength: Signal strength/confidence (0-100)
        strategy: Strategy name (e.g., "EHMA_MTF", "VCB", "SUPERTREND")
        timestamp: When signal was generated
        reason: Dict with additional signal metadata
    """
    instrument_key: str
    symbol: str
    side: str                 # LONG / SHORT
    timeframe: str
    entry: float
    stop: float
    target: float
    strength: float
    strategy: str
    timestamp: datetime
    reason: dict = field(default_factory=dict)
    
    def __post_init__(self):
        """Validate and normalize fields"""
        self.side = self.side.upper()
        if self.side not in ("LONG", "SHORT"):
            raise ValueError(f"side must be 'LONG' or 'SHORT', got '{self.side}'")
        
        self.symbol = self.symbol.upper()
    
    @property
    def direction(self) -> str:
        """Alias for side (backward compatibility)"""
        return self.side
    
    @property
    def risk_points(self) -> float:
        """Calculate risk in price points"""
        return abs(self.entry - self.stop)
    
    @property
    def reward_points(self) -> float:
        """Calculate reward in price points"""
        return abs(self.target - self.entry)
    
    @property
    def risk_reward_ratio(self) -> float:
        """Calculate R:R ratio"""
        risk = self.risk_points
        if risk <= 0:
            return 0.0
        return self.reward_points / risk
    
    def to_dict(self) -> dict:
        """Convert to dictionary for storage/display"""
        return {
            "instrument_key": self.instrument_key,
            "symbol": self.symbol,
            "side": self.side,
            "timeframe": self.timeframe,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "strength": self.strength,
            "strategy": self.strategy,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "reason": self.reason,
            "risk_reward": round(self.risk_reward_ratio, 2)
        }


# ============================================
# OPTION SELECTION RESULT
# ============================================

@dataclass
class OptionSelection:
    """
    Result of option selection process.
    
    Contains the selected option contract details and computed metrics.
    """
    instrument_key: str         # Option instrument key
    symbol: str                 # Underlying symbol
    option_type: str            # CE / PE
    strike: float
    expiry: str                 # YYYY-MM-DD
    ltp: float                  # Last traded price of option
    delta: float
    iv: float                   # Implied volatility
    theta: float
    gamma: float = 0.0
    vega: float = 0.0
    oi: int = 0                 # Open interest
    volume: int = 0
    stop_loss_pct: float = 20.0
    target_pct: float = 40.0
    rr: float = 2.0
    score: float = 0.0          # Selection score
    reason: str = ""
    
    @property
    def stop_loss_price(self) -> float:
        """Calculate option SL price"""
        return self.ltp * (1 - self.stop_loss_pct / 100)
    
    @property
    def target_price(self) -> float:
        """Calculate option target price"""
        return self.ltp * (1 + self.target_pct / 100)
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "instrument_key": self.instrument_key,
            "symbol": self.symbol,
            "option_type": self.option_type,
            "strike": self.strike,
            "expiry": self.expiry,
            "ltp": self.ltp,
            "delta": self.delta,
            "iv": self.iv,
            "theta": self.theta,
            "gamma": self.gamma,
            "vega": self.vega,
            "oi": self.oi,
            "volume": self.volume,
            "stop_loss_pct": self.stop_loss_pct,
            "target_pct": self.target_pct,
            "stop_loss_price": round(self.stop_loss_price, 2),
            "target_price": round(self.target_price, 2),
            "rr": self.rr,
            "score": self.score,
            "reason": self.reason
        }


# ============================================
# SELECTOR CONFIGURATION
# ============================================

@dataclass
class OptionSelectorConfig:
    """
    Configuration for option selection criteria.
    
    Attributes:
        min_delta: Minimum absolute delta (default 0.40)
        max_delta: Maximum absolute delta (default 0.65)
        max_iv_percentile: Max IV as percentile of history (default 0.70)
        min_oi: Minimum open interest for liquidity (default 100)
        min_volume: Minimum volume for liquidity (default 10)
        stop_loss_pct: Default SL % for options (default 20)
        target_pct: Default target % for options (default 40)
        min_rr: Minimum risk-reward ratio (default 2.0)
        allow_expiry_day: Allow trading on expiry day (default False)
        last_entry_time: Last time to enter trades (default "14:45")
        prefer_atm: Prefer ATM strikes over OTM (default True)
    """
    min_delta: float = 0.40
    max_delta: float = 0.65
    max_iv_percentile: float = 0.70
    min_oi: int = 100
    min_volume: int = 10
    stop_loss_pct: float = 20.0
    target_pct: float = 40.0
    min_rr: float = 2.0
    allow_expiry_day: bool = False
    last_entry_time: str = "14:45"
    prefer_atm: bool = True
    
    # Scoring weights
    weight_delta: float = 0.35
    weight_iv: float = 0.25
    weight_theta: float = 0.15
    weight_liquidity: float = 0.25


# ============================================
# OPTION SELECTOR
# ============================================

class OptionSelector:
    """
    Selects optimal options based on signal direction and Greek filters.
    
    Selection Process:
    1. Filter by option type (CE for LONG, PE for SHORT)
    2. Filter by strike proximity to spot (ATM ± 5 strikes)
    3. Filter by delta range
    4. Filter by liquidity (OI, volume)
    5. Rank by composite score (delta, IV, theta, liquidity)
    6. Validate R:R ratio
    
    Usage:
        config = OptionSelectorConfig(min_delta=0.45, max_delta=0.60)
        selector = OptionSelector(config)
        
        # From OptionChainProvider
        chain_df = pd.DataFrame(chain_dict['CE'] + chain_dict['PE'])
        
        selection = selector.select_option(signal, chain_df)
        if selection:
            print(f"Selected: {selection.strike} {selection.option_type}")
    """

    def __init__(self, config: OptionSelectorConfig = None):
        self.cfg = config or OptionSelectorConfig()

    def select_option(
        self,
        signal: UnderlyingSignal,
        option_chain: pd.DataFrame,
        iv_history: Optional[pd.Series] = None
    ) -> Optional[OptionSelection]:
        """
        Select the best option from the chain based on the signal.

        Args:
            signal: UnderlyingSignal with symbol, side, entry price, etc.
            option_chain: DataFrame with option data (from OptionChainProvider)
            iv_history: Optional historical IV for percentile filtering

        Returns:
            OptionSelection or None if no suitable option found
        """
        if option_chain is None or option_chain.empty:
            print(f"Empty option chain for {signal.symbol}")
            return None
        
        # Time check
        if not self._is_entry_time_valid(signal.timestamp):
            print(f"Entry time invalid for {signal.symbol}")
            return None

        # Step 1: Filter chain
        filtered = self._filter_chain(signal, option_chain)
        if filtered.empty:
            print(f"No options passed filters for {signal.symbol}")
            return None

        # Step 2: Rank candidates
        ranked = self._rank_candidates(filtered, iv_history)
        if ranked.empty:
            print(f"No options passed ranking for {signal.symbol}")
            return None

        # Step 3: Select best and validate R:R
        best = ranked.iloc[0]
        
        rr = self._calculate_rr(best["ltp"])
        if rr < self.cfg.min_rr:
            print(f"R:R {rr:.2f} below minimum {self.cfg.min_rr} for {signal.symbol}")
            return None

        # Build result
        return OptionSelection(
            instrument_key=best.get("instrument_key", ""),
            symbol=signal.symbol,
            option_type=best.get("option_type", "CE"),
            strike=float(best["strike"]),
            expiry=str(best.get("expiry", "")),
            ltp=float(best["ltp"]),
            delta=float(best["delta"]) if pd.notna(best.get("delta")) else 0.0,
            iv=float(best["iv"]) if pd.notna(best.get("iv")) else 0.0,
            theta=float(best["theta"]) if pd.notna(best.get("theta")) else 0.0,
            gamma=float(best["gamma"]) if pd.notna(best.get("gamma")) else 0.0,
            vega=float(best["vega"]) if pd.notna(best.get("vega")) else 0.0,
            oi=int(best["oi"]) if pd.notna(best.get("oi")) else 0,
            volume=int(best["volume"]) if pd.notna(best.get("volume")) else 0,
            stop_loss_pct=self.cfg.stop_loss_pct,
            target_pct=self.cfg.target_pct,
            rr=round(rr, 2),
            score=float(best.get("score", 0)),
            reason=best.get("reason", "Selected based on delta/IV/theta/liquidity ranking")
        )

    def select_multiple(
        self,
        signal: UnderlyingSignal,
        option_chain: pd.DataFrame,
        top_n: int = 3,
        iv_history: Optional[pd.Series] = None
    ) -> List[OptionSelection]:
        """
        Select top N options for comparison.
        
        Useful for displaying alternatives to the user.
        """
        if option_chain is None or option_chain.empty:
            return []
        
        filtered = self._filter_chain(signal, option_chain)
        if filtered.empty:
            return []
        
        ranked = self._rank_candidates(filtered, iv_history)
        
        results = []
        for _, row in ranked.head(top_n).iterrows():
            rr = self._calculate_rr(row["ltp"])
            
            results.append(OptionSelection(
                instrument_key=row.get("instrument_key", ""),
                symbol=signal.symbol,
                option_type=row.get("option_type", "CE"),
                strike=float(row["strike"]),
                expiry=str(row.get("expiry", "")),
                ltp=float(row["ltp"]),
                delta=float(row["delta"]) if pd.notna(row.get("delta")) else 0.0,
                iv=float(row["iv"]) if pd.notna(row.get("iv")) else 0.0,
                theta=float(row["theta"]) if pd.notna(row.get("theta")) else 0.0,
                gamma=float(row["gamma"]) if pd.notna(row.get("gamma")) else 0.0,
                vega=float(row["vega"]) if pd.notna(row.get("vega")) else 0.0,
                oi=int(row["oi"]) if pd.notna(row.get("oi")) else 0,
                volume=int(row["volume"]) if pd.notna(row.get("volume")) else 0,
                stop_loss_pct=self.cfg.stop_loss_pct,
                target_pct=self.cfg.target_pct,
                rr=round(rr, 2),
                score=float(row.get("score", 0)),
                reason=row.get("reason", "")
            ))
        
        return results

    # --------------------------------------------------
    # PRIVATE METHODS
    # --------------------------------------------------
    
    def _is_entry_time_valid(self, ts: datetime) -> bool:
        """Check if current time allows entry."""
        if ts is None:
            return True
        
        try:
            # Extract time component
            if hasattr(ts, 'time'):
                ts_time = ts.time()
            elif isinstance(ts, datetime):
                ts_time = ts.time()
            else:
                return True

            # Check against last entry time
            last_time = datetime.strptime(self.cfg.last_entry_time, "%H:%M").time()
            if ts_time > last_time:
                print(f"Time {ts_time} is after last entry time {last_time}")
                return False

            # Check expiry day (Thursday for weekly)
            if not self.cfg.allow_expiry_day:
                weekday = None
                if hasattr(ts, 'weekday'):
                    weekday = ts.weekday()
                elif hasattr(ts, 'date'):
                    weekday = ts.date().weekday()
                
                if weekday == 3:  # Thursday
                    print("Expiry day trading disabled")
                    return False

            return True
            
        except Exception as e:
            print(f"Time check error: {e}")
            return True  # Allow on error

    def _filter_chain(
        self,
        signal: UnderlyingSignal,
        chain: pd.DataFrame
    ) -> pd.DataFrame:
        """Filter option chain based on signal direction and criteria."""
        
        if chain.empty:
            return chain

        df = chain.copy()
        
        # Determine option type based on signal direction
        opt_type = "CE" if signal.side == "LONG" else "PE"
        
        # Filter by option type
        if "option_type" in df.columns:
            df = df[df["option_type"] == opt_type]
        
        if df.empty:
            print(f"No {opt_type} options in chain")
            return df

        # Filter by strike proximity to spot
        spot_price = signal.entry
        if spot_price and "strike" in df.columns:
            df["_dist"] = abs(df["strike"] - spot_price)
            df = df.sort_values("_dist").head(7)  # ATM ± 3 strikes

        # Filter by delta range
        if "delta" in df.columns and df["delta"].notna().any():
            delta_mask = (
                (df["delta"].abs() >= self.cfg.min_delta) &
                (df["delta"].abs() <= self.cfg.max_delta)
            )
            df_filtered = df[delta_mask]
            
            if not df_filtered.empty:
                df = df_filtered
            else:
                print(f"Delta filter ({self.cfg.min_delta}-{self.cfg.max_delta}) removed all options, relaxing...")

        # Filter by liquidity
        if "oi" in df.columns:
            df_liquid = df[df["oi"] >= self.cfg.min_oi]
            if not df_liquid.empty:
                df = df_liquid
        
        if "volume" in df.columns:
            df_liquid = df[df["volume"] >= self.cfg.min_volume]
            if not df_liquid.empty:
                df = df_liquid

        # Filter out zero LTP
        if "ltp" in df.columns:
            df = df[df["ltp"] > 0]

        return df

    def _rank_candidates(
        self,
        df: pd.DataFrame,
        iv_history: Optional[pd.Series]
    ) -> pd.DataFrame:
        """Rank options by composite score."""
        
        if df.empty:
            return df

        df = df.copy()

        # Optional: IV percentile filter
        if iv_history is not None and not iv_history.empty and "iv" in df.columns:
            iv_threshold = iv_history.quantile(self.cfg.max_iv_percentile)
            df_filtered = df[df["iv"] <= iv_threshold]
            if not df_filtered.empty:
                df = df_filtered

        if df.empty:
            return df

        # Calculate composite score
        score = pd.Series(0.0, index=df.index)

        # Delta score: prefer closer to 0.50 (ATM)
        if "delta" in df.columns and df["delta"].notna().any():
            # Score peaks at delta = 0.50
            delta_score = 1 - 2 * abs(df["delta"].abs() - 0.50)
            delta_score = delta_score.clip(0, 1)
            score += delta_score.fillna(0) * self.cfg.weight_delta

        # IV score: lower is better (normalized)
        if "iv" in df.columns and df["iv"].notna().any():
            iv_vals = df["iv"].fillna(df["iv"].median())
            iv_range = iv_vals.max() - iv_vals.min()
            if iv_range > 0:
                iv_score = 1 - (iv_vals - iv_vals.min()) / iv_range
            else:
                iv_score = 0.5
            score += iv_score * self.cfg.weight_iv

        # Theta score: less negative is better (normalized)
        if "theta" in df.columns and df["theta"].notna().any():
            theta_vals = df["theta"].fillna(0)
            theta_range = theta_vals.max() - theta_vals.min()
            if theta_range > 0:
                theta_score = (theta_vals - theta_vals.min()) / theta_range
            else:
                theta_score = 0.5
            score += theta_score * self.cfg.weight_theta

        # Liquidity score: higher OI + volume is better
        liquidity_score = pd.Series(0.0, index=df.index)
        
        if "oi" in df.columns and df["oi"].notna().any():
            oi_vals = df["oi"].fillna(0)
            if oi_vals.max() > 0:
                liquidity_score += (oi_vals / oi_vals.max()) * 0.6
        
        if "volume" in df.columns and df["volume"].notna().any():
            vol_vals = df["volume"].fillna(0)
            if vol_vals.max() > 0:
                liquidity_score += (vol_vals / vol_vals.max()) * 0.4
        
        score += liquidity_score * self.cfg.weight_liquidity

        df["score"] = score
        df["reason"] = df.apply(
            lambda r: self._build_reason(r), axis=1
        )

        # Clean up temp columns
        if "_dist" in df.columns:
            df = df.drop(columns=["_dist"])

        return df.sort_values("score", ascending=False)

    def _build_reason(self, row: pd.Series) -> str:
        """Build human-readable reason for selection."""
        parts = []
        
        if pd.notna(row.get("delta")):
            delta = abs(row["delta"])
            if 0.45 <= delta <= 0.55:
                parts.append("ATM delta")
            elif delta > 0.55:
                parts.append("ITM delta")
            else:
                parts.append("OTM delta")
        
        if pd.notna(row.get("iv")):
            parts.append(f"IV={row['iv']:.1f}%")
        
        if pd.notna(row.get("oi")) and row["oi"] > 1000:
            parts.append("Good liquidity")
        
        return ", ".join(parts) if parts else "Standard selection"

    def _calculate_rr(self, ltp: float) -> float:
        """Calculate risk-reward ratio for option position."""
        if ltp <= 0:
            return 0.0
        
        risk = ltp * self.cfg.stop_loss_pct / 100
        reward = ltp * self.cfg.target_pct / 100
        
        return reward / max(risk, 1e-6)


# ============================================
# UTILITY FUNCTIONS
# ============================================

def create_signal_from_dict(data: dict) -> UnderlyingSignal:
    """
    Create UnderlyingSignal from dictionary.
    
    Useful when loading from database or API response.
    """
    return UnderlyingSignal(
        instrument_key=data.get("instrument_key", ""),
        symbol=data.get("symbol", ""),
        side=data.get("side", data.get("direction", "LONG")),
        timeframe=data.get("timeframe", "15minute"),
        entry=float(data.get("entry", 0)),
        stop=float(data.get("stop", 0)),
        target=float(data.get("target", 0)),
        strength=float(data.get("strength", 0)),
        strategy=data.get("strategy", "UNKNOWN"),
        timestamp=pd.to_datetime(data.get("timestamp")) if data.get("timestamp") else datetime.now(),
        reason=data.get("reason", {})
    )


def load_signals_from_universe(date_filter: str = None) -> List[UnderlyingSignal]:
    """
    Load signals from tradable_universe table.
    
    Args:
        date_filter: Date string "YYYY-MM-DD" or None for today
    
    Returns:
        List of UnderlyingSignal objects
    """
    try:
        db = get_db()
        
        query = """
            SELECT 
                instrument_key,
                trading_symbol as symbol,
                direction as side,
                '15minute' as timeframe,
                current_price as entry,
                current_price * 0.98 as stop,
                current_price * 1.04 as target,
                0 as strength,
                recommended_strategy as strategy,
                generated_at as timestamp
            FROM tradable_universe
            WHERE valid_for_date = ?
              AND option_buy_ok = TRUE
        """
        
        date_val = date_filter or datetime.now().strftime("%Y-%m-%d")
        df = db.con.execute(query, [date_val]).df()
        
        signals = []
        for _, row in df.iterrows():
            try:
                signals.append(create_signal_from_dict(row.to_dict()))
            except Exception as e:
                print(f"Error creating signal: {e}")
        
        return signals
        
    except Exception as e:
        print(f"Error loading signals from universe: {e}")
        return []


def get_lot_size(symbol: str) -> int:
    """Get lot size for a symbol from fo_stocks_master."""
    try:
        db = get_db()
        result = db.con.execute("""
            SELECT lot_size FROM fo_stocks_master 
            WHERE trading_symbol = ? 
            LIMIT 1
        """, [symbol.upper()]).fetchone()
        return result[0] if result else 1
    except Exception:
        return 1


def calculate_position_size(
    capital: float,
    option_ltp: float,
    lot_size: int,
    max_risk_pct: float = 2.0
) -> int:
    """
    Calculate number of lots based on capital and risk.
    
    Args:
        capital: Available capital
        option_ltp: Option premium
        lot_size: Contract lot size
        max_risk_pct: Maximum risk as % of capital
    
    Returns:
        Number of lots to trade
    """
    if option_ltp <= 0 or lot_size <= 0:
        return 0
    
    cost_per_lot = option_ltp * lot_size
    max_investment = capital * (max_risk_pct / 100) * 5  # Assume 20% SL = 5x
    
    lots = int(max_investment / cost_per_lot)
    return max(1, lots)