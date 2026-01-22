# 🎯 Indian Market Squeeze → Options Trading Pipeline

## Overview
Complete pipeline from 15m Squeeze signals to options trading with Greeks-based analysis.

---

## 📊 Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SIGNAL GENERATION (Tab 2)                        │
│                                                                       │
│  15m Squeeze Scanner → Score 5 Signals (TRADABLE NOW)                │
│                      ↓                                                │
│              Signal Details:                                          │
│              • Symbol: RELIANCE                                       │
│              • Type: LONG/SHORT                                       │
│              • Entry: 2,450                                           │
│              • SL: 2,430 (ATR-based or %)                            │
│              • TP: 2,490 (Risk:Reward 1:2)                           │
│              • Score: 5/5                                             │
│              • Time: 13:15                                            │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│                  OPTION SELECTION ENGINE (New)                        │
│                                                                       │
│  1. Fetch Option Chain from Upstox API                               │
│     - Get all strikes for nearest weekly expiry                      │
│     - Filter by liquidity (OI > threshold)                           │
│                                                                       │
│  2. Calculate Greeks using vollib/py_vollib                          │
│     - Delta: Directional sensitivity                                 │
│     - Gamma: Delta acceleration                                      │
│     - Theta: Time decay                                              │
│     - Vega: Volatility sensitivity                                   │
│     - IV: Implied Volatility                                         │
│                                                                       │
│  3. Strike Selection Logic                                           │
│     LONG Signal → Buy CALL                                           │
│     - ATM or slightly OTM (0-2 strikes)                              │
│     - High Delta (0.5-0.7)                                           │
│     - Reasonable Theta (avoid deep ITM)                              │
│     - Good liquidity (bid-ask spread < 5%)                           │
│                                                                       │
│     SHORT Signal → Buy PUT                                           │
│     - ATM or slightly OTM (0-2 strikes)                              │
│     - High Delta (-0.5 to -0.7)                                      │
│     - Same criteria as CALL                                          │
│                                                                       │
│  4. Position Sizing                                                  │
│     - Capital allocation: 2-5% per trade                             │
│     - Lot size calculation based on premium                          │
│     - Max positions: 3-5 concurrent                                  │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│                     OPTIONS TRADING UI (Tab 5)                        │
│                                                                       │
│  🟢 ACTIVE SIGNALS → OPTIONS RECOMMENDATIONS                          │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ RELIANCE - LONG Signal (Score: 5, Time: 13:15)              │   │
│  │                                                               │   │
│  │ 📊 Underlying: ₹2,450 | SL: ₹2,430 | TP: ₹2,490            │   │
│  │                                                               │   │
│  │ 🎯 RECOMMENDED OPTIONS:                                       │   │
│  │                                                               │   │
│  │ Option 1: 2450 CE (ATM) ✅ BEST                              │   │
│  │ • Premium: ₹65                                                │   │
│  │ • Delta: 0.52                                                 │   │
│  │ • IV: 18.5%                                                   │   │
│  │ • Liquidity: Good (OI: 45,000)                               │   │
│  │ • Risk: ₹4,225 (1 lot × ₹65)                                │   │
│  │ • Potential: ₹8,450 (130% ROI)                              │   │
│  │ [📈 Trade Now] [📊 View Chain] [⏰ Set Alert]               │   │
│  │                                                               │   │
│  │ Option 2: 2500 CE (OTM)                                       │   │
│  │ • Premium: ₹32                                                │   │
│  │ • Delta: 0.35                                                 │   │
│  │ • Higher leverage, higher risk                               │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│                     TRADE EXECUTION (Manual/Auto)                     │
│                                                                       │
│  Manual Mode (Default):                                              │
│  1. User reviews recommendation                                      │
│  2. User clicks "Trade Now"                                          │
│  3. Confirm dialog with all details                                  │
│  4. Execute via Upstox API                                           │
│                                                                       │
│  Paper Trading Mode:                                                 │
│  1. Simulated fills at market price                                  │
│  2. Track P&L in real-time                                           │
│  3. Historical performance tracking                                  │
│                                                                       │
│  Auto Mode (Advanced):                                               │
│  1. Auto-execute when Score=5 signal appears                         │
│  2. Risk checks before execution                                     │
│  3. Position limits enforced                                         │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│                     POSITION MANAGEMENT                               │
│                                                                       │
│  🎯 ACTIVE POSITIONS (Real-time tracking)                            │
│                                                                       │
│  RELIANCE 2450 CE (Bought @ ₹65)                                    │
│  • Entry Time: 13:15                                                 │
│  • Current Premium: ₹78 (+20%)                                       │
│  • Unrealized P&L: +₹845 per lot                                    │
│  • Underlying: ₹2,470 (moving towards TP ₹2,490)                    │
│  • Exit Rules:                                                        │
│    ✓ TP Hit: Underlying reaches ₹2,490 → Exit 100%                 │
│    ✓ SL Hit: Underlying breaches ₹2,430 → Exit 100%                │
│    ✓ Time Stop: New 15m candle closes against signal → Exit          │
│    ✓ Trailing SL: When 50% profit, trail SL to breakeven           │
│                                                                       │
│  [🔴 Exit Position] [📊 View Greeks] [📈 Adjust SL/TP]             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 🔧 Technical Implementation

