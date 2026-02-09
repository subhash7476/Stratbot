# Live Scanner Pipeline Critical Bug Fixes

## Context

The PixityAI scanner integration has been fully implemented by Gemini 3 Pro, but the live scanner is **not behaving properly** during operation. User reports show:

1. **DuckDB file locking errors** occurring continuously:
   ```
   File is already open in F:\conda-envs\trading-env\python.exe (PID 18312)
   Tick buffer flush failed: IO Error: Cannot open file "d:\bot\root\data\live_buffer\ticks_today.duckdb":
   The process cannot access the file because it is being used by another process.
   ```

2. **Scanner not displaying signals** - The scanner page shows empty or stale state despite the unified runner being active.

**Investigation Results:**
Through comprehensive code exploration, we've identified **4 critical bugs** causing these issues:
- Signal persistence field mismatch (bar_ts vs timestamp)
- Missing lock timeouts causing deadlocks
- DuckDB multi-threaded access conflicts
- Silent exception handling hiding failures

These bugs prevent the scanner from functioning correctly in production.

## Root Cause Analysis

### Bug #1: Signal Persistence Field Mismatch (CRITICAL)

**Location:** `core/database/writers.py:176`

**Problem:**
```python
_to_str(getattr(signal, 'bar_ts', None))  # ❌ SignalEvent has no 'bar_ts' field
```

But `SignalEvent` (defined in `core/events.py:54-60`) uses `timestamp`:
```python
@dataclass(frozen=True)
class SignalEvent:
    strategy_id: str
    symbol: str
    timestamp: datetime  # ✅ Field is called 'timestamp' not 'bar_ts'
    signal_type: SignalType
    confidence: float
```

**Impact:**
- All signals saved with `NULL` for bar_ts column
- Scanner queries return incomplete data
- Runner state shows no timestamp for triggered signals
- User sees empty scanner even when signals generated

---

### Bug #2: Missing Lock Timeouts (HIGH PRIORITY)

**Location:** `core/database/manager.py`

**Problem:**
- Line 222: `signals_writer()` has NO timeout on WriterLock
- Line 318: `config_writer()` has NO timeout on WriterLock
- Line 147: `live_buffer_writer()` has timeout=10.0 (correct)

**Code:**
```python
# signals_writer - Line 222
with WriterLock(str(lock_path)):  # ❌ No timeout, can hang indefinitely

# config_writer - Line 318
with WriterLock(str(lock_path)):  # ❌ No timeout, can hang indefinitely

# live_buffer_writer - Line 147
with WriterLock(str(lock_path), timeout=10.0):  # ✅ Has timeout
```

**Impact:**
- When unified runner's ingestor thread and trading thread both try to write config/signals
- One thread waits indefinitely for lock
- Runner state updates fail silently
- Scanner never sees updated state

---

### Bug #3: DuckDB Multi-Thread Access Conflicts (ARCHITECTURE)

**Location:** Multiple files in unified runner architecture

**Problem:**
The unified_live_runner.py runs multiple threads in the **same Python process** (PID 18312):

1. **IngestorThread** (background) - Writes to `ticks_today.duckdb` every 0.5s
2. **AggregatorThread** (within ingestor) - Reads ticks, writes candles every 1.5s
3. **TradingThread** (foreground) - Reads from live_buffer via ResamplingWrapper
4. **FlaskThread** (dashboard) - Reads from live_buffer for scanner display

**Windows DuckDB Limitation:**
DuckDB on Windows uses file-level locking. Even with Python-level WriterLock, when Thread A has an open write connection and Thread B tries to open another write connection to the **same file**, Windows denies access.

**Evidence from logs:**
```
File is already open in F:\conda-envs\trading-env\python.exe (PID 18312)
```
Same PID = same process = threading conflict, not multi-process issue.

**Impact:**
- Tick buffer flushes fail continuously
- Ticks pile up in memory
- Trading decisions made on stale data
- System performance degrades

---

### Bug #4: Silent Exception Handling (OBSERVABILITY)

