# Scanner + Watchlist Integration - Implementation Plan

## Overview

Implement a dual-panel Scanner page with:
- **Left Panel (32%)**: Watchlist - market context (READ-ONLY)
- **Right Panel (68%)**: Live Scanner - strategy states from runner
- **Bottom Drawer**: Symbol context on click

All data flows from DuckDB. Flask is strictly read-only. Runner sovereignty preserved.

---

## Architecture Diagram

```
┌───────────────────────────────────────────────────────────────┐
│                      SCANNER PAGE                              │
├─────────────────────┬─────────────────────────────────────────┤
│                     │                                         │
│     WATCHLIST       │            LIVE SCANNER                 │
│      (32%)          │              (68%)                      │
│                     │                                         │
│  ┌───────────────┐  │  ┌─────────────────────────────────┐   │
│  │ Symbol | Price│  │  │ Symbol│Strategy│Bias│Conf│Status│   │
│  │ NIFTY  │52450 │  │  │ NIFTY │ ehma   │BULL│ 85%│RUNNING│  │
│  │ BANK   │48200 │  │  │ BANK  │ conf   │BEAR│ 72%│WAITING│  │
│  └───────────────┘  │  └─────────────────────────────────┘   │
│                     │                                         │
├─────────────────────┴─────────────────────────────────────────┤
│              SYMBOL CONTEXT DRAWER (Collapsible)              │
│  [Analytics] [Active Strategies] [Recent Signals] [Regime]    │
└───────────────────────────────────────────────────────────────┘
```

---

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `core/data/schema.py` | MODIFY | Add `runner_state` and `instrument_meta` tables |
| `app_facade/scanner_facade.py` | CREATE | ScannerFacade with 3 methods |
| `flask_app/blueprints/scanner.py` | MODIFY | Add 3 API routes |
| `flask_app/templates/scanner/index.html` | REWRITE | Dual-panel layout with drawer |
| `core/runner.py` | MODIFY | Add `_update_runner_state()` hook |

---

## Phase 1: Schema Additions

Add to `core/data/schema.py` BOOTSTRAP_STATEMENTS:

### runner_state Table

Tracks runner state per symbol/strategy pair. **Written by Runner only.**

```sql
CREATE TABLE IF NOT EXISTS runner_state (
    symbol TEXT,
    strategy_id TEXT,
    timeframe TEXT DEFAULT '1m',
    current_bias TEXT,           -- BULLISH, BEARISH, NEUTRAL
    signal_state TEXT,           -- PENDING, TRIGGERED, COOLDOWN
    confidence DOUBLE,
    last_bar_ts TIMESTAMP,
    status TEXT,                 -- RUNNING, WAITING, DISABLED
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, strategy_id)
);
```

### instrument_meta Table

Instrument classification metadata.

```sql
CREATE TABLE IF NOT EXISTS instrument_meta (
    symbol TEXT PRIMARY KEY,
    trading_symbol TEXT,
    instrument_key TEXT,
    exchange TEXT,              -- NSE, BSE
    market_type TEXT,           -- INDEX, FUT, EQ, ETF
    lot_size INTEGER DEFAULT 1,
    tick_size DOUBLE DEFAULT 0.05,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Phase 2: ScannerFacade

Create `app_facade/scanner_facade.py`

### Data Models

```python
from dataclasses import dataclass
from typing import List, Dict, Optional, Any
from datetime import datetime

@dataclass
class WatchlistRow:
    """Single row in the Watchlist panel."""
    symbol: str
    trading_symbol: str
    market_type: str
    last_price: float
    price_change_pct: float
    volume: float
    volatility_atr: Optional[float]
    last_updated: Optional[datetime]

@dataclass
class ScannerRow:
    """Single row in the Live Scanner panel."""
    symbol: str
    strategy_id: str
    strategy_name: str
    timeframe: str
    current_bias: str
    signal_state: str
    confidence: float
    last_bar_ts: Optional[datetime]
    status: str  # RUNNING, WAITING, TRIGGERED, COOLDOWN, DISABLED