### 1. Signal Adapter Module
**File:** `core/signal_to_options.py`

```python
@dataclass
class SqueezeToOptionSignal:
    """Converts Squeeze signal to UnderlyingSignal format"""

    @staticmethod
    def convert(squeeze_signal: SqueezeSignal, symbol: str, instrument_key: str) -> UnderlyingSignal:
        """
        Convert SqueezeSignal to UnderlyingSignal

        Squeeze Signal has:
        - signal_type: LONG/SHORT
        - entry_price, sl_price, tp_price
        - score (4 or 5)
        - timestamp
        - reasons (list of why signal fired)

        Maps to:
        - UnderlyingSignal with strength=score*20 (0-100 scale)
        - strategy="SQUEEZE_15M"
        """
        return UnderlyingSignal(
            instrument_key=instrument_key,
            symbol=symbol,
            side=squeeze_signal.signal_type,
            timeframe="15minute",
            entry=squeeze_signal.entry_price,
            stop=squeeze_signal.sl_price,
            target=squeeze_signal.tp_price,
            strength=squeeze_signal.score * 20,  # 5 → 100, 4 → 80
            strategy="SQUEEZE_15M",
            timestamp=squeeze_signal.timestamp,
            reason={
                "score": squeeze_signal.score,
                "reasons": squeeze_signal.reasons,
                "status": squeeze_signal.status
            }
        )
```

### 2. Greeks Calculator (using vollib)
**File:** `core/greeks_calculator.py`

