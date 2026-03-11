# Nifty & Banknifty Options Analysis Dashboard - Implementation Plan

**Project:** PixityAI Trading Platform  
**Document Type:** Implementation Plan  
**Created:** 2026-03-02  
**Status:** Pending Approval  

---

## 1. Executive Summary

Build a real-time options analysis dashboard for **Nifty 50** and **Banknifty** indices featuring:
- **Max Pain Calculator** — Strike with maximum option buyer pain
- **Open Interest (OI) Analysis** — CE/PE OI by strike, changes, and buildup patterns
- **Put-Call Ratio (PCR)** — Overall and by-strike PCR with sentiment interpretation
- **Implied Volatility (IV) Charts** — IV smile/skew visualization
- **Options Greeks** — Delta, Gamma, Theta, Vega, Rho per strike
- **Volume Analysis** — Options volume vs. OI for liquidity assessment

**Data Source:** Upstox V3 API (real-time option chain)  
**Update Frequency:** 5-second snapshots  
**Expiry:** Weekly expiries only  
**Layout:** Single page with tabs (Nifty | Banknifty)

---

## 2. Existing Infrastructure Audit

### 2.1 What Already Exists ✅

| Component | File | Status | Reusable |
|-----------|------|--------|----------|
| **Option Data Model** | `core/instruments/option.py` | Complete | ✅ Yes |
| **Instrument Master** | `core/instruments/instrument_db.py` | Complete | ✅ Yes |
| **Options Contract Selector** | `core/execution/options/selector.py` | Complete | ✅ Yes |
| **Greeks Model** | `core/risk/greeks/greeks_model.py` | Complete | ✅ Yes |
| **Greeks Calculator** | `core/risk/greeks/greeks_calculator.py` | Complete | ✅ Yes |
| **Black-76 Engine** | `core/risk/greeks/black76_engine.py` | Complete | ✅ Yes |
| **Upstox Market Data** | `core/brokers/upstox_market_data.py` | Complete | ✅ Yes (LTP fetch) |
| **Credential Manager** | `core/auth/credentials.py` | Complete | ✅ Yes |
| **ZMQ Publisher** | `core/zmq/telemetry_publisher.py` | Complete | ✅ Extend |
| **Flask App Factory** | `flask_app/__init__.py` | Complete | ✅ Yes |
| **Dashboard Template** | `flask_app/templates/dashboard.html` | Complete | ✅ Reference |

### 2.2 What Needs to Be Built 🆕

| Component | File | Priority | Complexity |
|-----------|------|----------|------------|
| **Options Provider** | `core/data/options_provider.py` | P0 | Medium |
| **Options Analytics Engine** | `core/analytics/options_analytics.py` | P0 | Medium |
| **Options Facade** | `app_facade/options_facade.py` | P0 | Low |
| **DB Schema (Options Cache)** | `core/data/schema.py` | P0 | Low |
| **Flask Blueprint** | `flask_app/blueprints/options/` | P0 | Medium |
| **Dashboard Template** | `flask_app/templates/options/analysis.html` | P0 | High |
| **JavaScript Charts** | `flask_app/static/js/options_dashboard.js` | P0 | High |
| **Options Publisher** | `core/zmq/options_publisher.py` | P1 | Low |
| **Navigation Link** | `flask_app/templates/layout.html` | P1 | Trivial |

### 2.3 Key Findings

1. **Option Chain Fetch:** No existing code fetches full option chain from Upstox V3
   - Need to implement: `GET /v3/option-chain` endpoint wrapper
   
2. **Max Pain:** No existing implementation
   - Need to build from scratch using OI data
   
3. **PCR:** No existing implementation
   - Simple calculation: `PCR = Total PE OI / Total CE OI`
   
4. **Greeks:** Already implemented (Black-76 engine)
   - Can reuse for IV/Greeks display
   
5. **Instrument Resolution:** Already works via `InstrumentMaster.resolve_option()`
   - Handles symbol format mismatches correctly

---

## 3. Architecture

