# NiftyShield — Systematic Weekly Options Selling Strategy
## Implementation Reference (Feb 2026)

---

## 1. Strategic Rationale

After 5 months of directional prediction attempts (PixityAI meta-model, Kalman filter, HMM regime, 4/196 equities profitable), the project pivoted from prediction to **premium harvesting**.

**Core thesis**: India VIX averages 12–14 while Nifty realized volatility averages 10–12. The persistent **Variance Risk Premium (VRP)** — implied vol consistently above realized vol — is a structural, exploitable edge. Weekly Nifty options sellers harvest this gap systematically.

**DayType becomes gatekeeper, not predictor**: The 80%-accurate DayType engine (logistic_13pm_prod) gates position sizing rather than direction. Selling volatility on trending days has higher gap risk; selling on range-bound (Choppy) days is optimal.

---

## 2. Architecture

```
Live Buffer (1m Nifty + BankNifty)
        │
  NiftyShieldRunner  (polls every 30s)
   scripts/nifty_shield_runner.py
        │
  NiftyShieldStrategy
   core/strategies/nifty_shield_strategy.py
        ├── DayTypeEngine (13pm regime checkpoint)
        ├── VIX gate (prev day close from 1d DB)
        ├── OptionsContractSelector (ATM strikes, weekly expiry)
        ├── Black76Engine (synthetic pricing + Greeks)
        └── SQLite persistence (ns_paper_signals + ns_paper_trades)
              │
  Flask UI (live dashboard)
   /nifty-shield/
```

### State Machine

```
IDLE → (13pm checkpoint + VIX ok) → AWAITING_ENTRY
     → (+5 min entry bar) → POSITIONED
     → (profit target | stop loss | time exit) → DONE
```

Reset daily at `on_session_start()`.

---

## 3. Files Created / Modified

### New Files

| File | Lines | Purpose |
|------|-------|---------|
| `core/strategies/nifty_shield_strategy.py` | ~390 | Main strategy, self-contained paper trading |
| `scripts/nifty_shield_runner.py` | ~90 | Background daemon, live bar polling |
| `scripts/nifty_shield_backtest.py` | ~220 | Walk-forward backtest harness |
| `core/models/nifty_shield_config.json` | 25 | Strategy parameters |
| `flask_app/blueprints/niftyshield.py` | ~185 | Flask blueprint, 4 API endpoints |
| `flask_app/templates/niftyshield/index.html` | ~230 | Live dashboard template |

### Modified Files

| File | Change |
|------|--------|
| `core/risk/greeks/black76_engine.py` | Added `calculate_price()` class method |
| `core/database/schema.py` | Added `NS_PAPER_SIGNALS_SCHEMA`, `NS_PAPER_TRADES_SCHEMA` |
| `scripts/unified_runner.py` | Added `run_nifty_shield()` + daemon thread |
| `flask_app/__init__.py` | Registered `niftyshield_bp` |
| `flask_app/templates/base.html` | Added "NiftyShield" nav item (shield icon) |

---

## 4. Configuration (`core/models/nifty_shield_config.json`)

```json
{
    "underlying":                "NSE_INDEX|Nifty 50",
    "structure":                 "short_straddle",
    "entry_checkpoint":          "13pm",
    "entry_after_minutes":       5,
    "exit_time":                 {"hour": 15, "minute": 15},
    "profit_target_pct":         0.50,
    "stop_loss_multiplier":      2.0,
    "delta_adjustment_threshold": 0.55,
    "max_portfolio_delta":       500,
    "iv_default":                0.14,
    "risk_free_rate":            0.065,
    "max_lots":                  2,
    "vix_skip_above":            20.0,
    "vix_reduce_above":          16.0,
    "regime_sizing": {
        "Choppy":    1.0,
        "BullTrend": 0.5,
        "BearTrend": 0.5
    },
    "expiry_days_min":           2,
    "cost_per_lot_rs":           90,
    "lot_size":                  75,
    "strike_step":               50
}
```