```python
from py_vollib.black_scholes import black_scholes as bs
from py_vollib.black_scholes.greeks import analytical as greeks
import numpy as np
from datetime import datetime

class GreeksCalculator:
    """
    Calculate option Greeks using Black-Scholes model

    Uses py_vollib library (pure Python implementation of vollib)
    GitHub: https://github.com/vollib/py_vollib
    """

    def __init__(self, risk_free_rate=0.065):
        self.risk_free_rate = risk_free_rate

    def calculate_greeks(
        self,
        spot_price: float,
        strike_price: float,
        time_to_expiry_days: float,
        implied_volatility: float,
        option_type: str  # 'c' for call, 'p' for put
    ) -> dict:
        """
        Calculate all Greeks for an option

        Returns:
            {
                'price': theoretical_price,
                'delta': delta,
                'gamma': gamma,
                'theta': theta,
                'vega': vega,
                'rho': rho
            }
        """
        # Convert days to years
        T = time_to_expiry_days / 365.0

        # Ensure inputs are valid
        if T <= 0:
            return self._zero_greeks()

        S = spot_price
        K = strike_price
        r = self.risk_free_rate
        sigma = implied_volatility
        flag = option_type.lower()

        try:
            # Theoretical price
            price = bs(flag, S, K, T, r, sigma)

            # Greeks
            delta = greeks.delta(flag, S, K, T, r, sigma)
            gamma = greeks.gamma(flag, S, K, T, r, sigma)
            theta = greeks.theta(flag, S, K, T, r, sigma)
            vega = greeks.vega(flag, S, K, T, r, sigma)
            rho = greeks.rho(flag, S, K, T, r, sigma)

            return {
                'price': price,
                'delta': delta,
                'gamma': gamma,
                'theta': theta / 365,  # Daily theta
                'vega': vega / 100,    # Vega per 1% vol change
                'rho': rho / 100,      # Rho per 1% rate change
                'iv': sigma * 100      # IV as percentage
            }
        except Exception as e:
            print(f"Error calculating Greeks: {e}")
            return self._zero_greeks()

    def _zero_greeks(self):
        """Return zero Greeks when calculation fails"""
        return {
            'price': 0,
            'delta': 0,
            'gamma': 0,
            'theta': 0,
            'vega': 0,
            'rho': 0,
            'iv': 0
        }

    def calculate_implied_volatility(
        self,
        market_price: float,
        spot_price: float,
        strike_price: float,
        time_to_expiry_days: float,
        option_type: str
    ) -> float:
        """
        Calculate IV from market price using Newton-Raphson

        Returns:
            Implied volatility as decimal (e.g., 0.25 for 25%)
        """
        from py_vollib.black_scholes.implied_volatility import implied_volatility as iv_calc

        T = time_to_expiry_days / 365.0
        flag = option_type.lower()

        try:
            iv = iv_calc(
                price=market_price,
                S=spot_price,
                K=strike_price,
                t=T,
                r=self.risk_free_rate,
                flag=flag
            )
            return iv
        except:
            # If IV calculation fails, return default
            return 0.25  # 25% default
```

### 3. Option Recommendation Engine
**File:** `core/option_recommender.py`