### 3.1 High-Level Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                    Flask Route: /options/analysis                │
│                    (flask_app/blueprints/options/routes.py)      │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                    OptionsFacade                                 │
│                    (app_facade/options_facade.py)                │
│  - get_option_chain(index: "NIFTY" | "BANKNIFTY")               │
│  - calculate_max_pain(option_chain)                             │
│  - calculate_pcr(option_chain)                                  │
│  - get_oi_changes(option_chain)                                 │
│  - get_greeks(strike, iv, underlying_price)                     │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                    OptionsProvider                               │
│                    (core/data/options_provider.py)               │
│  - fetch_option_chain_from_upstox(index, expiry)                │
│  - cache_option_chain(snapshot)                                 │
│  - get_cached_option_chain(index, expiry)                       │
│  - get_historical_oi(index, expiry, date_range)                 │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Upstox V3 API                                 │
│  GET /v3/option-chain?instrument_key=NSE_INDEX|Nifty50          │
│  GET /v2/market-quote/quotes?instrument_key=NSE_FO|54710,...    │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 Data Flow (5-Second Update Cycle)

```
┌─────────────┐      ┌──────────────┐      ┌──────────────┐
│  Upstox V3  │─────▶│ Options      │─────▶│ DuckDB       │
│  API        │      │ Provider     │      │ (cache)      │
└─────────────┘      └──────────────┘      └──────┬───────┘
                                                   │
                                                   ▼
                                          ┌──────────────┐
                                          │ Options      │
                                          │ Analytics    │
                                          └──────┬───────┘
                                                   │
                                                   ▼
                                          ┌──────────────┐
                                          │ ZMQ          │
                                          │ Publisher    │
                                          └──────┬───────┘
                                                   │
                                                   ▼
                                          ┌──────────────┐
                                          │ Flask        │
                                          │ (SSE → JS)   │
                                          └──────┬───────┘
                                                   │
                                                   ▼
                                          ┌──────────────┐
                                          │ Browser      │
                                          │ (Chart.js)   │
                                          └──────────────┘
```

---

## 4. Database Schema

### 4.1 New Tables

```sql
-- Option chain snapshots (for historical analysis and trend)
CREATE TABLE IF NOT EXISTS option_chain_snapshot (
    snapshot_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    underlying_symbol  TEXT NOT NULL,  -- "NSE_INDEX|Nifty 50" or "NSE_INDEX|Nifty Bank"
    expiry_date        TEXT NOT NULL,  -- YYYY-MM-DD format
    strike_price       REAL NOT NULL,
    option_type        TEXT NOT NULL,  -- 'CE' or 'PE'
    instrument_key     TEXT NOT NULL,  -- Upstox key: "NSE_FO|54710"
    tradingsymbol      TEXT NOT NULL,  -- Human-readable: "NIFTY04MAR2622500CE"
    
    -- Price data
    ltp                REAL,           -- Last traded price
    open               REAL,
    high               REAL,
    low                REAL,
    close              REAL,
    
    -- OI data
    oi                 INTEGER DEFAULT 0,
    oi_change          INTEGER DEFAULT 0,
    oi_change_pct      REAL DEFAULT 0.0,
    volume             INTEGER DEFAULT 0,
    
    -- Greeks (calculated)
    iv                 REAL,           -- Implied volatility (decimal: 0.14 = 14%)
    delta              REAL,
    gamma              REAL,
    theta              REAL,
    vega               REAL,
    rho                REAL,
    
    -- Metadata
    lot_size           INTEGER DEFAULT 75,
    underlying_ltp     REAL            -- Spot price at snapshot time
);

-- Daily OI summary (aggregated for trend analysis)
CREATE TABLE IF NOT EXISTS daily_oi_summary (
    summary_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date               TEXT NOT NULL,
    underlying_symbol  TEXT NOT NULL,
    expiry_date        TEXT NOT NULL,
    
    -- CE totals
    total_ce_oi        INTEGER DEFAULT 0,
    total_ce_volume    INTEGER DEFAULT 0,
    avg_ce_iv          REAL,
    
    -- PE totals
    total_pe_oi        INTEGER DEFAULT 0,
    total_pe_volume    INTEGER DEFAULT 0,
    avg_pe_iv          REAL,
    
    -- Calculated metrics
    pcr                REAL,           -- PE OI / CE OI
    max_pain_strike    REAL,
    atm_strike         REAL,
    
    -- Market context
    underlying_close   REAL,
    underlying_change_pct REAL,
    
    UNIQUE(date, underlying_symbol, expiry_date)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_option_chain_underlying ON option_chain_snapshot(underlying_symbol);
CREATE INDEX IF NOT EXISTS idx_option_chain_expiry ON option_chain_snapshot(expiry_date);
CREATE INDEX IF NOT EXISTS idx_option_chain_strike ON option_chain_snapshot(strike_price);
CREATE INDEX IF NOT EXISTS idx_daily_oi_date ON daily_oi_summary(date);
```