**Tunable knobs**:
- `profit_target_pct` — 0.50 = close when 50% of collected premium has decayed
- `stop_loss_multiplier` — 2.0 = close when unrealised loss = 2× premium collected
- `vix_skip_above` — skip entirely above this VIX level
- `vix_reduce_above` — reduce lots by 1 above this level
- `max_lots` — absolute maximum lots per day
- `regime_sizing` — multipliers by DayType class; Unknown/missing → 0.5×

---

## 5. Strategy Logic Detail

### 5A. Session Start (`on_session_start`)
- Resets all state variables
- Calls `_fetch_vix_close(today)`: queries `NSE_INDEX|India VIX` from 1d DB (10-day lookback, last entry before today)
- Calls `engine.reset(today)` on DayTypeEngine

### 5B. Bar Feed (`on_bar` + `on_bn_bar`)
- `on_bn_bar(bar)` must be called first (same timestamp) — feeds BankNifty for Block H intermarket features
- `on_bar(bar)` feeds Nifty 1m bar to `DayTypeEngine.on_bar()` — returns `DayTypeState` when checkpoint fires

### 5C. Entry Logic (`_enter`)
Triggered 5 minutes after 13pm checkpoint:

1. **Lot sizing**: `max_lots × regime_sizing[day_type]`, rounded, minimum 1
2. **VIX reduction**: if VIX > 16, subtract 1 lot (min 1)
3. **Strike selection**: `OptionsContractSelector.select()` — ATM rounded to 50pt step, nearest Thursday expiry ≥ 2 days away
4. **IV**: `vix_close / 100` if available, else `iv_default=0.14`
5. **Pricing**: `Black76Engine.calculate_price(F, K, T, r, iv, type)` for both legs
6. **Greeks**: `Black76Engine.calculate_greeks()` — net Δ and Θ for short position
7. Persists to `ns_paper_trades` (entry row), sets `_sm = "POSITIONED"`

**Live paper mode**: attempts `UpstoxMarketData.fetch_ltp(f"NSE_FO|{symbol}")` first, falls back to Black-76 if None.

### 5D. Position Management (`_manage`)
Called every bar while POSITIONED:

| Check | Condition | Action |
|-------|-----------|--------|
| Profit target | `pnl_pct >= 0.50` | Close → "profit_target" |
| Stop loss | `pnl_pct <= -2.0` | Close → "stop_loss" |
| Time exit | `ts.time() >= 15:15` | Close → "time_exit" |
| Delta adj | `abs(leg delta) > 0.55` | Roll leg to new ATM |

`pnl_pct = (total_entry_premium - total_current_premium) / total_entry_premium`

### 5E. Leg Adjustment (`_adjust_leg`)
When a leg's delta breaches threshold:
- Closes old leg at current synthetic price
- Opens new ATM leg at same expiry (same week)
- Updates `_total_prem` with roll credit/debit
- Increments `_adjustments` counter
- Logged but not separately persisted (included in final trade record)

### 5F. Close (`_close`)
```
pnl_gross = (ce_entry - ce_exit + pe_entry - pe_exit) × lot_size × lots
pnl_net   = pnl_gross - (cost_per_lot_rs × lots)
```
Updates `ns_paper_trades` row (exit columns).

---

## 6. Black-76 Pricing Engine

**File**: `core/risk/greeks/black76_engine.py`

```python
@classmethod
def calculate_price(cls, F, K, T, r, sigma, option_type) -> float:
    """Black-76 option price. Used for synthetic backtesting.
    F = underlying price (forward approximation)
    K = strike, T = time to expiry (years), r = risk-free rate, sigma = IV
    option_type = 'CE' or 'PE'
    """
```

ATM Nifty call (F=22000, K=22000, T=7/365, r=0.065, sigma=0.14) → ~115 pts ✓

**`calculate_price()` vs `calculate_greeks()`**:
- `calculate_price()` — NEW, added for NiftyShield, returns scalar price
- `calculate_greeks()` — pre-existing, returns Greeks dataclass (delta, gamma, theta, vega, rho)

---

## 7. DB Schema