```python
from typing import List, Tuple, Optional
from dataclasses import dataclass
import pandas as pd
from core.greeks_calculator import GreeksCalculator
from core.option_chain_provider import OptionChainProvider
from core.option_selector import UnderlyingSignal

@dataclass
class OptionRecommendation:
    """Single option recommendation with all details"""
    symbol: str
    strike: float
    option_type: str  # CE or PE
    expiry_date: str
    premium: float
    lot_size: int

    # Greeks
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float

    # Liquidity
    oi: int
    volume: int
    bid_ask_spread_pct: float

    # Position sizing
    capital_required: float
    potential_return: float
    potential_return_pct: float

    # Ranking
    rank_score: float  # 0-100, higher is better
    rank_reason: str

class OptionRecommender:
    """
    Recommend best options for underlying signals
    """

    def __init__(self):
        self.greeks_calc = GreeksCalculator()
        self.chain_provider = OptionChainProvider()

    def recommend_for_signal(
        self,
        signal: UnderlyingSignal,
        max_recommendations: int = 3,
        capital_per_trade: float = 50000
    ) -> List[OptionRecommendation]:
        """
        Find best option contracts for a signal

        Args:
            signal: UnderlyingSignal from squeeze scanner
            max_recommendations: Number of options to return
            capital_per_trade: Max capital to allocate

        Returns:
            List of OptionRecommendation sorted by rank_score
        """
        # 1. Fetch option chain
        chain = self.chain_provider.get_option_chain(
            symbol=signal.symbol,
            expiry_type='weekly'  # Prefer weekly for 15m signals
        )

        if chain is None or chain.empty:
            return []

        # 2. Filter chain
        option_type = 'CE' if signal.side == 'LONG' else 'PE'
        filtered = self._filter_chain(chain, signal, option_type)

        # 3. Calculate Greeks for each option
        recommendations = []
        for _, opt in filtered.iterrows():
            greeks = self._calculate_option_greeks(opt, signal)

            # 4. Rank option
            rank_score, rank_reason = self._rank_option(
                opt, greeks, signal, capital_per_trade
            )

            # 5. Create recommendation
            rec = self._create_recommendation(
                opt, greeks, signal, rank_score, rank_reason, capital_per_trade
            )
            recommendations.append(rec)

        # 6. Sort by rank and return top N
        recommendations.sort(key=lambda x: x.rank_score, reverse=True)
        return recommendations[:max_recommendations]

    def _filter_chain(
        self,
        chain: pd.DataFrame,
        signal: UnderlyingSignal,
        option_type: str
    ) -> pd.DataFrame:
        """
        Filter option chain to relevant strikes

        Criteria:
        - Option type (CE/PE based on signal direction)
        - Strike range: ATM ± 5 strikes
        - Minimum OI (liquidity)
        - Reasonable bid-ask spread
        """
        # Filter by option type
        chain = chain[chain['option_type'] == option_type].copy()

        # Find ATM strike
        atm_strike = self._find_atm_strike(chain, signal.entry)

        # Filter to ATM ± 5 strikes
        strike_range = 5
        min_strike = atm_strike - (strike_range * self._get_strike_gap(chain))
        max_strike = atm_strike + (strike_range * self._get_strike_gap(chain))
        chain = chain[
            (chain['strike'] >= min_strike) &
            (chain['strike'] <= max_strike)
        ]

        # Filter by minimum liquidity
        min_oi = 1000
        chain = chain[chain['oi'] >= min_oi]

        # Filter by bid-ask spread (< 10%)
        chain = chain[
            ((chain['ask'] - chain['bid']) / chain['ltp'] * 100) < 10
        ]

        return chain

    def _calculate_option_greeks(self, option_row, signal) -> dict:
        """Calculate Greeks for single option"""
        days_to_expiry = self._calculate_days_to_expiry(option_row['expiry'])

        return self.greeks_calc.calculate_greeks(
            spot_price=signal.entry,
            strike_price=option_row['strike'],
            time_to_expiry_days=days_to_expiry,
            implied_volatility=option_row.get('iv', 0.25),  # From chain or default
            option_type='c' if option_row['option_type'] == 'CE' else 'p'
        )

    def _rank_option(
        self,
        option_row,
        greeks: dict,
        signal: UnderlyingSignal,
        capital: float
    ) -> Tuple[float, str]:
        """
        Rank option based on multiple criteria

        Scoring (0-100):
        - Delta appropriateness (30 points)
        - Liquidity (20 points)
        - Theta efficiency (20 points)
        - Capital efficiency (15 points)
        - IV level (15 points)
        """
        score = 0
        reasons = []

        # 1. Delta score (prefer 0.5-0.7 absolute)
        abs_delta = abs(greeks['delta'])
        if 0.5 <= abs_delta <= 0.7:
            delta_score = 30
            reasons.append("Optimal delta")
        elif 0.4 <= abs_delta < 0.5 or 0.7 < abs_delta <= 0.8:
            delta_score = 20
            reasons.append("Good delta")
        else:
            delta_score = 10
            reasons.append("Suboptimal delta")
        score += delta_score

        # 2. Liquidity score (OI + Volume)
        oi = option_row['oi']
        volume = option_row['volume']
        if oi > 50000 and volume > 1000:
            liq_score = 20
            reasons.append("Excellent liquidity")
        elif oi > 10000 and volume > 500:
            liq_score = 15
            reasons.append("Good liquidity")
        else:
            liq_score = 10
            reasons.append("Moderate liquidity")
        score += liq_score

        # 3. Theta efficiency (lower theta decay better for short-term)
        daily_theta_pct = abs(greeks['theta']) / option_row['ltp'] * 100
        if daily_theta_pct < 1:
            theta_score = 20
            reasons.append("Low theta decay")
        elif daily_theta_pct < 2:
            theta_score = 15
            reasons.append("Moderate theta")
        else:
            theta_score = 5
            reasons.append("High theta decay")
        score += theta_score

        # 4. Capital efficiency
        lots_possible = capital / (option_row['ltp'] * option_row['lot_size'])
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

        # 5. IV level (prefer reasonable IV, not too high/low)
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

        return score, ", ".join(reasons)

    # ... helper methods for ATM strike, strike gap, days to expiry, etc.
```

