# Central Logging System + Status Bar + Log Viewer

## Overview
Add a centralized logging utility, replace `print()` statements, expose logs via REST API, and build a bottom status bar with an integrated log viewer drawer in the Flask UI.

---

## Phase 1: Central Logger Utility

### Create `core/logging/__init__.py`
- Export `setup_logger` and `TelemetryHandler`

### Create `core/logging/logger.py`
- `setup_logger(name, log_file=None, level=None, telemetry_publisher=None, console=True)` function
- `RotatingFileHandler` (10MB, 5 backups) — each process writes to `logs/<name>.log` by default
- `StreamHandler` for console
- `TelemetryHandler(logging.Handler)` class that wraps `TelemetryPublisher.publish_log()` — exception-safe, non-blocking
- `_configured_loggers: set` guard against duplicate handlers
- `logger.propagate = False` to prevent root logger duplication
- Format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s` (matches existing convention)
- Level defaults to `config.settings.LOG_LEVEL` ("INFO")

---

## Phase 2: Log File Reader + REST API

### Create `core/logging/log_reader.py`
- `tail_log_file(file_path, lines=300, level_filter, source_filter)` — seek-from-end reader, returns `[{timestamp, source, level, message}]`
- `count_errors(file_path, window_minutes=60)` — counts ERROR entries in time window
- `get_available_log_files()` — lists all `.log` files in `logs/` directory
- `_parse_log_line()` using regex on standard format

### Modify `flask_app/blueprints/ops/routes.py`
- Add `GET /ops/api/logs?file=<name>&level=<level>&source=<source>&lines=300` — returns JSON log entries
- Add `GET /ops/api/error-count?window_minutes=60` — returns `{error_count: N}`
- Add `GET /ops/api/log-files` — returns list of available log files for the source dropdown

---

## Phase 3: Status Bar + Log Viewer (base.html)

### Modify `flask_app/templates/base.html`

**Status Bar** (fixed bottom, after sidebar offset `left-64`):
- Market status indicator (Open/Closed dot + label)
- Broker/WS status indicator (Connected/Disconnected)
- DB status indicator
- Error count badge
- "Logs" toggle button with chevron

**Log Viewer Drawer** (slides up from status bar):
- `max-height` CSS transition (0 → 380px)
- Toolbar: level dropdown, source dropdown (populated from `/ops/api/log-files`), pause/resume, auto-scroll toggle, refresh button
- Monospace log content area (`max-height: 320px`, scrollable)
- Log entries color-coded by level (ERROR=red, WARNING=amber, INFO=slate, DEBUG=dim)

**JavaScript** (inline in base.html):
- `pollStatusBar()` — fetches `/ops/api/status` + `/ops/api/error-count` every 15s
- `toggleLogDrawer()` — open/close with CSS transition
- `refreshLogs()` — fetches `/ops/api/logs` with current filters
- `connectLogSSE()` — opens `EventSource('/api/telemetry/stream')`, filters for `telemetry.logs.*` messages
- `appendLogEntry()` — adds live entries respecting pause/filter/autoscroll state
- DOM capped at 500 entries

**CSS additions**:
- `main { padding-bottom: 3rem; }` to prevent content hidden behind status bar
- `.log-ERROR`, `.log-WARNING`, `.log-INFO`, `.log-DEBUG` color classes
- Replace existing `<footer>` with status bar (move copyright into status bar or remove)

---

## Phase 4: Replace print() in Core + Runtime Scripts

### Priority 1 — Core runtime files:

| File | Changes |
|------|---------|
| `core/runner.py` (line 24) | Replace `logging.getLogger(__name__)` with `setup_logger("trading_runner")`. Replace ~16 `print()` calls with `logger.info/error` |
| `core/execution/handler.py` (line 298) | Replace 1 `print()` with `logger.info()` |
| `flask_app/blueprints/database.py` | Replace 2 `print()` with `logger.error()` |
| `flask_app/blueprints/backtest.py` | Replace 1 `print()` with `logger.error()` |

### Priority 2 — Production scripts:

| File | Changes |
|------|---------|
| `scripts/market_ingestor.py` (lines 28-36) | Remove `basicConfig`, use `setup_logger("market_ingestor", telemetry_publisher=self.telemetry)` |
| `scripts/unified_live_runner.py` (lines 64-71) | Remove manual handlers, use `setup_logger("unified_live_runner")` |
| `scripts/live_runner.py` (line 44) | Remove `basicConfig`, use `setup_logger("live_runner")` |
| `scripts/run_flask.py` | Replace 7 `print()` with logger calls |
| `scripts/health_check.py` | Replace 6 `print()` with logger calls |
| `scripts/eod_rollover.py` | Replace 2 `print()` with logger calls |
| `scripts/strategy_runner_node.py` | Replace 5 `print()` with logger calls |
| `scripts/market_data_node.py` | Replace 5 `print()` with logger calls |
| `scripts/market_scanner_node.py` | Replace prints with logger calls |

### Priority 3 — Utility/dev scripts (batch conversion):
- Remaining scripts in `scripts/` (~30 files, ~220 print calls)
- Pattern: add `from core.logging import setup_logger; log = setup_logger("<script_name>")` and replace `print()` → `log.info()`

---

## Phase 5: Logger Naming Convention

| Process/Module | Logger Name |
|---------------|-------------|
| Market Ingestor | `market_ingestor` |
| Live Runner | `live_runner` |
| Unified Live Runner | `unified_live_runner` |
| Trading Runner (core) | `trading_runner` |
| Strategy Runner Node | `strategy_runner` |
| Market Data Node | `market_data_node` |
| Flask App | `flask_app` |
| Scanner | `scanner:<name>` |
| Strategy | `strategy:<name>` |
| Health Check | `health_check` |
| EOD Rollover | `eod_rollover` |

Each logger writes to `logs/<logger_name>.log` by default.

---

## Files to Create
1. `core/logging/__init__.py`
2. `core/logging/logger.py`
3. `core/logging/log_reader.py`

## Files to Modify
1. `flask_app/templates/base.html` — status bar + log drawer + JS
2. `flask_app/blueprints/ops/routes.py` — 3 new API endpoints
3. `core/runner.py` — replace print() + setup_logger
4. `core/execution/handler.py` — replace 1 print()
5. `flask_app/blueprints/database.py` — replace 2 print()
6. `flask_app/blueprints/backtest.py` — replace 1 print()
7. `scripts/market_ingestor.py` — replace basicConfig with setup_logger
8. `scripts/unified_live_runner.py` — replace manual handlers
9. `scripts/live_runner.py` — replace basicConfig
10. `scripts/run_flask.py` — replace print()
11. `scripts/health_check.py` — replace print()
12. `scripts/eod_rollover.py` — replace print()
13. `scripts/strategy_runner_node.py` — replace print()
14. `scripts/market_data_node.py` — replace print()
15. `scripts/market_scanner_node.py` — replace print()
16. Remaining ~20 utility scripts — batch print() → logger conversion

## Key Reusable Infrastructure (no changes needed)
- `core/messaging/telemetry.py` — `TelemetryPublisher.publish_log(level, msg)` already exists
- `flask_app/__init__.py` — `TelemetryBridge` + SSE endpoint already forward logs to browser
- `/ops/api/status` — existing endpoint reused by status bar polling

---

## Architecture Flow
```
[Python Module] → setup_logger(name)
    ├── RotatingFileHandler → logs/<name>.log
    ├── StreamHandler → console
    └── TelemetryHandler → TelemetryPublisher.publish_log()
                               ↓ (ZMQ)
                          TelemetryBridge (flask_app)
                               ↓ (SSE)
                          Browser EventSource
                               ↓
                          Log Viewer Drawer (base.html)

[Browser] → fetch /ops/api/logs → log_reader.tail_log_file() → logs/*.log
[Browser] → fetch /ops/api/error-count → log_reader.count_errors()
[Browser] → fetch /ops/api/status → existing ops endpoint (status bar indicators)
```

---

## Verification Plan
1. Start Flask app → status bar visible on all pages with indicators
2. Open log drawer → fetches and displays recent logs from file
3. Run `scripts/market_ingestor.py` → verify logs appear in `logs/market_ingestor.log`
4. If telemetry is connected → live log entries stream into the drawer via SSE
5. Filter by ERROR → only error lines shown
6. Filter by source → only matching source shown
7. Pause → new entries stop appearing; Resume → they resume
8. Error count badge updates on status bar
9. `grep -r "print(" core/ flask_app/` returns 0 results in production code
10. Run existing tests: `pytest tests/` — all pass