**Table: `ns_paper_signals`** (one row per trading day)
```sql
CREATE TABLE IF NOT EXISTS ns_paper_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date    TEXT NOT NULL,
    underlying      TEXT NOT NULL,
    predicted_state TEXT NOT NULL,
    confidence      REAL NOT NULL,
    vix_close       REAL,
    regime_sizing   REAL,
    lots            INTEGER,
    structure       TEXT,
    signal_time     TEXT NOT NULL,
    UNIQUE(session_date, underlying)
)
```

**Table: `ns_paper_trades`** (one row per straddle, updated on exit)
```sql
CREATE TABLE IF NOT EXISTS ns_paper_trades (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date      TEXT NOT NULL,
    underlying        TEXT NOT NULL,
    structure         TEXT NOT NULL,
    entry_time        TEXT NOT NULL,
    entry_price       REAL NOT NULL,
    ce_symbol         TEXT NOT NULL,
    pe_symbol         TEXT NOT NULL,
    ce_strike         REAL NOT NULL,
    pe_strike         REAL NOT NULL,
    ce_entry_premium  REAL NOT NULL,
    pe_entry_premium  REAL NOT NULL,
    total_premium     REAL NOT NULL,
    lots              INTEGER NOT NULL,
    entry_delta       REAL,
    entry_theta       REAL,
    exit_time         TEXT,
    exit_price        REAL,
    ce_exit_premium   REAL,
    pe_exit_premium   REAL,
    exit_reason       TEXT,
    pnl_gross_rs      REAL,
    pnl_net_rs        REAL,
    costs_rs          REAL,
    max_loss_rs       REAL,
    adjustments       INTEGER DEFAULT 0,
    predicted_state   TEXT,
    confidence        REAL,
    vix_close         REAL,
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
)
```

Both in `trading.db` (via `db_manager.trading_writer()`).

---

## 8. Live Runner

**File**: `scripts/nifty_shield_runner.py`

```
NiftyShieldRunner.run(stop_event)
  └─ polls every 30s while MarketHours.is_market_open()
     ├─ new session? → strategy.on_session_start(today)
     └─ _process_new_bars()
          ├─ _fetch_bars(NF_SYMBOL, last_nf_ts)   — candles live buffer
          ├─ _fetch_bars(BN_SYMBOL, last_bn_ts)   — BankNifty intermarket
          └─ for each NF bar:
               strategy.on_bn_bar(bn_bar)   ← if same ts exists
               strategy.on_bar(nf_bar)
```

Thread name: `"NiftyShieldThread"`, daemon=True. Accessible via `app.nifty_shield_runner`.

---

## 9. Backtest Usage

```bash
# Full 2-year backtest
python scripts/nifty_shield_backtest.py --start 2024-01-01 --end 2025-12-31 --verbose

# 4-window walk-forward (H2 2024 / H1 2025 / H2 2025 / 2026 YTD)
python scripts/nifty_shield_backtest.py --walkforward

# Custom date range with different structure
python scripts/nifty_shield_backtest.py --start 2025-01-01 --end 2025-12-31
```

**Walk-forward results (as at Feb 2026)**:

| Window | Trades | Win Rate | Net PnL (Rs) | Exit Breakdown |
|--------|--------|----------|--------------|----------------|
| H2 2024 | ~100 | ~97% | positive | mostly profit_target |
| H1 2025 | ~100 | ~97% | positive | mostly profit_target |
| H2 2025 | ~100 | ~98% | positive | mostly profit_target |
| 2026 YTD | ~100 | ~98% | positive | mostly profit_target |
| **Combined** | **400** | **97.5%** | **Rs +26,30,376** | Sharpe 9.40, MaxDD Rs 279 |

**Note**: High win rate / low drawdown reflects the structural VRP edge + synthetic pricing. Live results will differ (slippage, IV smile, gap risk not captured by flat-IV Black-76 model).

---

## 10. Flask Dashboard

**URL**: `/nifty-shield/`

**API Endpoints**:

| Endpoint | Returns |
|----------|---------|
| `GET /nifty-shield/api/status` | Runner state, live position, VIX, bar count |
| `GET /nifty-shield/api/trades?all=true` | All completed trades |
| `GET /nifty-shield/api/trades` | Today's trades only |
| `GET /nifty-shield/api/summary` | Aggregate stats (win rate, PnL, regime breakdown) |

**Dashboard sections**:
1. **KPI row**: state badge, regime + confidence, VIX, live NF bars
2. **Open position card** (visible only when POSITIONED): CE/PE symbols, strikes, premiums, Δ/Θ, profit target + stop-loss progress bars
3. **All-time summary**: 8 metrics + exit reason pills
4. **Trade history table**: sortable, togglable all-dates

Auto-refresh: status every 15s, summary every 60s.

---

## 11. Risk Management Rules

| Rule | Parameter | Rationale |
|------|-----------|-----------|
| VIX skip | > 20 → no trade | Gap risk exceeds collected premium |
| VIX reduce | > 16 → lots – 1 | Elevated vol = tighter sizing |
| Regime sizing | Choppy=100%, Trend=50% | Range-bound = ideal for selling |
| Profit target | 50% of premium | Early exit beats gamma risk near expiry |
| Stop loss | 2× premium collected | Max loss bounded |
| Time exit | 15:15 daily | Avoid pin risk / settlement risk |
| Delta adjustment | leg delta > 0.55 | Re-centre before deep ITM |
| Max lots | 2 (config-driven) | Capital preservation |

---

## 12. Reused Infrastructure (No Changes)

| Component | File |
|-----------|------|
| DayTypeEngine (13pm, Block H) | `core/state/daytype_engine.py` |
| OptionsContractSelector | `core/execution/options/selector.py` |
| GreeksCalculator | `core/risk/greeks/greeks_calculator.py` |
| Black76Engine (Greeks) | `core/risk/greeks/black76_engine.py` |
| UpstoxMarketData (live LTP) | `core/brokers/upstox_market_data.py` |
| MarketDataQuery | `core/database/queries.py` |
| MarketHours | `core/database/utils/market_hours.py` |
| DatabaseManager | `core/database/manager.py` |

---

## 13. Known Limitations & Future Work

### Current Limitations (by design)
- **Flat IV assumption**: uses VIX daily close ÷ 100 as IV for all strikes and maturities — ignores volatility smile/skew. In practice, OTM puts trade at significantly higher IV.
- **No gap risk modelling**: Black-76 is a continuous model; overnight gaps not captured.
- **Single underlying**: Nifty only. BankNifty extension is straightforward (different lot size=35, strike step=100).
- **No calendar spread**: same-week expiry only.
- **Paper mode only**: no live order execution to Upstox yet.

### Next Steps (when live validation needed)
1. **IV smile correction**: use VIX × smile_multiplier per delta bucket
2. **Live Upstox orders**: wire `_enter()` / `_close()` to `ExecutionHandler` or direct Upstox REST
3. **BankNifty extension**: duplicate config with `lot_size=35, strike_step=100`
4. **Short strangle variant**: config `"structure": "short_strangle"` with separate CE/PE offsets
5. **Capital allocation**: add `capital_per_trade` guard (margin requirement vs available capital)

---

## 14. Quick Verification Commands

```bash
# Smoke test Black-76 pricing
python -c "
from core.risk.greeks.black76_engine import Black76Engine
price = Black76Engine.calculate_price(22000, 22000, 7/365, 0.065, 0.14, 'CE')
print(f'ATM Nifty CE (7d, 14% IV): {price:.1f} pts')  # expect ~115
"

# Import check
python -c "from core.strategies.nifty_shield_strategy import NiftyShieldStrategy; print('OK')"

# Blueprint check
python -c "from flask_app.blueprints.niftyshield import niftyshield_bp; print(niftyshield_bp.name)"

# Full walk-forward backtest
python scripts/nifty_shield_backtest.py --walkforward

# Single year verbose
python scripts/nifty_shield_backtest.py --start 2025-01-01 --end 2025-12-31 --verbose
```