### 4. UI Integration (Tab 5 - Options Trading)
**File:** `pages/5_Options_Trading.py` (enhanced version)

```python
# Add to Tab 5 UI

def show_squeeze_signals_to_options():
    """Show active squeeze signals with option recommendations"""

    st.markdown("## 🎯 Active Squeeze Signals → Options")

    # Get active signals from session state
    tradable_signals = st.session_state.get("sq_live_tradable")

    if tradable_signals is None or tradable_signals.empty:
        st.info("No active signals. Go to Live Scanner tab to scan for signals.")
        return

    # Initialize recommender
    recommender = OptionRecommender()

    # Process each signal
    for idx, row in tradable_signals.iterrows():
        with st.expander(
            f"🎯 {row['Symbol']} - {row['Signal']} (Score: {row['Score']}, Time: {row['Time']})",
            expanded=True
        ):
            col1, col2 = st.columns([1, 2])

            with col1:
                st.markdown("### 📊 Underlying Signal")
                st.metric("Entry", f"₹{row['Entry']}")
                st.metric("Stop Loss", f"₹{row['SL']}")
                st.metric("Target", f"₹{row['TP']}")
                st.metric("Risk:Reward", f"1:{row.get('RR', 2.0)}")

            with col2:
                # Convert to UnderlyingSignal
                signal = SqueezeToOptionSignal.convert_from_dataframe_row(row)

                # Get recommendations
                with st.spinner("Analyzing options..."):
                    recommendations = recommender.recommend_for_signal(
                        signal=signal,
                        max_recommendations=3,
                        capital_per_trade=st.session_state.get('option_capital', 50000)
                    )

                if not recommendations:
                    st.warning("No suitable options found")
                    continue

                st.markdown("### 🎯 Recommended Options")

                for i, rec in enumerate(recommendations):
                    rank_emoji = "✅" if i == 0 else "⭐" if i == 1 else "📌"

                    with st.container():
                        st.markdown(f"#### {rank_emoji} Option {i+1}: {rec.strike} {rec.option_type}")

                        col_a, col_b, col_c, col_d = st.columns(4)
                        col_a.metric("Premium", f"₹{rec.premium:.2f}")
                        col_b.metric("Delta", f"{rec.delta:.2f}")
                        col_c.metric("IV", f"{rec.iv:.1f}%")
                        col_d.metric("Score", f"{rec.rank_score:.0f}/100")

                        col_e, col_f, col_g, col_h = st.columns(4)
                        col_e.metric("Capital Req", f"₹{rec.capital_required:,.0f}")
                        col_f.metric("Potential Return", f"₹{rec.potential_return:,.0f}")
                        col_g.metric("ROI", f"{rec.potential_return_pct:.0f}%")
                        col_h.metric("OI", f"{rec.oi:,}")

                        st.caption(f"💡 {rec.rank_reason}")

                        # Action buttons
                        btn_col1, btn_col2, btn_col3 = st.columns(3)
                        if btn_col1.button(f"📈 Trade Now", key=f"trade_{idx}_{i}"):
                            execute_option_trade(rec, signal)
                        if btn_col2.button(f"📊 View Chain", key=f"chain_{idx}_{i}"):
                            show_full_option_chain(signal.symbol)
                        if btn_col3.button(f"⏰ Set Alert", key=f"alert_{idx}_{i}"):
                            create_price_alert(rec)

                        st.divider()
```

---

## 📈 Exit Rules for Options

### Rule 1: Underlying TP/SL Hit
- **TP Hit**: Exit 100% of position
- **SL Hit**: Exit 100% of position immediately

### Rule 2: Time-Based Exit
- **New 15m candle closes against signal**: Exit position
- **End of day**: Close all positions 10 minutes before market close