**Location:** `core/runner.py:383-384`

**Problem:**
```python
except Exception as e:
    logger.error(f"Failed to update runner state: {e}")
    # ❌ Silently continues, doesn't propagate error
```

**Impact:**
- Runner state update failures are invisible to user
- Scanner appears broken but no clear error message
- Debugging requires log analysis

---

## Architecture Overview (Current State)

**Current Unified Runner Architecture:**

```
unified_live_runner.py (Single Process - PID 18312)
├─ IngestorThread (background daemon)
│  ├─ WebSocket → TickBuffer (memory)
│  ├─ TickBuffer.flush() every 0.5s → ticks_today.duckdb (WRITE)
│  └─ DbTickAggregator every 1.5s → ticks_today.duckdb (READ/WRITE)
├─ TradingThread (foreground)
│  ├─ LiveDuckDBMarketDataProvider → candles_today.duckdb (READ)
│  ├─ ResamplingMarketDataProvider (1m → 15m)
│  ├─ PixityAIMetaStrategy → generates signals
│  ├─ save_signal() → signals.db (WRITE) ❌ Bug #1
│  └─ _update_runner_state() → config.db (WRITE) ❌ Bug #2
└─ FlaskThread (dashboard)
   └─ ScannerFacade → reads config.db, signals.db (READ)
```