### 4.2 Migration Strategy

Add to `core/data/schema.py`:

```python
def migrate_options_schema(conn):
    """Add options-related tables if they don't exist."""
    conn.execute(OPTION_CHAIN_SNAPSHOT_TABLE_SQL)
    conn.execute(DAILY_OI_SUMMARY_TABLE_SQL)
    conn.execute(OPTION_CHAIN_INDEXES_SQL)
```

---

## 5. Implementation Details

### 5.1 OptionsProvider (`core/data/options_provider.py`)

```python
class OptionsProvider:
    """
    Fetches and caches option chain data from Upstox V3 API.
    
    Usage:
        provider = OptionsProvider()
        chain = provider.fetch_option_chain("NSE_INDEX|Nifty 50", "2026-03-04")
    """
    
    def __init__(self, db_path: Path = ...):
        self._db_path = db_path
        self._cache: Dict[str, OptionChainSnapshot] = {}
        self._last_fetch_time: Dict[str, datetime] = {}
    
    def fetch_option_chain(
        self,
        underlying: str,  # "NSE_INDEX|Nifty 50" or "NSE_INDEX|Nifty Bank"
        expiry: str,      # YYYY-MM-DD
        force_refresh: bool = False
    ) -> List[OptionChainRow]:
        """
        Fetch full option chain for given underlying and expiry.
        
        Returns list of OptionChainRow dataclasses with:
        - strike, option_type (CE/PE)
        - instrument_key, tradingsymbol
        - ltp, oi, oi_change, volume
        - iv (if available from API)
        """
        cache_key = f"{underlying}:{expiry}"
        
        # Return cached if < 5 seconds old
        if not force_refresh and cache_key in self._last_fetch_time:
            age = (datetime.now() - self._last_fetch_time[cache_key]).total_seconds()
            if age < 5:
                return self._cache.get(cache_key, [])
        
        # Fetch from Upstox V3 API
        chain_data = self._fetch_from_upstox_v3(underlying, expiry)
        
        # Cache and persist
        self._cache[cache_key] = chain_data
        self._last_fetch_time[cache_key] = datetime.now()
        self._persist_to_duckdb(chain_data)
        
        return chain_data
    
    def _fetch_from_upstox_v3(self, underlying: str, expiry: str) -> List[OptionChainRow]:
        """
        Call Upstox V3 Option Chain API.
        
        GET https://api.upstox.com/v3/option-chain?instrument_key={underlying}&expiry_date={expiry}
        """
        from core.auth.credentials import credentials
        token = credentials.get("access_token")
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        params = {
            "instrument_key": underlying,
            "expiry_date": expiry
        }
        
        resp = requests.get(
            "https://api.upstox.com/v3/option-chain",
            headers=headers,
            params=params,
            timeout=10
        )
        
        if resp.status_code != 200:
            logger.error(f"[OptionsProvider] API Error {resp.status_code}: {resp.text}")
            return []
        
        data = resp.json().get("data", {})
        return self._parse_option_chain_response(data, expiry)
    
    def get_weekly_expiry(self, underlying: str, as_of_date: Optional[date] = None) -> str:
        """
        Get the nearest weekly expiry date for given index.
        
        Nifty: Tuesday
        Banknifty: Wednesday
        
        Returns: YYYY-MM-DD format
        """
        as_of = as_of_date or date.today()
        
        # Expiry weekday mapping
        expiry_weekday = {
            "NSE_INDEX|Nifty 50": 1,      # Tuesday
            "NSE_INDEX|Nifty Bank": 2,    # Wednesday
        }.get(underlying, 1)
        
        # Find nearest expiry (at least 1 day ahead)
        target = as_of + timedelta(days=1)
        days_ahead = (expiry_weekday - target.weekday()) % 7
        expiry = target + timedelta(days=days_ahead)
        
        return expiry.strftime("%Y-%m-%d")
```

---

### 5.2 OptionsAnalytics Engine (`core/analytics/options_analytics.py`)

