## Comprehensive Exploration Report: Trading Platform Execution Layer

Analysis of the execution, configuration, and instrument systems. Here's a detailed breakdown:
---
### 1. **Core Execution Directory Structure (`core/execution/`)**
The execution layer contains **33 Python files** organized into:
**Core Order & Execution Files:**
- `handler.py` — Main ExecutionHandler (2+ phases of signal processing)
- `order_models.py` — NormalizedOrder, OrderMetadata, OrderSide
- `order_lifecycle.py` — FillEvent, OrderStatus enums
- `order_tracker.py` — OrderState and OrderTracker for order lifecycle management
- `position_tracker.py` — PositionTracker (position state management)
- `position_models.py` — Position and PositionSide data structures
**Risk & Safety Files:**
- `risk_manager.py` — RiskManager for pre-trade evaluation
- `risk_models.py` — RiskStatus and RiskDecision
- `rules.py` — ExecutionRuleError and 3 enforcement functions (idempotency, risk clearance, authority)
**Financial Tracking:**
- `pnl_tracker.py` — PnL calculations
- `margin_tracker.py` — Margin usage tracking
- `equity.py` — Equity position tracking
- `future.py` — Futures-specific logic
- `capital_allocator.py` — Capital allocation
- `sizing_policy.py` — Position sizing logic
**Persistence & Data:**
- `persistence/execution_store.py` — ExecutionStore
- `persistence/order_repository.py` — OrderRepository
- `persistence/fill_repository.py` — FillRepository
- `persistence/position_repository.py` — PositionRepository
**Advanced Features:**
- `groups/group_tracker.py`, `order_group.py`, `group_pnl.py` — Order grouping (Phase 9B)
- `pixityAI_risk_engine.py` — PixityAI-specific risk
- `reconciliation.py` — ReconciliationEngine
- `health_monitor.py`, `recorder.py`, `write_buffer.py` — Auxiliary systems
---
### 2. **ExecutionHandler: `process_signal()` Flow**
**Location:** `/d/BOT/root/core/execution/handler.py` (lines 367-561)
**Architecture (8 Phases):**
```
Phase 0: Authority & Idempotency Enforcement
  ├─ enforce_execution_authority(not self._processing_signal)
  ├─ Derive signal_id (from signal or hash)
  └─ enforce_signal_idempotency(signal_id, self._seen_signals)
Phase 1: Observability & Kill Switches
  ├─ Increment metrics.signals_received
  ├─ Check manual STOP file
  ├─ Check kill_switch flag
  ├─ Check max_trades_per_day
  └─ Check max_drawdown_limit
Phase 2: Risk Checks (BEFORE Order Creation)
  ├─ _check_risk_limits() — positional constraints
  ├─ _check_greek_limits() — Phase 9C (portfolio Greeks)
  └─ enforce_risk_clearance()
Phase 3: Position Stacking Guard
  └─ has_open_position() check — MAX 1 position per symbol
Phase 4: TLP Context Capture
  └─ CaptureEngine.capture_context() — snapshot market state
Phase 5: Order Creation (NormalizedOrder)
  ├─ Determine Side (BUY/SELL/EXIT based on signal type & position)
  ├─ Determine Quantity (strategy_qty OR confidence-scaled)
  ├─ Wrap in OrderMetadata (confidence + strategy_metadata)
  └─ Create NormalizedOrder with:
      • instrument (InstrumentParser.parse())
      • side, quantity, order_type=MARKET
      • signal_id, strategy_id, timestamp
      • correlation_id (UUID)
      • metadata with TLP context
Phase 6: Pre-Trade Risk Integration
  └─ risk_manager.evaluate(order) → RiskDecision
Phase 7: Broker Execution
  ├─ broker.place_order(order) → broker_id
  └─ PaperBroker: Simulate immediate fill with FillEvent
**Returns:** Optional[NormalizedOrder] — order on success, None if rejected/killed
```
**Key Mandatory Checks:**
- Lines 390-407: Mandatory `sl_distance` and `risk_r` in signal.metadata for non-EXIT signals
- Lines 437-440: Position stacking guard (blocks new entry if position open)
- Lines 666-665: Risk limits validation
---
### 3. **NormalizedOrder Model**
**Location:** `/d/BOT/root/core/execution/order_models.py` (lines 24-62)
```python
@dataclass(frozen=True)
class NormalizedOrder:
    instrument: Instrument          # Equity/Option/Future
    side: OrderSide                 # BUY | SELL
    quantity: int
    order_type: OrderType           # MARKET (only type supported)
    strategy_id: str
    signal_id: str                  # Links to SignalEvent
    timestamp: datetime
    correlation_id: UUID = field(default_factory=uuid4)  # Order ID
    metadata: OrderMetadata         # Confidence + strategy_metadata
    group_id: Optional[UUID] = None # Phase 9B: Multi-leg orders
```
**Metadata Structure:**
```python
@dataclass(frozen=True)
class OrderMetadata:
    original_confidence: float
    strategy_metadata: Dict[str, Any]  # Flexible key-value store
```
**Properties:**
- `symbol` — returns `instrument.symbol`
- `instrument_type` — returns `instrument.type`
- `to_dict()` — serialization
**No LOT_SIZE/MULTIPLIER in NormalizedOrder** — stored in Instrument instead.
---
### 4. **PositionTracker & Position Models**
**Location:** `/d/BOT/root/core/execution/position_tracker.py` (lines 49-137)
**`update_from_fill()` Method Signature:**
```python
def update_from_fill(self, fill: FillEvent, persist: bool = True) -> float:
    """
    Update position from incremental fill.
    Returns: Realized PnL (float)
    """
```
**Implements:**
1. **Signed Quantity Logic** — net_quantity = current + fill_signed_qty
2. **Position State Transitions** — FLAT → LONG/SHORT, flipping, reducing, increasing
3. **Average Price Calculation** — weighted average on open, unchanged on reduce, fill price on flip
4. **Realized PnL Calculation** (lines 82-97):
   - Only computed when reducing/flipping (not on pure opens)
   - `realized_pnl = price_diff × qty_closed × multiplier × direction_sign`
   - **Uses `instrument.multiplier`** for scaled instruments (futures/options)