### Rule 3: Profit-Based Trailing
- **50% profit**: Move SL to breakeven (entry premium)
- **100% profit**: Book 50%, trail remaining 50% with 20% trailing SL

### Rule 4: Theta Protection
- **Daily theta exceeds 5% of premium**: Consider early exit if signal weakens

---

## 🗄️ Database Schema

### New Table: `squeeze_option_trades`
```sql
CREATE TABLE squeeze_option_trades (
    trade_id INTEGER PRIMARY KEY,

    -- Signal Info
    signal_timestamp TIMESTAMP,
    underlying_symbol VARCHAR(20),
    signal_type VARCHAR(10),  -- LONG/SHORT
    signal_score INTEGER,

    -- Underlying Levels
    entry_price DECIMAL(10,2),
    sl_price DECIMAL(10,2),
    tp_price DECIMAL(10,2),

    -- Option Details
    option_symbol VARCHAR(50),
    strike DECIMAL(10,2),
    option_type VARCHAR(2),  -- CE/PE
    expiry_date DATE,

    -- Trade Execution
    entry_premium DECIMAL(10,2),
    entry_time TIMESTAMP,
    lots INTEGER,
    lot_size INTEGER,
    total_capital DECIMAL(12,2),

    -- Greeks at Entry
    entry_delta DECIMAL(6,4),
    entry_iv DECIMAL(6,4),

    -- Exit
    exit_premium DECIMAL(10,2),
    exit_time TIMESTAMP,
    exit_reason VARCHAR(50),  -- TP_HIT, SL_HIT, TIME_STOP, etc.

    -- P&L
    gross_pnl DECIMAL(12,2),
    net_pnl DECIMAL(12,2),  -- After brokerage
    pnl_pct DECIMAL(8,2),

    -- Status
    status VARCHAR(20),  -- OPEN, CLOSED

    FOREIGN KEY (underlying_symbol) REFERENCES instruments(trading_symbol)
);
```

---

## 🎨 UI Mockup (Tab 5)