```python
class OptionsAnalytics:
    """
    Calculates options analytics: Max Pain, PCR, OI Analysis, Greeks.
    """
    
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
        
        Returns: MaxPainResult with strike, total_pain, pain_by_strike
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
    
    @staticmethod
    def calculate_pcr(option_chain: List[OptionChainRow]) -> PCRResult:
        """
        Calculate Put-Call Ratio.
        
        PCR = Total PE OI / Total CE OI
        
        Interpretation:
        - PCR > 1.2 → Bullish (more puts = hedging)
        - PCR < 0.7 → Bearish (more calls = speculative)
        - 0.7 - 1.2 → Neutral
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
        
        return PCRResult(
            pcr=pcr,
            total_ce_oi=total_ce_oi,
            total_pe_oi=total_pe_oi,
            pcr_by_strike=pcr_by_strike,
            sentiment=sentiment
        )
    
    @staticmethod
    def analyze_oi_changes(
        current_chain: List[OptionChainRow],
        previous_chain: Optional[List[OptionChainRow]] = None
    ) -> OIAnalysisResult:
        """
        Analyze OI buildup patterns.
        
        Patterns:
        - Long Buildup: OI ↑ + Price ↑ → New long positions
        - Short Buildup: OI ↑ + Price ↓ → New short positions
        - Long Unwinding: OI ↓ + Price ↓ → Longs exiting
        - Short Covering: OI ↓ + Price ↑ → Shorts exiting
        """
        strikes = sorted(set(row.strike for row in current_chain))
        
        analysis_by_strike = {}
        for strike in strikes:
            ce_row = next((r for r in current_chain if r.strike == strike and r.option_type == "CE"), None)
            pe_row = next((r for r in current_chain if r.strike == strike and r.option_type == "PE"), None)
            
            if ce_row:
                ce_pattern = OptionsAnalytics._identify_pattern(ce_row.oi_change, ce_row.ltp)
            else:
                ce_pattern = None
            
            if pe_row:
                pe_pattern = OptionsAnalytics._identify_pattern(pe_row.oi_change, pe_row.ltp)
            else:
                pe_pattern = None
            
            analysis_by_strike[strike] = {
                "ce_oi": ce_row.oi if ce_row else 0,
                "ce_oi_change": ce_row.oi_change if ce_row else 0,
                "ce_pattern": ce_pattern,
                "pe_oi": pe_row.oi if pe_row else 0,
                "pe_oi_change": pe_row.oi_change if pe_row else 0,
                "pe_pattern": pe_pattern
            }
        
        # Find highest OI strikes (resistance/support)
        ce_oi_sorted = sorted(
            [(row.strike, row.oi) for row in current_chain if row.option_type == "CE"],
            key=lambda x: x[1],
            reverse=True
        )[:5]
        
        pe_oi_sorted = sorted(
            [(row.strike, row.oi) for row in current_chain if row.option_type == "PE"],
            key=lambda x: x[1],
            reverse=True
        )[:5]
        
        return OIAnalysisResult(
            analysis_by_strike=analysis_by_strike,
            highest_ce_oi_strikes=ce_oi_sorted,
            highest_pe_oi_strikes=pe_oi_sorted,
            ce_oi_buildup=sum(1 for s in analysis_by_strike.values() if s["ce_pattern"] == "Long Buildup"),
            pe_oi_buildup=sum(1 for s in analysis_by_strike.values() if s["pe_pattern"] == "Long Buildup")
        )
    
    @staticmethod
    def calculate_greeks_for_chain(
        option_chain: List[OptionChainRow],
        spot_price: float,
        time_to_expiry_years: float,
        risk_free_rate: float = 0.05
    ) -> List[GreeksRow]:
        """
        Calculate Greeks for all strikes in the option chain.
        Uses existing Black76Engine.
        """
        from core.risk.greeks.black76_engine import Black76Engine
        
        greeks_rows = []
        for row in option_chain:
            if row.iv is None or row.iv <= 0:
                continue  # Skip if IV not available
            
            greeks = Black76Engine.calculate_greeks(
                F=spot_price,
                K=row.strike,
                T=time_to_expiry_years,
                r=risk_free_rate,
                sigma=row.iv,
                option_type=row.option_type
            )
            
            greeks_rows.append(GreeksRow(
                strike=row.strike,
                option_type=row.option_type,
                iv=row.iv,
                delta=greeks.delta,
                gamma=greeks.gamma,
                vega=greeks.vega,
                theta=greeks.theta,
                rho=greeks.rho
            ))
        
        return greeks_rows
    
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
        if pcr > 1.2:
            return "Bullish"
        elif pcr < 0.7:
            return "Bearish"
        else:
            return "Neutral"
```