@dataclass
class SymbolContext:
    """Full context for a selected symbol."""
    symbol: str
    trading_symbol: str
    market_type: str

    # Latest analytics
    latest_bias: Optional[str]
    latest_confidence: Optional[float]
    indicator_states: Optional[Dict[str, Any]]

    # Market regime
    regime: Optional[str]
    momentum_bias: Optional[str]
    trend_strength: Optional[float]
    volatility_level: Optional[str]

    # Active strategies
    active_strategies: List[Dict[str, Any]]

    # Recent signals (last 10)
    recent_signals: List[Dict[str, Any]]

    # Last trade (if any)
    last_trade: Optional[Dict[str, Any]]
```

### Facade Methods

| Method | Source Tables | Returns |
|--------|---------------|---------|
| `get_watchlist_snapshot()` | ohlcv_1m, instrument_meta, confluence_insights | `List[WatchlistRow]` |
| `get_live_scanner_state()` | runner_state | `List[ScannerRow]` |
| `get_symbol_context(symbol)` | All tables aggregated | `SymbolContext` |

All queries use `read_only=True` connections.

### Watchlist Query

```sql
WITH latest_bars AS (
    SELECT
        instrument_key,
        close AS last_price,
        volume,
        timestamp,
        ROW_NUMBER() OVER (PARTITION BY instrument_key ORDER BY timestamp DESC) AS rn
    FROM ohlcv_1m
),
prev_bars AS (
    SELECT
        instrument_key,
        close AS prev_close,
        ROW_NUMBER() OVER (PARTITION BY instrument_key ORDER BY timestamp DESC) AS rn
    FROM ohlcv_1m
)
SELECT
    COALESCE(m.symbol, lb.instrument_key) AS symbol,
    COALESCE(m.trading_symbol, lb.instrument_key) AS trading_symbol,
    COALESCE(m.market_type, 'EQ') AS market_type,
    lb.last_price,
    CASE
        WHEN pb.prev_close > 0 THEN ((lb.last_price - pb.prev_close) / pb.prev_close) * 100
        ELSE 0
    END AS price_change_pct,
    lb.volume,
    lb.timestamp AS last_updated
FROM latest_bars lb
LEFT JOIN prev_bars pb ON lb.instrument_key = pb.instrument_key AND pb.rn = 2
LEFT JOIN instrument_meta m ON lb.instrument_key = m.instrument_key
WHERE lb.rn = 1
ORDER BY lb.volume DESC
LIMIT 100
```

### Scanner State Query

```sql
SELECT
    rs.symbol,
    rs.strategy_id,
    rs.strategy_id AS strategy_name,
    rs.timeframe,
    rs.current_bias,
    rs.signal_state,
    rs.confidence,
    rs.last_bar_ts,
    rs.status
FROM runner_state rs
ORDER BY
    CASE rs.status
        WHEN 'RUNNING' THEN 1
        WHEN 'TRIGGERED' THEN 2
        WHEN 'WAITING' THEN 3
        WHEN 'COOLDOWN' THEN 4
        ELSE 5
    END,
    rs.updated_at DESC
```

---

## Phase 3: Flask Routes

Update `flask_app/blueprints/scanner.py`:

```python
from flask import Blueprint, render_template, jsonify
from flask_app.middleware import login_required
from app_facade.scanner_facade import ScannerFacade
from dataclasses import asdict
from datetime import datetime

scanner_bp = Blueprint('scanner', __name__)

@scanner_bp.route('/')
@login_required
def index():
    """Live scanner page with dual-panel layout."""
    return render_template('scanner/index.html')