```
┌──────────────────────────────────────────────────────────────────────┐
│ 🎯 INDIAN MARKET SQUEEZE → OPTIONS TRADING                           │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│ ⚙️ Settings:                                                         │
│   Capital per Trade: [₹50,000]  Max Positions: [3]                  │
│   Auto-Execute: [ ] OFF          Paper Trading: [✓] ON              │
│                                                                       │
├──────────────────────────────────────────────────────────────────────┤
│ 📊 ACTIVE SIGNALS (3)                                                │
│                                                                       │
│ ▼ RELIANCE - LONG (Score: 5, 13:15) ─────────────────────────────── │
│   │ Underlying: ₹2,450 | SL: ₹2,430 | TP: ₹2,490                   │
│   │                                                                   │
│   │ 🎯 RECOMMENDED OPTIONS:                                          │
│   │                                                                   │
│   │ ✅ Option 1: 2450 CE (ATM) - Score: 87/100                      │
│   │   Premium: ₹65 | Delta: 0.52 | IV: 18.5% | OI: 45,000          │
│   │   Capital: ₹4,225 | Potential: ₹8,450 (130% ROI)               │
│   │   💡 Optimal delta, Excellent liquidity, Low theta              │
│   │   [📈 Trade Now] [📊 View Chain] [⏰ Set Alert]                │
│   │                                                                   │
│   │ ⭐ Option 2: 2500 CE (OTM) - Score: 72/100                      │
│   │   Premium: ₹32 | Delta: 0.35 | IV: 20.2%                        │
│   │   Higher leverage, lower success probability                     │
│   └─────────────────────────────────────────────────────────────────│
│                                                                       │
│ ▼ TATASTEEL - SHORT (Score: 5, 13:30) ──────────────────────────── │
│   │ ... (similar layout)                                             │
│   └─────────────────────────────────────────────────────────────────│
│                                                                       │
├──────────────────────────────────────────────────────────────────────┤
│ 🟢 OPEN POSITIONS (2)                                                │
│                                                                       │
│ RELIANCE 2450 CE | Entry: ₹65 @ 13:17 | Current: ₹78 (+20%)        │
│ P&L: +₹845 | Underlying: ₹2,470 → TP: ₹2,490                       │
│ [🔴 Exit] [📊 Greeks] [📈 Adjust]                                  │
│                                                                       │
├──────────────────────────────────────────────────────────────────────┤
│ 📈 TODAY'S PERFORMANCE                                               │
│   Trades: 5 | Win: 3 (60%) | Total P&L: +₹12,450 (+24.9%)          │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Implementation Phases

### Phase 1: Foundation (Week 1)
- [x] Install py_vollib: `pip install py_vollib`
- [ ] Create `GreeksCalculator` class
- [ ] Create `SqueezeToOptionSignal` adapter
- [ ] Test Greeks calculation with sample data

### Phase 2: Recommendation Engine (Week 2)
- [ ] Build `OptionRecommender` class
- [ ] Implement strike filtering logic
- [ ] Implement ranking algorithm
- [ ] Test with historical squeeze signals

### Phase 3: UI Integration (Week 3)
- [ ] Design Tab 5 UI layout
- [ ] Connect squeeze signals to options
- [ ] Display recommendations with Greeks
- [ ] Add "Trade Now" button (paper trading first)

### Phase 4: Trade Execution (Week 4)
- [ ] Implement paper trading mode
- [ ] Build position tracking
- [ ] Implement exit rules monitoring
- [ ] Add P&L tracking

### Phase 5: Live Trading (Week 5+)
- [ ] Connect to Upstox order API
- [ ] Add risk checks
- [ ] Implement auto-execution (optional)
- [ ] Add performance analytics

---

## 📚 Dependencies to Add

```bash
pip install py_vollib          # Greeks calculation
pip install scipy              # Statistical functions (already have)
pip install pandas numpy       # Already installed
```

---

## ⚠️ Risk Management Rules

1. **Max Capital per Trade**: 2-5% of total capital
2. **Max Open Positions**: 3-5 concurrent
3. **Max Loss per Day**: 10% of capital → STOP trading
4. **Position Sizing**: Based on premium, not underlying price
5. **Greeks Limits**:
   - Minimum Delta: 0.30 (avoid deep OTM)
   - Maximum Theta: 5% daily decay
   - Prefer IV percentile: 20-80 (avoid extremes)

---

## 📊 Expected Performance Metrics

**Backtesting Assumptions** (to be validated):
- Win rate: 60-70% (aligned with squeeze signal accuracy)
- Avg win: 50-100% (options leverage)
- Avg loss: -30 to -50% (SL hit)
- Risk:Reward: 1:1.5 to 1:2

**Greeks-based selection should improve**:
- Better entry timing (delta)
- Lower theta decay (time efficiency)
- Better IV selection (avoid overpaying)

---

## 🎯 Next Steps

1. **Install py_vollib**: `pip install py_vollib`
2. **Create `core/greeks_calculator.py`** with GreeksCalculator class
3. **Create `core/signal_to_options.py`** with adapter
4. **Create `core/option_recommender.py`** with recommendation engine
5. **Enhance `pages/5_Options_Trading.py`** with squeeze integration
6. **Test with paper trading first**
7. **Validate with historical data**
8. **Go live with small position sizes**

---

## 📞 Questions to Answer

1. **Capital Allocation**: How much capital per trade? (Recommend: ₹25,000-₹50,000)
2. **Expiry Preference**: Weekly or Monthly? (Recommend: Weekly for 15m signals)
3. **Strike Preference**: ATM or OTM? (Recommend: ATM to 1-strike OTM)
4. **Auto-Execution**: Manual review or auto-trade? (Recommend: Manual initially)
5. **Exit Strategy**: Strict TP/SL or trailing? (Recommend: Hybrid - trail after 50% profit)

---

**Author**: Trading Bot Pro
**Version**: 1.0
**Date**: 2026-01-17
**Strategy**: Indian Market Squeeze (15m) → Options Trading Pipeline