---

### 5.3 Options Facade (`app_facade/options_facade.py`)

```python
class OptionsFacade:
    """
    Bridge between Flask UI and core options logic.
    All methods are read-only (no state mutation).
    """
    
    def __init__(self):
        self._provider = OptionsProvider()
        self._analytics = OptionsAnalytics()
    
    def get_options_dashboard_data(
        self,
        index: str,  # "NIFTY" or "BANKNIFTY"
        expiry: Optional[str] = None
    ) -> OptionsDashboardData:
        """
        Get all data needed for the options dashboard.
        """
        # Map index name to underlying symbol
        underlying_map = {
            "NIFTY": "NSE_INDEX|Nifty 50",
            "BANKNIFTY": "NSE_INDEX|Nifty Bank"
        }
        underlying = underlying_map.get(index.upper())
        
        if not underlying:
            raise ValueError(f"Unknown index: {index}")
        
        # Get expiry (default to nearest weekly)
        if not expiry:
            expiry = self._provider.get_weekly_expiry(underlying)
        
        # Fetch option chain
        option_chain = self._provider.fetch_option_chain(underlying, expiry)
        
        # Get underlying LTP
        underlying_ltp = self._get_underlying_ltp(underlying)
        
        # Calculate analytics
        max_pain = self._analytics.calculate_max_pain(option_chain, underlying_ltp)
        pcr = self._analytics.calculate_pcr(option_chain)
        oi_analysis = self._analytics.analyze_oi_changes(option_chain)
        
        # Calculate time to expiry (for Greeks)
        expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
        tte_years = (expiry_date - date.today()).days / 365.0
        
        # Calculate Greeks
        greeks = self._analytics.calculate_greeks_for_chain(
            option_chain, underlying_ltp, tte_years
        )
        
        return OptionsDashboardData(
            index=index,
            underlying=underlying,
            expiry=expiry,
            underlying_ltp=underlying_ltp,
            option_chain=option_chain,
            max_pain=max_pain,
            pcr=pcr,
            oi_analysis=oi_analysis,
            greeks=greeks,
            last_update=datetime.now()
        )
    
    def get_available_expiries(self, index: str) -> List[str]:
        """Get list of available weekly expiry dates."""
        underlying = {
            "NIFTY": "NSE_INDEX|Nifty 50",
            "BANKNIFTY": "NSE_INDEX|Nifty Bank"
        }.get(index.upper())
        
        # For now, return next 4 weekly expiries
        expiries = []
        current = date.today()
        for _ in range(4):
            expiry = self._provider.get_weekly_expiry(underlying, current)
            expiries.append(expiry)
            current = datetime.strptime(expiry, "%Y-%m-%d").date() + timedelta(days=7)
        
        return expiries
    
    def _get_underlying_ltp(self, underlying: str) -> float:
        """Fetch underlying index LTP."""
        from core.brokers.upstox_market_data import UpstoxMarketData
        
        # Map underlying to instrument key
        ltp_key_map = {
            "NSE_INDEX|Nifty 50": "NSE_INDEX|IND-NIFTY 50",
            "NSE_INDEX|Nifty Bank": "NSE_INDEX|IND-Nifty Bank"
        }
        instrument_key = ltp_key_map.get(underlying)
        
        if not instrument_key:
            return 0.0
        
        market_data = UpstoxMarketData()
        ltp = market_data.fetch_ltp(instrument_key)
        return ltp or 0.0
```

---

### 5.4 Flask Blueprint (`flask_app/blueprints/options/`)

**File Structure:**
```
flask_app/blueprints/options/
├── __init__.py
└── routes.py
```

**`__init__.py`:**
```python
from flask import Blueprint

options_bp = Blueprint('options', __name__, url_prefix='/options')

from . import routes
```