**No Explicit `lot_size` Parameter** — but `instrument.multiplier` is applied in PnL calc.
**Persistence:** Optional persist=True calls `position_repo.save()`
**Other Key Methods:**
- `get_position(symbol)` — returns Position (FLAT if none)
- `has_open_position(symbol)` — checks if side != FLAT
- `net_quantity(symbol)` — signed quantity (+ LONG, - SHORT)
- `get_all_positions()` — Dict[symbol, Position]
---
### 5. **PixityAI Configuration**
**Location:** `/d/BOT/root/core/models/pixityAI_config.json`
```json
{
    "strategy_id": "pixityAI_meta",
    "lookback": 100,
    "swing_period": 5,
    "reversion_k": 2.0,
    "sl_mult": 1.0,                    // SL = 1 × ATR
    "tp_mult": 2.0,                    // TP = 2 × ATR (1:2 R:R)
    "time_stop_bars": 12,              // Time stop = 12 bars
    "bar_minutes": 15,
    "long_threshold": 0.45,
    "short_threshold": 0.45,
    "cooldown_bars": 3,
    "risk_per_trade": 500.0,           // Rs 500 per trade
    "max_daily_trades": 10,
    "skip_meta_model": true,           // Meta-model disabled (anti-predictive)
    "skip_reversion": true,            // Reversion signals disabled
    "preferred_timeframe": "15m",      // 15m timeframe (> 1h)
    "symbols": ["NSE_EQ|INE155A01022", "NSE_EQ|INE118H01025"],
    "notes": "Meta-model anti-predictive. Raw events at 15m have edge. TataPwr & BajajFin walk-forward validated."
}
```
**Key Config Fields:**
- NO explicit `multiplier`, `lot_size`, or F&O-specific fields
- Only equities listed (NSE_EQ format)
- Risk sizing via `risk_per_trade` (Rs 500) — not via multiplier
---
### 6. **Credentials & Config Files**
**`config/credentials.json`:**
- Upstox API key, secret, access token
- User ID: 4PC6HK (Subhash Chandran Nair)
- Supported exchanges: NFO, NSE, MCX, BSE, BFO, BCD, CDS
- **NO `fo_stocks` table** — standard Upstox V2 REST/WebSocket
**`config/market_universe.json`:**
- 2 indices: NSE_INDEX|Nifty 50, NSE_INDEX|Nifty Bank
- 45 equity stocks (Nifty 50 constituents)
- Total: 47 symbols (V9 PM Scalper + Stock Day-Type focus)
- Format: NSE_EQ|INE{ISIN} for equities
- **No F&O instruments listed**
**`config/settings.py`:**
- `DB_PATH` = "data/trading_bot.duckdb"
- `LOG_LEVEL` = "INFO"
- `load_zmq_config()` — loads from `zmq.json`
---
### 7. **Instrument Models**
**Location:** `core/instruments/`
**Base Class: `Instrument` (frozen dataclass)**
```python
@dataclass(frozen=True)
class Instrument:
    symbol: str
    type: InstrumentType          # EQUITY | FUTURE | OPTION
    multiplier: float = 1.0       # Price multiplier (1.0 for equity, >1 for derivatives)
```
**Concrete Types:**
1. **Equity** (`equity.py`)
   - `multiplier = 1.0` (always)
   - No additional fields
2. **Future** (`future.py`)
   - `underlying: str` (e.g., "NIFTY50")
   - `expiry: date`
   - `multiplier: float` (contract multiplier, e.g., 50 for NIFTY50)
3. **Option** (`option.py`)
   - `underlying: str`
   - `expiry: date`
   - `strike: float`
   - `option_type: OptionType` (CALL | PUT)
   - `lot_size: int` (default 1, typically 50-100 per exchange)
   - `multiplier: float` (payout multiplier)
