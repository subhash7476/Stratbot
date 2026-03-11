# MCX Gold Options Commodity Page Integration Report

## 1. Executive Summary

This report documents the completed integration of the MCX Gold Options strategy into the Commodity Page.

The work focused on integration only (not redesign of quantitative logic), wiring existing modules into a deterministic orchestrator flow and exposing results through API, persistence, execution hooks, and UI.

Implemented objectives:
- Deterministic strategy snapshots every 5 seconds
- Pipeline integration:
  - volatility regime
  - strike selection
  - liquidity filter
  - premium risk sizing
  - execution candidate
- Snapshot enrichment with Greeks and structured metrics
- Snapshot persistence for audit/diagnostics
- AUTO_TRIGGER execution through existing `ExecutionHandler`
- Duplicate-trade protection (dedupe, cooldown, state machine, contract lock)
- Commodity API and UI upgrades for real-time visibility and audit export

Validation result:
- Full test suite passed: `109 passed`

---

## 2. Scope and Constraints Followed

Followed constraints:
- No redesign of quant modules
- Reused existing execution and database layers
- Added orchestration/API/UI/persistence wiring only
- Implemented typed Python and deterministic schema outputs

Quant modules reused as-is:
- `analytics/options_greeks.py`
- `strategy/regime/volatility_regime.py`
- `strategy/options/strike_selector.py`
- `strategy/filters/liquidity_filter.py`
- `risk/premium_risk_model.py`
- `analytics/structured_metrics.py`
- `core/backtest/usdinr_attribution.py`

---

## 3. New Orchestration Layer

### 3.1 File Added
- `services/commodity_strategy_orchestrator.py`

### 3.2 Core Responsibilities
- Builds deterministic strategy snapshot via:
  - `get_latest_snapshot(now: datetime) -> CommodityStrategySnapshot`
- Enforces `snapshot_interval_seconds = 5`
- Prevents over-sampling via snapshot rate guard
- Applies pipeline in strict order:
  1. Volatility regime
  2. Strike selection
  3. Liquidity filter
  4. Premium risk sizing
  5. Execution candidate
- Enriches snapshot with:
  - Greeks from `compute_greeks_with_iv`
  - structured metrics from `build_strategy_metrics_snapshot`
- Selects expiry with rollover rule:
  - prefer current expiry
  - switch to next expiry if trading days remaining < 3

### 3.3 Deterministic Snapshot Contract
Implemented dataclass:
- `CommodityStrategySnapshot`

Fields:
- `snapshot_id`
- `regime`
- `strike_selection`
- `liquidity_check`
- `risk_sizing`
- `greeks`
- `metrics`
- `decision`
- `rejection_reasons`
- `data_freshness`
- `audit_meta`
- `execution_status`

`to_dict()` returns fixed key order for deterministic downstream behavior.

### 3.4 Snapshot ID Determinism
Uses hash of:
- regime
- expiry
- strike
- liquidity pass flag
- risk size
- timestamp bucket

Timestamp bucket definition:
- `timestamp_bucket = floor(now_epoch / snapshot_interval_seconds)`

This ensures consistency if interval changes from 5 seconds in the future.

---

## 4. Data Freshness Guards

Implemented hard rejects:
- option chain stale if age > 10 seconds
- USDINR stale if age > 30 seconds

On stale data:
- `data_freshness.data_fresh = false`
- `decision = "REJECT"`
- `execution_status = "REJECTED"`

---

## 5. Execution Wiring (AUTO_TRIGGER)

### 5.1 Trigger Conditions
Execution is attempted only when all conditions are satisfied:
- `decision == "ACCEPT"`
- `liquidity_check.trade_allowed == true`
- `data_freshness.data_fresh == true`

### 5.2 Guard Order (Strict)
Before execution, guards run in exact sequence:
1. data freshness check
2. liquidity check
3. snapshot ID dedupe
4. cooldown check
5. state machine check
6. execution

### 5.3 Signal Payload to ExecutionHandler
Signals routed via existing `ExecutionHandler.process_signal()` include:
- `execution_mode = "option"`
- `selected_contract`
- `quantity`
- `liquidity_pass_gate`
- required risk fields for compatibility (`sl_distance`, `risk_r`)