**`routes.py`:**
```python
from flask import render_template, jsonify, request
from . import options_bp
from app_facade.options_facade import OptionsFacade

facade = OptionsFacade()

@options_bp.route('/analysis')
def options_analysis():
    """Main options analysis dashboard page."""
    return render_template('options/analysis.html')

@options_bp.route('/api/dashboard-data')
def api_dashboard_data():
    """Get dashboard data as JSON."""
    index = request.args.get('index', 'NIFTY')
    expiry = request.args.get('expiry', None)
    
    try:
        data = facade.get_options_dashboard_data(index, expiry)
        return jsonify(data.to_dict())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@options_bp.route('/api/expiries')
def api_expiries():
    """Get available expiry dates."""
    index = request.args.get('index', 'NIFTY')
    
    try:
        expiries = facade.get_available_expiries(index)
        return jsonify({'expiries': expiries})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

---

### 5.5 Dashboard Template (`flask_app/templates/options/analysis.html`)

**Key Features:**
- Tabbed interface (Nifty | Banknifty)
- Auto-refresh every 5 seconds
- Interactive Chart.js visualizations
- Real-time OI/PCR/Max Pain updates
- Greeks table with color coding

**Layout Sections:**
1. **Header:** Index tabs, expiry selector, auto-refresh toggle, last update timestamp
2. **Summary Cards:** Spot Price, Max Pain, PCR, ATM IV
3. **Charts:** OI Distribution, OI Change, IV Smile, Volume
4. **Options Chain Table:** CE OI/ΔOI/IV | Strike | PE IV/ΔOI/OI
5. **OI Buildup Analysis:** Pattern detection (Long Buildup, Short Covering, etc.)

---

### 5.6 JavaScript Dashboard (`flask_app/static/js/options_dashboard.js`)

**Key Functions:**
```javascript
// Main dashboard controller
class OptionsDashboard {
    constructor() {
        this.currentIndex = 'NIFTY';
        this.currentExpiry = null;
        this.refreshInterval = 5000;
        this.autoRefresh = true;
        this.charts = {};
    }
    
    async init() {
        await this.loadExpiries();
        await this.refreshData();
        this.startAutoRefresh();
        this.setupEventListeners();
    }
    
    async refreshData() {
        const response = await fetch(`/options/api/dashboard-data?index=${this.currentIndex}&expiry=${this.currentExpiry}`);
        const data = await response.json();
        
        this.updateSummaryCards(data);
        this.updateCharts(data);
        this.updateOptionsChainTable(data);
        this.updateGreeksTable(data);
        this.updateLastUpdateTime();
    }
    
    updateCharts(data) {
        // OI Distribution Chart (CE vs PE by strike)
        this.updateOIDistributionChart(data);
        
        // OI Change Chart
        this.updateOIChangeChart(data);
        
        // IV Smile Chart
        this.updateIVSmileChart(data);
    }
    
    startAutoRefresh() {
        setInterval(() => {
            if (this.autoRefresh) {
                this.refreshData();
            }
        }, this.refreshInterval);
    }
}
```

---

## 6. Upstox V3 API Integration

### 6.1 Option Chain Endpoint

```http
GET https://api.upstox.com/v3/option-chain
Authorization: Bearer {access_token}