@scanner_bp.route('/api/watchlist')
@login_required
def get_watchlist():
    """Returns watchlist snapshot for left panel."""
    try:
        rows = ScannerFacade.get_watchlist_snapshot()
        return jsonify({
            "success": True,
            "data": [asdict(r) for r in rows],
            "count": len(rows),
            "updated_at": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@scanner_bp.route('/api/scanner-state')
@login_required
def get_scanner_state():
    """Returns live scanner state for right panel."""
    try:
        rows = ScannerFacade.get_live_scanner_state()
        return jsonify({
            "success": True,
            "data": [asdict(r) for r in rows],
            "count": len(rows),
            "updated_at": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@scanner_bp.route('/api/symbol-context/<symbol>')
@login_required
def get_symbol_context(symbol: str):
    """Returns full context for a symbol (bottom drawer)."""
    try:
        context = ScannerFacade.get_symbol_context(symbol)
        if context is None:
            return jsonify({"success": False, "error": "Symbol not found"}), 404
        return jsonify({
            "success": True,
            "data": asdict(context)
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
```

---

## Phase 4: Template Layout

Rewrite `flask_app/templates/scanner/index.html`

### CSS Classes

```css
.scanner-layout {
    display: grid;
    grid-template-columns: 32% 68%;
    gap: 1.5rem;
    height: calc(100vh - 200px);
}

.symbol-drawer {
    position: fixed;
    bottom: 0;
    left: 16rem;
    right: 0;
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.3s ease-out;
}

.symbol-drawer.open {
    max-height: 400px;
}

/* Status badges */
.status-RUNNING { background: rgba(16, 185, 129, 0.15); color: #10b981; }
.status-TRIGGERED { background: rgba(245, 158, 11, 0.15); color: #f59e0b; animation: pulse 1.5s infinite; }
.status-WAITING { background: rgba(107, 114, 128, 0.15); color: #6b7280; }
.status-COOLDOWN { background: rgba(139, 92, 246, 0.15); color: #8b5cf6; }
.status-DISABLED { background: rgba(239, 68, 68, 0.15); color: #ef4444; }

/* Bias colors */
.bias-BULLISH { color: #10b981; }
.bias-BEARISH { color: #ef4444; }
.bias-NEUTRAL { color: #6b7280; }
```

### Watchlist Panel Features

- **Tabs**: Price, Performance, Risk, Technicals
- **Columns**: Symbol, Last Price, % Change, Volume
- **Click action**: Opens symbol drawer (does NOT affect runner)

### Scanner Panel Features

- **Columns**: Symbol, Strategy, Timeframe, Bias, Signal State, Confidence, Last Bar, Status
- **Sorting**: By status priority (RUNNING > TRIGGERED > WAITING > COOLDOWN > DISABLED)
- **Click action**: Opens symbol drawer

### Symbol Drawer Features

- **Analytics**: Latest bias, confidence
- **Active Strategies**: List with status badges
- **Recent Signals**: Chronological list (last 10)
- **Regime & Trades**: Market regime info + last trade

---

## Phase 5: Runner Integration

Add to `core/runner.py` in the bar processing loop:

```python
def _update_runner_state(self, symbol: str, strategy: BaseStrategy,
                          signal: Optional[SignalEvent], bar: OHLCVBar):
    """Persist runner state to DuckDB for UI consumption."""
    from core.data.duckdb_client import db_cursor

    status = "RUNNING"
    if strategy.strategy_id in self._disabled_strategies:
        status = "DISABLED"
    elif not strategy.is_enabled:
        status = "DISABLED"

    signal_state = "PENDING"
    current_bias = "NEUTRAL"
    confidence = 0.0

    if signal:
        signal_state = "TRIGGERED"
        current_bias = signal.signal_type.value
        confidence = signal.confidence

    try:
        with db_cursor() as conn:
            conn.execute("""
                INSERT INTO runner_state
                (symbol, strategy_id, timeframe, current_bias, signal_state,
                 confidence, last_bar_ts, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (symbol, strategy_id) DO UPDATE SET
                    current_bias = EXCLUDED.current_bias,
                    signal_state = EXCLUDED.signal_state,
                    confidence = EXCLUDED.confidence,
                    last_bar_ts = EXCLUDED.last_bar_ts,
                    status = EXCLUDED.status,
                    updated_at = CURRENT_TIMESTAMP
            """, [
                symbol,
                strategy.strategy_id,
                '1m',
                current_bias,
                signal_state,
                confidence,
                bar.timestamp,
                status
            ])
    except Exception as e:
        # Non-critical: log but don't interrupt runner
        print(f"[RUNNER] Failed to update state: {e}")
```

---

## Polling Strategy

| Endpoint | Interval | Rationale |
|----------|----------|-----------|
| `/api/watchlist` | 5 seconds | Market data updates per bar (1m); 5s is sufficient |
| `/api/scanner-state` | 2 seconds | Runner state can change rapidly during signal processing |
| `/api/symbol-context/<symbol>` | On-demand | Only fetched when drawer opens; no polling needed |

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         RUNNER (Write Path)                      │
│                                                                  │
│  TradingRunner.process_bar()                                     │
│       │                                                          │
│       ├── strategy.process_bar(bar, context) → SignalEvent      │
│       │                                                          │
│       └── _update_runner_state(symbol, strategy, signal, bar)   │
│                    │                                             │
│                    ▼                                             │
│            ┌──────────────┐                                      │
│            │ runner_state │  (DuckDB)                            │
│            └──────────────┘                                      │
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼ (Read Only)
┌─────────────────────────────────────────────────────────────────┐
│                      FLASK (Read Path)                           │
│                                                                  │
│  ScannerFacade.get_live_scanner_state()                         │
│       │                                                          │
│       └── db_cursor(read_only=True)                             │
│                    │                                             │
│                    ▼                                             │
│            ┌──────────────┐                                      │
│            │ runner_state │  (DuckDB)                            │
│            └──────────────┘                                      │
│                    │                                             │
│                    ▼                                             │
│            JSON Response → Frontend                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Order

1. **Schema** - Add tables to `schema.py` and bootstrap
2. **Facade** - Create `scanner_facade.py` with all methods
3. **Routes** - Update `scanner.py` routes to use facade
4. **Template** - Rewrite with dual-panel layout
5. **Runner** - Add `_update_runner_state()` hook
6. **Testing** - Verify end-to-end data flow

---

## Verification Checklist

### Test 1: Schema Bootstrap
```bash
python -c "from core.data.schema import init_tables; init_tables()"
# Verify tables exist in DuckDB
```

### Test 2: Facade Methods
```python
from app_facade.scanner_facade import ScannerFacade
print(ScannerFacade.get_watchlist_snapshot())
print(ScannerFacade.get_live_scanner_state())
print(ScannerFacade.get_symbol_context("NIFTY"))
```

### Test 3: API Endpoints
```bash
curl http://localhost:5000/scanner/api/watchlist
curl http://localhost:5000/scanner/api/scanner-state
curl http://localhost:5000/scanner/api/symbol-context/NIFTY
```

### Test 4: UI Flow
1. Load `/scanner/` page
2. Verify both panels render with data
3. Click a watchlist row → drawer opens
4. Click a scanner row → drawer updates
5. Confirm no console errors, polling works

---

## Architectural Compliance Checklist

| Rule | Implementation | Compliance |
|------|----------------|------------|
| No strategy logic in Flask | Facade only reads from DB | ✅ |
| No execution triggers from UI | No POST/PUT/DELETE routes | ✅ |
| Flask is READ-ONLY | All queries use `read_only=True` | ✅ |
| DuckDB is single source of truth | All data from tables | ✅ |
| Runner sovereignty absolute | Runner writes, Flask reads | ✅ |
| No WebSocket in Flask | Polling only (2s/5s intervals) | ✅ |
| UI reflects state | No side effects on click | ✅ |

---

## Notes

- **No chart libraries** used unless explicitly approved
- **Tailwind CSS** for all styling (existing pattern)
- **Glassmorphism** effects via `.glass` class
- **Font Awesome** icons for visual elements
- **Graceful degradation** - missing data shows placeholders, not errors