**InstrumentParser** (`instrument_parser.py`):
- Regex: `^([A-Z]+)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$` for options
- Format: NIFTY28JAN2522500CE
- Fallback: Any non-matching string → Equity(symbol)
---
### 8. **Execution Rules (Constitutional Invariants)**
**Location:** `/d/BOT/root/core/execution/rules.py`
```python
def enforce_signal_idempotency(signal_id: str, seen_signals: Set[str])
    # Ensures signal never processed 2x in session
def enforce_risk_clearance(risk_approved: bool, reason: str)
    # Ensures execution never proceeds if risk rejected
def enforce_execution_authority(authorized: bool)
    # Ensures execution originates from ExecutionHandler.process_signal()
    # Flags: not self._processing_signal (mutual exclusion)
```
All raise `ExecutionRuleError` on violation — considered non-recoverable.
---
### 9. **Risk Manager & Position Stacking**
**RiskManager** (`risk_manager.py`):
```python
def evaluate(order: NormalizedOrder, 
             trades_today: int, 
             max_trades_per_day: int) -> RiskDecision
```
**Checks:**
1. Global kill switch flag
2. Daily trade limit (trades_today >= max)
3. Max order quantity (order.qty > limit)
4. Symbol allow-list (if defined)
5. Symbol deny-list
**Position Stacking Guard** (ExecutionHandler line 437-440):
```python
if signal.signal_type != SignalType.EXIT:
    if self.position_tracker.has_open_position(signal.symbol):
        return None  # Block new entry
```
---
### 10. **Realized PnL & Fill Processing Flow**
**Path:** handler.py → `_handle_broker_fill()` → `position_tracker.update_from_fill()`
**Key Step (position_tracker.py lines 82-97):**
```python
if is_reducing_or_flipping and current_signed_qty != 0:
    qty_closed = min(abs(current_signed_qty), abs(fill_signed_qty))
    price_diff = fill_price - pos.avg_price
    realized_pnl = price_diff * qty_closed * pos.instrument.multiplier \
                   * (1 if current_signed_qty > 0 else -1)
```
**Formula:** `realized_pnl = (fill_price - avg_price) × qty_closed × multiplier × direction`
For **equities** (multiplier=1.0):
- realized_pnl = (fill_price - avg_price) × qty_closed
For **futures/options** (multiplier>1.0):
- realized_pnl scales by contract multiplier (e.g., 50 for NIFTY50)
---
### 11. **Signal Event Structure**
**Location:** `/d/BOT/root/core/events.py` (lines 73-80)
```python
@dataclass(frozen=True)
class SignalEvent:
    strategy_id: str
    symbol: str
    timestamp: datetime
    signal_type: SignalType          # BUY | SELL | EXIT | NEUTRAL
    confidence: float                # [0, 1]
    metadata: Dict[str, Any]         # MUST contain:
                                     #  - sl_distance (float)
                                     #  - risk_r (float)
    context: Optional[TradeStructuralContext] = None
```
**Mandatory Metadata for Entry Signals (lines 390-404 in handler.py):**
- `sl_distance` — absolute price distance to stop loss
- `risk_r` — risk unit (used for sizing, fee impact calc)
**Optional Strategy Metadata:**
- `quantity` — explicit position size override
- `percentile`, `rank` — signal quality metrics
- Many other context fields populated by capture engine
---
## Summary Table
| Component | File | Key Classes/Functions | Multiplier Support |
|-----------|------|----------------------|-------------------|
| **Order Intake** | handler.py | ExecutionHandler.process_signal() | Via instrument.multiplier |
| **Order Model** | order_models.py | NormalizedOrder, OrderMetadata | No (in Instrument) |
| **Position State** | position_tracker.py | PositionTracker.update_from_fill() | YES (used in PnL calc) |
| **Position Model** | position_models.py | Position, PositionSide | Via instrument.multiplier |
| **Instruments** | instrument_*.py | Equity, Future, Option | YES (1.0, >1.0, flexible) |
| **Risk** | risk_manager.py | RiskManager.evaluate() | No (symbol-level rules) |
| **Rules** | rules.py | 3 enforcement functions | N/A |
| **Config** | pixityAI_config.json | Strategy params | No F&O fields |
| **Credentials** | credentials.json | Upstox API | NFO/NSE/MCX/BSE/BFO/BCD/CDS |
---
This architecture cleanly separates **signal → order → position → pnl** flows with embedded risk enforcement at every phase, and uses `instrument.multiplier` to handle scaled instruments (futures/options) in PnL calculations.
agentId: af73bc5f4cd9801fd (for resuming to continue this agent's work if needed)
<usage>total_tokens: 60013
tool_uses: 25
duration_ms: 69911</usage>
Task
Explore signal flow and broker layer
Excellent! Now I have all the information needed. Let me provide a comprehensive report:
---
Show more

Good — I have a complete picture of the codebase. Let me write my verdict before presenting the plan.


Created architect's verdict document for Options Bridge plan
Created architect's verdict document for Options Bridge plan