Query Parameters:
- instrument_key: "NSE_INDEX|Nifty 50" or "NSE_INDEX|Nifty Bank"
- expiry_date: "2026-03-04" (YYYY-MM-DD)
```

**Response Structure:**
```json
{
  "data": {
    "underlying": {
      "instrument_key": "NSE_INDEX|Nifty 50",
      "ltp": 22450.30,
      "change": 268.50,
      "change_percent": 1.21
    },
    "option_chain": [
      {
        "strike": 22400,
        "expiry": "2026-03-04",
        "call": {
          "instrument_key": "NSE_FO|54710",
          "tradingsymbol": "NIFTY04MAR2622400CE",
          "ltp": 185.50,
          "oi": 45100,
          "oi_change": 8500,
          "volume": 125000,
          "iv": 0.142,
          "delta": 0.52,
          "gamma": 0.0012,
          "theta": -8.5,
          "vega": 12.3
        },
        "put": {
          "instrument_key": "NSE_FO|54711",
          "tradingsymbol": "NIFTY04MAR2622400PE",
          "ltp": 142.30,
          "oi": 52300,
          "oi_change": 3200,
          "volume": 98000,
          "iv": 0.140,
          "delta": -0.48,
          "gamma": 0.0012,
          "theta": -7.8,
          "vega": 12.1
        }
      },
      ...
    ]
  }
}
```

### 6.2 Authentication

Use existing `core/auth/credentials.py`:
```python
from core.auth.credentials import credentials
token = credentials.get("access_token")
```

**Token Expiry Handling:**
- Upstox tokens expire every 24 hours
- Check `credentials.needs_daily_refresh` before API calls
- If expired, redirect to `/ops/callback/upstox` for re-authentication

---

## 7. Implementation Phases

### Phase 1: Core Infrastructure (4-6 hours)

| Task | File | Estimate |
|------|------|----------|
| Create `OptionsProvider` | `core/data/options_provider.py` | 2-3 hours |
| Create `OptionsAnalytics` | `core/analytics/options_analytics.py` | 2 hours |
| Add DB schema | `core/data/schema.py` | 30 min |
| Test data fetch | Manual script | 30 min |

**Deliverable:** Can fetch option chain from Upstox and calculate Max Pain/PCR

---

### Phase 2: Facade & API (2-3 hours)

| Task | File | Estimate |
|------|------|----------|
| Create `OptionsFacade` | `app_facade/options_facade.py` | 1 hour |
| Create Flask blueprint | `flask_app/blueprints/options/` | 1 hour |
| Add API routes | `flask_app/blueprints/options/routes.py` | 30 min |
| Test API endpoints | Postman/curl | 30 min |

**Deliverable:** REST API endpoints returning JSON data

---

### Phase 3: UI Dashboard (6-8 hours)

| Task | File | Estimate |
|------|------|----------|
| Create HTML template | `flask_app/templates/options/analysis.html` | 3-4 hours |
| Create JavaScript controller | `flask_app/static/js/options_dashboard.js` | 2-3 hours |
| Add Chart.js visualizations | Embedded in template | 1 hour |
| Add navigation link | `flask_app/templates/layout.html` | 30 min |

**Deliverable:** Working dashboard with auto-refresh

---

### Phase 4: Real-Time Updates (2-3 hours)

| Task | File | Estimate |
|------|------|----------|
| Create `OptionsPublisher` | `core/zmq/options_publisher.py` | 1 hour |
| Integrate with runner | `scripts/unified_live_runner.py` | 30 min |
| Add SSE endpoint | `flask_app/blueprints/options/routes.py` | 30 min |
| Test real-time updates | Manual testing | 30 min |

**Deliverable:** Sub-second updates via ZMQ → SSE

---

### Phase 5: Testing & Polish (2-3 hours)

| Task | Estimate |
|------|----------|
| Test with live data | 1 hour |
| Fix edge cases (API errors, missing data) | 30 min |
| Performance optimization | 30 min |
| Documentation | 30 min |

**Deliverable:** Production-ready dashboard

---

**Total Estimated Time:** 16-23 hours (2-3 days part-time)

---

## 8. Testing Strategy

### 8.1 Unit Tests

```python
# tests/analytics/test_options_analytics.py

def test_max_pain_calculation():
    chain = [
        OptionChainRow(strike=22400, option_type="CE", oi=45100, ltp=185.50),
        OptionChainRow(strike=22400, option_type="PE", oi=52300, ltp=142.30),
        OptionChainRow(strike=22500, option_type="CE", oi=25300, ltp=125.00),
        OptionChainRow(strike=22500, option_type="PE", oi=15400, ltp=95.50),
    ]
    result = OptionsAnalytics.calculate_max_pain(chain, spot_price=22450.30)
    assert result.max_pain_strike == 22400
    assert result.distance_from_spot == 50.30

def test_pcr_calculation():
    chain = [...]
    result = OptionsAnalytics.calculate_pcr(chain)
    assert 0.7 < result.pcr < 1.2  # Neutral range
    assert result.sentiment == "Neutral"

def test_oi_pattern_identification():
    assert OptionsAnalytics._identify_pattern(oi_change=5000, price_change_pct=2.1) == "Long Buildup"
    assert OptionsAnalytics._identify_pattern(oi_change=-3000, price_change_pct=1.5) == "Short Covering"
```

### 8.2 Integration Tests

```python
# tests/integration/test_options_provider.py

def test_fetch_option_chain_from_upstox():
    provider = OptionsProvider()
    chain = provider.fetch_option_chain("NSE_INDEX|Nifty 50", "2026-03-04")
    assert len(chain) > 0
    assert all(row.strike > 0 for row in chain)
    assert all(row.option_type in ("CE", "PE") for row in chain)

def test_get_weekly_expiry():
    provider = OptionsProvider()
    nifty_expiry = provider.get_weekly_expiry("NSE_INDEX|Nifty 50")
    banknifty_expiry = provider.get_weekly_expiry("NSE_INDEX|Nifty Bank")
    
    # Nifty expires on Tuesday (weekday=1)
    assert datetime.strptime(nifty_expiry, "%Y-%m-%d").weekday() == 1
    # Banknifty expires on Wednesday (weekday=2)
    assert datetime.strptime(banknifty_expiry, "%Y-%m-%d").weekday() == 2