**Where It Breaks:**
1. ✅ Ingestor thread writes ticks successfully
2. ✅ Aggregator creates 1m candles successfully
3. ✅ TradingThread reads and resamples to 15m successfully
4. ✅ PixityAI generates signals successfully
5. ❌ **save_signal() writes NULL for bar_ts** (Bug #1)
6. ❌ **_update_runner_state() hangs on lock** (Bug #2)
7. ❌ **Tick buffer flush conflicts with aggregator** (Bug #3)
8. ❌ **Errors hidden, user sees "not behaving"** (Bug #4)

---

## Fix Implementation Tasks

### Fix #1: Correct Signal Persistence Field (CRITICAL - P0)

**File:** `core/database/writers.py`

**Line 176** - Change from:
```python
_to_str(getattr(signal, 'bar_ts', None))
```

To:
```python
_to_str(getattr(signal, 'timestamp', None))
```

**Full context (lines 161-178):**
```python
def save_signal(self, signal) -> None:
    """Persist a signal record."""
    with self.db.signals_writer() as conn:
        conn.execute(
            """
            INSERT INTO signals
            (signal_id, strategy_id, symbol, signal_type, confidence, bar_ts, status)
            VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
            """,
            [
                getattr(signal, 'signal_id', None),
                getattr(signal, 'strategy_id', None),
                getattr(signal, 'symbol', ''),
                _to_str(getattr(signal, 'signal_type', '')),
                float(getattr(signal, 'confidence', 0.0)),
                _to_str(getattr(signal, 'timestamp', None))  # ✅ Fixed
            ],
        )
```

**Why:** `SignalEvent` dataclass (core/events.py:54-60) defines field as `timestamp`, not `bar_ts`.

**Impact:** This single change will make signals visible in scanner immediately.

---

### Fix #2: Add Lock Timeouts to Prevent Deadlocks (HIGH - P0)

**File:** `core/database/manager.py`

**Two changes needed:**

**Change 1 - Line 222** (signals_writer):
```python
# BEFORE:
with WriterLock(str(lock_path)):

# AFTER:
with WriterLock(str(lock_path), timeout=10.0):
```

**Change 2 - Line 318** (config_writer):
```python
# BEFORE:
with WriterLock(str(lock_path)):

# AFTER:
with WriterLock(str(lock_path), timeout=10.0):
```

**Why:** Without timeout, threads can wait indefinitely for locks in unified mode. 10 second timeout matches live_buffer_writer pattern.

**Impact:** Runner state updates will either succeed or fail fast, preventing silent hangs.

---

### Fix #3: Improve DuckDB Thread Safety (MEDIUM - P1)

**File:** `core/database/ingestors/websocket_ingestor.py`

**Location:** Lines 52-64 (TickBuffer.flush method)

**Current Code:**
```python
def flush(self):
    if not self._buffer:
        return
    try:
        with self.db_manager.live_buffer_writer() as conns:
            # ... write ticks
    except Exception as e:
        logger.error(f"Tick buffer flush failed: {e}")
        # Silently continues
```

**Add retry logic with backoff:**
```python
def flush(self, max_retries=3):
    if not self._buffer:
        return

    for attempt in range(max_retries):
        try:
            with self.db_manager.live_buffer_writer() as conns:
                # ... write ticks
            return  # Success
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Tick buffer flush failed (attempt {attempt+1}/{max_retries}): {e}")
                time.sleep(0.1 * (attempt + 1))  # Exponential backoff
            else:
                logger.error(f"Tick buffer flush failed after {max_retries} attempts: {e}")
                # Drop oldest ticks to prevent memory overflow
                if len(self._buffer) > 1000:
                    self._buffer = self._buffer[-500:]
                    logger.warning("Dropped old ticks to prevent memory overflow")
```

**Why:** Adds resilience to temporary DuckDB lock conflicts. Prevents tick buffer from growing unbounded.

**Impact:** Reduces lock conflict errors in logs, maintains system stability.

---

### Fix #4: Add Explicit Error Propagation (LOW - P2)

**File:** `core/runner.py`

**Location:** Lines 383-384

**Enhancement:**
```python
except Exception as e:
    logger.error(f"Failed to update runner state for {symbol}/{strategy.strategy_id}: {e}", exc_info=True)
    # Optional: Re-raise if critical
    # raise
```

**Why:** `exc_info=True` logs full stack trace for debugging. Consider re-raising in strict mode.

**Impact:** Better observability for scanner state update failures.

---

### Fix #5: Add Scanner Health Check Endpoint (OPTIONAL - P3)

**File:** `flask_app/blueprints/scanner.py`

**Add new endpoint:**
```python
@scanner_bp.route('/api/health', methods=['GET'])
@login_required
def health_check():
    """System health check for debugging."""
    from core.database.manager import DatabaseManager
    db_manager = DatabaseManager()

    health = {
        "runner_state_count": 0,
        "signals_count": 0,
        "live_buffer_accessible": False,
        "last_runner_update": None
    }

    try:
        with db_manager.config_reader() as conn:
            result = conn.execute("SELECT COUNT(*) FROM runner_state").fetchone()
            health["runner_state_count"] = result[0]

            result = conn.execute("SELECT MAX(updated_at) FROM runner_state").fetchone()
            health["last_runner_update"] = result[0]
    except Exception as e:
        health["runner_state_error"] = str(e)

    try:
        with db_manager.signals_reader() as conn:
            result = conn.execute("SELECT COUNT(*) FROM signals WHERE created_at > datetime('now', '-1 day')").fetchone()
            health["signals_count"] = result[0]
    except Exception as e:
        health["signals_error"] = str(e)

    try:
        with db_manager.live_buffer_reader() as conns:
            health["live_buffer_accessible"] = True
    except Exception as e:
        health["live_buffer_error"] = str(e)

    return jsonify(health)
```

**Why:** Provides diagnostic endpoint to check if database writes are succeeding.

**Impact:** Easier troubleshooting of scanner issues.

## Verification & Testing

### Pre-Fix Verification (Confirm Bugs Exist)

**1. Check current signal persistence:**
```sql
-- Connect to signals.db
sqlite3 data/signals/signals.db

-- Check if bar_ts is NULL for recent signals
SELECT signal_id, strategy_id, symbol, bar_ts, created_at
FROM signals
ORDER BY created_at DESC LIMIT 10;

-- Expected: bar_ts should be NULL for PixityAI signals
```

**2. Check runner_state updates:**
```sql
-- Connect to config.db
sqlite3 data/config/config.db

-- Check if runner_state is empty or stale
SELECT symbol, strategy_id, timeframe, updated_at
FROM runner_state
WHERE strategy_id = 'pixityAI_meta';

-- Expected: Empty or very old timestamps
```

**3. Check DuckDB lock errors in logs:**
```bash
tail -f logs/unified_live_runner.log | grep "Tick buffer flush failed"

# Expected: Continuous errors about file being used by another process
```

---

### Post-Fix Verification (Confirm Fixes Work)

**Apply fixes #1-#4, then test:**

**1. Restart unified runner:**
```bash
# Stop current runner (Ctrl+C or kill PID 18312)
python scripts/unified_live_runner.py \
    --mode paper \
    --symbols "NSE_EQ|INE155A01022" "NSE_EQ|INE118H01025" \
    --strategies pixityAI_meta \
    --max-capital 100000 \
    --max-daily-loss 5000
```

**2. Verify signals persist correctly:**
```sql
-- Wait for 1-2 signals to generate (15-30 minutes)
sqlite3 data/signals/signals.db

SELECT signal_id, strategy_id, symbol, signal_type, bar_ts
FROM signals
WHERE strategy_id = 'pixityAI_meta'
ORDER BY created_at DESC LIMIT 5;

-- Expected: bar_ts should have valid timestamps like '2026-02-09 10:30:00'
```

**3. Verify runner_state updates:**
```sql
sqlite3 data/config/config.db

SELECT symbol, strategy_id, timeframe, current_bias, signal_state, updated_at
FROM runner_state
WHERE strategy_id = 'pixityAI_meta';

-- Expected:
-- - timeframe = '15m'
-- - updated_at refreshes every 15 minutes
-- - signal_state = 'TRIGGERED' when signal fires
```

**4. Verify DuckDB lock errors reduced:**
```bash
tail -f logs/unified_live_runner.log | grep "Tick buffer flush failed"

# Expected: Zero or very few errors (only if truly simultaneous writes)
```

**5. Check scanner UI:**
```
Open: http://127.0.0.1:5000/scanner
```

Expected behavior:
- Both symbols (Tata Power, Bajaj Finance) show status = "RUNNING"
- Timeframe column shows "15m"
- `last_bar_ts` updates every 15 minutes (9:30, 9:45, 10:00, ...)
- When signal triggers: `signal_state = "TRIGGERED"`, `current_bias = "BUY"/"SELL"`

**6. (Optional) Check health endpoint:**
```bash
curl -X GET http://127.0.0.1:5000/scanner/api/health \
  -H "Cookie: session=<your-session-cookie>"

# Expected JSON:
{
  "runner_state_count": 2,  # Tata Power + Bajaj Finance
  "signals_count": 5,       # Recent signals
  "live_buffer_accessible": true,
  "last_runner_update": "2026-02-09 13:45:00"
}
```

---

### Success Criteria

**Fix #1 (Signal Persistence):**
- ✅ `bar_ts` column in signals table has valid timestamps
- ✅ Scanner symbol drawer shows "Recent Signals" with timestamps

**Fix #2 (Lock Timeouts):**
- ✅ `runner_state` table updates every 15 minutes
- ✅ No "hung" log entries (timeouts fail fast)

**Fix #3 (DuckDB Thread Safety):**
- ✅ <5 "Tick buffer flush failed" errors per hour (down from continuous)
- ✅ Tick buffer size remains stable (check with health endpoint)

**Fix #4 (Error Propagation):**
- ✅ Stack traces visible in logs when errors occur
- ✅ Easier debugging of scanner issues

---

### Monitoring Checklist (Post-Fix)

**Daily checks:**
- [ ] Scanner shows both symbols with status = "RUNNING"
- [ ] `last_bar_ts` updates every 15 minutes
- [ ] Signals visible with valid timestamps
- [ ] DuckDB lock errors <5 per hour
- [ ] No hung processes or memory leaks

**Weekly checks:**
- [ ] Signal frequency ~10-15/day/symbol (matches backtest)
- [ ] Runner state timestamps current (not stale)
- [ ] Paper trading PnL tracking (future validation)

---

## Implementation Priority

| Fix | Priority | Impact | Effort | Risk |
|-----|----------|--------|--------|------|
| #1: Signal timestamp | P0 | Critical | 1 line | None |
| #2: Lock timeouts | P0 | High | 2 lines | None |
| #3: Tick buffer retry | P1 | Medium | 15 lines | Low |
| #4: Error logging | P2 | Low | 1 line | None |
| #5: Health endpoint | P3 | Optional | 30 lines | None |

**Recommended implementation order:**
1. Fix #1 + #2 together (critical path, <5 min)
2. Test scanner behavior (15-30 min)
3. Fix #3 if DuckDB errors persist (15 min)
4. Fix #4 + #5 as time permits (optional)

---

## Rollback Plan

If fixes cause unexpected issues:

**Quick rollback:**
```bash
git diff HEAD core/database/writers.py core/database/manager.py
git checkout HEAD -- core/database/writers.py core/database/manager.py
```

**Restart runner:**
```bash
ps aux | grep unified_live_runner | awk '{print $2}' | xargs kill
python scripts/unified_live_runner.py --mode paper --symbols "NSE_EQ|INE155A01022" "NSE_EQ|INE118H01025" --strategies pixityAI_meta
```

**Worst case:** Revert to separate ingestor process (slower but more stable):
```bash
# Terminal 1: Ingestor only
python scripts/market_ingestor.py

# Terminal 2: Runner only (no ingestor thread)
python scripts/unified_live_runner.py --no-ingestor --mode paper ...
```

---

## Critical Files to Modify

| Priority | File | Lines Changed | Type | Description |
|----------|------|---------------|------|-------------|
| P0 | `core/database/writers.py` | 176 | Fix | Change `signal.bar_ts` → `signal.timestamp` |
| P0 | `core/database/manager.py` | 222, 318 | Fix | Add `timeout=10.0` to WriterLock calls |
| P1 | `core/database/ingestors/websocket_ingestor.py` | 52-64 | Enhance | Add retry logic with backoff |
| P2 | `core/runner.py` | 384 | Enhance | Add `exc_info=True` to error logging |
| P3 | `flask_app/blueprints/scanner.py` | NEW | Optional | Add `/api/health` endpoint |

**Total changes:** 5 lines critical, 15 lines optional enhancements

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Fix #1 breaks existing signals | Low | Medium | SignalEvent uses `timestamp` in core/events.py, change is correct |
| Fix #2 causes new timeouts | Low | Low | 10s timeout already proven in live_buffer_writer |
| Fix #3 introduces bugs | Very Low | Low | Only adds retry logic, doesn't change core behavior |
| Regression after fixes | Very Low | Medium | Git rollback available, changes are minimal |

---

## Expected Outcomes

**Immediate (after Fix #1 + #2):**
- Scanner displays signals with timestamps ✅
- Runner state updates every 15 minutes ✅
- No more silent failures ✅

**Within 1 hour:**
- DuckDB lock errors reduced from continuous to <5/hour ✅
- Scanner shows live PixityAI state ✅
- User can monitor signals in real-time ✅

**Within 1 day:**
- System stability confirmed ✅
- Paper trading tracking functional ✅
- Ready for extended observation ✅

---

## Summary

The live scanner is **not behaving properly** due to 4 critical bugs:

1. **Signal persistence field mismatch** - Signals saved with NULL timestamps
2. **Missing lock timeouts** - Runner state updates hang indefinitely
3. **DuckDB threading conflicts** - Tick buffer flushes fail continuously
4. **Silent exception handling** - Errors hidden from user

**Fix impact:**
- 🔴 **Fix #1 + #2 (CRITICAL):** 3 lines changed → Scanner functional
- 🟠 **Fix #3 (HIGH):** 15 lines changed → Lock errors reduced
- 🟡 **Fix #4 + #5 (LOW):** 30 lines changed → Better observability

**Estimated fix time:** 15-30 minutes
**Testing time:** 30-60 minutes
**Total downtime:** <2 hours

The fixes are **minimal, low-risk, and immediately testable**. After applying, the scanner should display live PixityAI signals correctly.