### 5.4 Duplicate Trade Protection
Implemented protections:
- state machine per contract:
  - `FLAT`
  - `PENDING_ENTRY`
  - `OPEN`
  - `PENDING_EXIT`
- snapshot hash dedupe:
  - skip if already executed (`SKIPPED_DUPLICATE`)
- cooldown:
  - 30 seconds per contract (`SKIPPED_COOLDOWN`)
- explicit contract lock:
  - if state in `[PENDING_ENTRY, OPEN]`, selected contract is kept and strike reselection is skipped

### 5.5 Execution Status Field
Added snapshot field:
- `execution_status`

Values:
- `NONE`
- `EXECUTED`
- `SKIPPED_DUPLICATE`
- `SKIPPED_COOLDOWN`
- `REJECTED`

---

## 6. Persistence and Audit Trail

### 6.1 Schema Update
Added table schema in DB bootstrap:
- `commodity_strategy_snapshots`

Columns:
- `timestamp`
- `snapshot_id`
- `regime`
- `selected_strike`
- `liquidity_pass`
- `risk_size`
- `decision`
- `rejection_reason`
- `metrics_json`
- `snapshot_json`

### 6.2 Write Path
- Persisted every snapshot through `DatabaseManager.trading_writer`
- Includes rejected snapshots for diagnostics and replay

### 6.3 Decision Trace Coverage
Persisted trace includes:
- decision
- rejection reason(s)
- selected strike
- regime
- risk size

---

## 7. Commodity API Integration

### 7.1 Updated Endpoint
`GET /commodities/api/state`

Now includes:
- existing legacy fields (`summary`, `active_positions`, `recent_signals`, `closed_trades`)
- `strategy_snapshot`
- `audit_meta`

### 7.2 New Endpoints
- `GET /commodities/api/strategy-snapshot`
- `GET /commodities/api/metrics`
- `GET /commodities/api/usdinr-attribution`

Attribution endpoint expects:
- `run_id_without`
- `run_id_with`

### 7.3 Deterministic Response Shape
- Deterministic key order preserved
- JSON key sorting disabled for stability in audit/UI

---

## 8. Commodity UI Integration

Updated commodity template to poll every 5 seconds and render:
- Volatility regime card
- Strike selection card
- Liquidity status card
- Premium risk sizing card
- Greeks summary
- USDINR filter impact placeholder/message
- Rejection reasons and rejection timeline
- Legacy summary retained for compatibility

Added:
- Audit Export button
  - downloads latest snapshot payload as JSON

---

## 9. Files Added / Modified

### Added
- `services/commodity_strategy_orchestrator.py`
- `tests/execution/test_commodity_orchestrator.py`
- `tests/flask_app/test_commodities_integration.py`

### Modified
- `core/database/schema.py`
- `app_facade/commodities_facade.py`
- `flask_app/blueprints/commodities.py`
- `flask_app/templates/commodities/index.html`
- `flask_app/__init__.py`

---

## 10. Testing and Verification

### 10.1 New Coverage
Added tests for:
- regime pipeline integration
- expiry rollover behavior
- liquidity rejection path
- freshness gate rejection
- deterministic snapshot ID behavior
- accepted snapshot execution intent
- duplicate snapshot skip behavior
- commodity API schema and endpoint responses

### 10.2 Final Result
- Full suite execution completed successfully:
  - `109 passed`

---

## 11. Operational Notes

- AUTO_TRIGGER is active at orchestrator level.
- If `ExecutionHandler` is unavailable in app context, snapshots still generate/persist and execution is marked rejected with reason.
- Existing legacy commodity signal summary remains available for continuity while strategy snapshot becomes primary decision surface.

---

## 12. Audit Readiness Checklist

This implementation is audit-ready for ChatGPT review because it provides:
- deterministic snapshot schema and IDs
- explicit guard ordering before execution
- persisted decision trace for replay
- explicit execution status outcomes
- endpoint-level deterministic payloads
- test-backed behavior for key failure/safety cases