```

### 8.3 Manual Testing Checklist

- [ ] Dashboard loads without errors
- [ ] Nifty tab shows correct data
- [ ] Banknifty tab shows correct data
- [ ] Auto-refresh updates data every 5 seconds
- [ ] Max Pain matches NSE website value
- [ ] PCR calculation is correct
- [ ] Charts render correctly
- [ ] Options chain table shows ATM strikes highlighted
- [ ] Greeks table displays Delta/Gamma/Theta/Vega
- [ ] API error handling (no token, network issue)
- [ ] Expiry selector works
- [ ] Last update timestamp is accurate

---

## 9. Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **Upstox API Rate Limits** | High | Medium | Cache for 5 seconds, batch LTP requests |
| **Token Expiry** | Medium | High | Check `needs_daily_refresh` before each call |
| **Large Option Chain (100+ strikes)** | Low | Medium | Show ATM ± 10 strikes by default, paginate |
| **Missing IV Data from API** | Medium | Medium | Calculate IV using Black-76 inverse if needed |
| **DuckDB Lock Contention** | Low | Low | Use read-only connections for queries |
| **Browser Performance (5s refresh)** | Low | Low | Throttle chart updates, use requestAnimationFrame |

---

## 10. Success Criteria

- ✅ Fetches live Nifty & Banknifty option chain from Upstox V3
- ✅ Calculates Max Pain correctly (validated vs. NSE website)
- ✅ Displays OI distribution chart (CE vs PE)
- ✅ Shows PCR with interpretation (Bullish/Bearish/Neutral)
- ✅ Updates every 5 seconds during market hours
- ✅ Loads in < 2 seconds
- ✅ Displays Greeks (Delta, Gamma, Theta, Vega) per strike
- ✅ Shows IV smile/skew chart
- ✅ Identifies OI buildup patterns (Long Buildup, Short Covering)
- ✅ Handles API errors gracefully (shows last known data)

---

## 11. Future Enhancements (Post-MVP)

| Feature | Priority | Complexity |
|---------|----------|------------|
| **Historical OI Trend Charts** | P2 | Medium |
| **Options Strategy Builder** (Straddle, Strangle) | P2 | High |
| **Max Pain Heatmap** (multiple expiries) | P2 | Medium |
| **PCR Time-Series** (intraday trend) | P2 | Low |
| **Unusual Options Activity Scanner** | P1 | High |
| **Export to CSV/Excel** | P3 | Low |
| **Mobile-Responsive Layout** | P3 | Medium |
| **Alerts** (Max Pain shift, PCR extreme) | P2 | Medium |

---

## 12. Dependencies

Add to `pyproject.toml` (if not already present):

```toml
[project.dependencies]
flask = "*"
pyzmq = "*"
duckdb = "*"
pandas = "*"
numpy = "*"
requests = "*"
# Chart.js is loaded via CDN in template
```

---

## 13. File Inventory (Complete List)

### New Files to Create

```
core/data/options_provider.py
core/analytics/options_analytics.py
app_facade/options_facade.py
core/zmq/options_publisher.py
flask_app/blueprints/options/__init__.py
flask_app/blueprints/options/routes.py
flask_app/templates/options/analysis.html
flask_app/static/js/options_dashboard.js
```

### Files to Modify

```
core/data/schema.py              # Add options tables
flask_app/templates/layout.html  # Add navigation link
scripts/unified_live_runner.py   # Start OptionsPublisher (optional)
```

---

## 14. Next Steps

1. **Review this plan** — Confirm all requirements are captured
2. **Approve implementation** — Greenlight to start coding
3. **Phase 1 kickoff** — Build `OptionsProvider` and `OptionsAnalytics`
4. **Test data fetch** — Verify Upstox V3 API integration works
5. **Iterate through phases** — UI → Real-time → Polish

---

**Document Status:** Ready for Implementation  
**Approval Required:** Yes  
**Estimated Start:** Upon approval  

---

*This plan leverages existing infrastructure (Greeks engine, instrument resolution, credential management) while building new options-specific analytics and UI components. The 5-second update cycle and weekly expiry focus align with your requirements.*


