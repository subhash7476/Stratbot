# PixityAI Live Scanner Integration Plan

## Context

The PixityAI strategy has been walk-forward validated with profitable results (200 trades, 53% win rate, +Rs 20,757 over 15 months) on **Tata Power** and **Bajaj Finance** at **15-minute timeframe** using raw event generation (skip_meta_model=true). The scanner page is already built and operational, displaying strategy states via the `runner_state` table. The goal is to integrate PixityAI to run on live market data and display signals in the scanner, exactly as it runs in backtests.

**Walk-forward validation results:**
- Symbols: INE155A01022 (Tata Power), INE118H01025 (Bajaj Finance)
- Timeframe: 15m (critical - 15m >> 1h for profitability)
- Config: skip_meta_model=true (meta-model is anti-predictive on equities)
- Performance: 16.6% annual return on Rs 100k capital

## Architecture Overview

**Chosen Approach: Extended Unified Runner** (simplest path, reuses 100% existing infrastructure)

```
unified_live_runner.py
├─ IngestorThread: WebSocket → DuckDB (1m ticks/candles)
├─ TradingThread: TradingRunner
│  ├─ LiveDuckDBMarketDataProvider (1m bars)
│  ├─ ResamplingMarketDataProvider (NEW: 1m → 15m aggregation)
│  ├─ PixityAIMetaStrategy (processes 15m bars, generates signals)
│  ├─ ExecutionHandler (paper broker for future paper trading)
│  └─ _update_runner_state() → runner_state table
└─ FlaskThread: Scanner dashboard (polls runner_state every 2s)
```

**Data Flow:**
1. Ingestor writes 1m bars to `data/live_buffer/candles_today.duckdb`
2. LiveDuckDBMarketDataProvider polls for new 1m bars (0.5s interval)
3. **ResamplingMarketDataProvider** accumulates 1m bars → emits complete 15m bars
4. PixityAIMetaStrategy processes 15m bars → generates swing/reversion signals
5. TradingRunner updates `runner_state` table
6. Scanner page displays signals (already implemented)

## Implementation Tasks

### 1. Create Resampling Wrapper (NEW Component)

**File:** `core/database/providers/resampling_wrapper.py`

**Purpose:** Wraps any MarketDataProvider to resample 1m → 15m bars on-the-fly.

**Key Design:**
- Maintains per-symbol buffer of 1m bars
- Uses existing `core/analytics/resampler.py:resample_ohlcv()` function
- Emits 15m bars only when period is complete (e.g., 9:30:00, 9:45:00, 10:00:00)
- Tracks last emitted timestamp to avoid duplicates

**Implementation sketch:**
```python
class ResamplingMarketDataProvider(MarketDataProvider):
    def __init__(self, base_provider: MarketDataProvider, target_tf: str = "15m"):
        self.base = base_provider
        self.target_tf = target_tf
        self.buffers = {}  # symbol -> list[OHLCVBar]
        self.last_emitted = {}  # symbol -> last 15m timestamp
        self.resampled_buffers = {}  # symbol -> list of ready 15m bars

    def get_next_bar(self, symbol: str) -> Optional[OHLCVBar]:
        # 1. Check if we have a resampled bar ready
        if self.resampled_buffers.get(symbol):
            return self.resampled_buffers[symbol].pop(0)

        # 2. Poll base provider for 1m bars, accumulate in buffer
        while True:
            bar_1m = self.base.get_next_bar(symbol)
            if not bar_1m:
                return None  # No more 1m bars available

            self.buffers.setdefault(symbol, []).append(bar_1m)

            # 3. Check if we can resample (have enough bars for complete period)
            if self._can_resample(symbol):
                bars_15m = self._resample(symbol)
                self.resampled_buffers[symbol] = bars_15m
                return self.resampled_buffers[symbol].pop(0)

    def _can_resample(self, symbol: str) -> bool:
        # Check if buffer spans a complete 15m period
        # Implementation: check if latest bar's timestamp is on 15m boundary
        pass

    def _resample(self, symbol: str) -> List[OHLCVBar]:
        # Convert buffer to DataFrame, call resample_ohlcv(), convert back
        pass
```

**Critical details:**
- Align to NSE market times: 9:15, 9:30, 9:45, 10:00, ... (resampler already does this with offset='15min')
- Handle partial periods gracefully (don't emit incomplete bars)
- Warmup: load last 100 bars from historical data on init for indicator calculation

### 2. Integrate Resampling into Unified Runner (MODIFY)

**File:** `scripts/unified_live_runner.py`

**Changes at line 146:**
```python
# BEFORE:
market_data = LiveDuckDBMarketDataProvider(args.symbols, db_manager=db_manager)

# AFTER:
from core.database.providers.resampling_wrapper import ResamplingMarketDataProvider

base_provider = LiveDuckDBMarketDataProvider(args.symbols, db_manager=db_manager)

# Check if any strategy requires resampling
needs_resampling = any(s_id == "pixityAI_meta" for s_id in args.strategies)
if needs_resampling:
    market_data = ResamplingMarketDataProvider(base_provider, target_tf="15m")
else:
    market_data = base_provider
```

**Optional enhancement:** Add `--timeframe` CLI arg to specify target timeframe per run.

### 3. Verify Configuration (NO CHANGES)

**File:** `core/models/pixityAI_config.json`

Already correct:
```json
{
    "strategy_id": "pixityAI_meta",
    "skip_meta_model": true,
    "preferred_timeframe": "15m",
    "bar_minutes": 15,
    "symbols": [
        "NSE_EQ|INE155A01022",  // Tata Power
        "NSE_EQ|INE118H01025"   // Bajaj Finance
    ],
    "risk_per_trade": 500.0,
    "time_stop_bars": 12
}
```

### 4. Update Database Schema for Timeframe Tracking (OPTIONAL)

**File:** `core/database/schema.py`

The `runner_state` table already has a `timeframe` column:
```sql
CREATE TABLE runner_state (
    symbol TEXT,
    strategy_id TEXT,
    timeframe TEXT DEFAULT '1m',  -- Will be '15m' for PixityAI
    ...
)
```

**Verify:** Ensure `_update_runner_state()` in `core/runner.py` writes correct timeframe:
```python
# Should derive timeframe from strategy config or bar metadata
timeframe = self.config.get("bar_minutes", "1") + "m"
```

### 5. Scanner Display (NO CHANGES NEEDED)

The scanner page already:
- Polls `/api/scanner-state` every 2s
- Displays all strategies from `runner_state` table
- Shows: Symbol | Strategy | Timeframe | Bias | Confidence | State

PixityAI signals will automatically appear with `strategy_id="pixityAI_meta"` and `timeframe="15m"`.

**Optional frontend enhancement:** Add strategy filter dropdown to filter by "pixityAI_meta" only.

## Launch & Operations

### Startup Command

```bash
python scripts/unified_live_runner.py \
    --mode paper \
    --symbols "NSE_EQ|INE155A01022" "NSE_EQ|INE118H01025" \
    --strategies pixityAI_meta \
    --max-capital 100000 \
    --max-daily-loss 5000
```

**What happens:**
1. Market ingestor starts → subscribes to Upstox WebSocket → writes 1m bars
2. Trading runner starts with ResamplingMarketDataProvider (15m)
3. PixityAI strategy processes 15m bars → generates signals
4. Paper broker tracks virtual positions (for future paper trading validation)
5. Scanner dashboard starts at http://127.0.0.1:5000/scanner
6. Signals appear in scanner within ~30s of 15m bar close

### Monitoring Checklist

**Real-time (Scanner Dashboard):**
- Both symbols (Tata Power, Bajaj Finance) show status = "RUNNING"
- `last_bar_ts` updates every 15 minutes (9:30, 9:45, 10:00, ...)
- When signal triggers: `signal_state = "TRIGGERED"`, `current_bias = "BUY"/"SELL"`

**Database Validation:**
```sql
-- Check runner state updates
SELECT symbol, strategy_id, timeframe, current_bias, signal_state, last_bar_ts
FROM runner_state
WHERE strategy_id = 'pixityAI_meta';

-- Check signals generated
SELECT symbol, signal_type, confidence, bar_ts
FROM signals
WHERE strategy_id = 'pixityAI_meta'
ORDER BY bar_ts DESC LIMIT 10;

-- Check paper trades (future paper trading)
SELECT * FROM trades WHERE strategy_id = 'pixityAI_meta' ORDER BY entry_time DESC;
```

**Logs:**
```bash
tail -f logs/unified_live_runner.log | grep pixityAI
```

## Testing Strategy

### Phase 1: Mock Data Replay (1 day)

**Goal:** Validate that live pipeline generates identical signals to backtest.

**Script:** Create `scripts/validate_pixityAI_live.py`

```python
# 1. Load Oct-Dec 2024 data for Tata Power
# 2. Write to live_buffer sequentially (simulating real-time)
# 3. Run unified_live_runner with PixityAI
# 4. Compare generated signals with backtest results for same period
# 5. Assert 100% signal alignment (timestamps, direction, entry price)
```

**Success criteria:** Zero signal mismatches.

### Phase 2: Live Observation Mode (1 week)

**Goal:** Observe live signal generation without execution.

**Approach:**
1. Run live during market hours (9:15-15:30)
2. Log all signals to CSV: `data/live_tests/pixityAI_signals_{date}.csv`
3. Set paper broker to quantity=0 or disable ExecutionHandler
4. Monitor for:
   - 15m bar alignment (timestamps on :15, :30, :45, :00)
   - Signal frequency (expect ~10-15 signals/day/symbol based on backtest)
   - No crashes or hangs

### Phase 3: Paper Trading Mode (1 week)

**Goal:** Validate paper trading matches backtest economics.

**Approach:**
1. Enable paper broker (already default with `--mode paper`)
2. Log all paper trades
3. Compare daily PnL with backtest expectations
4. Validate:
   - TP/SL hit rates
   - Time-stop enforcement (12 bars = 3 hours)
   - Fee calculations (Rs 20 brokerage + 0.025% STT on SELL)

**Success criteria:** Paper PnL within 10% of backtest expectations over 5 consecutive trading days.

## Future Enhancements (Out of Scope)

### Phase 4: Live Execution
- Change `--mode live` to use UpstoxAdapter instead of PaperBroker
- Add circuit breakers (max 2 positions/symbol, max Rs 50k exposure)
- Add kill switch in scanner UI

### Phase 5: Multi-Symbol Expansion
- After 1 month validation on 2 symbols
- Run walk-forward on additional liquid F&O stocks
- Add profitable symbols to config

### Phase 6: Meta-Model Re-Training
- If raw events show >60% win rate over 100 trades
- Collect live outcomes from signals.db + trades.db
- Retrain model, backtest, then enable with `skip_meta_model=false`

## Critical Files to Modify

### New Files (Priority 1)
1. **`core/database/providers/resampling_wrapper.py`** (~200 lines)
   - ResamplingMarketDataProvider class
   - Accumulates 1m bars, emits 15m bars
   - Uses `core/analytics/resampler.py:resample_ohlcv()`

### Modified Files (Priority 1)
2. **`scripts/unified_live_runner.py`** (~10 lines changed)
   - Line 146: Wrap LiveDuckDBMarketDataProvider with ResamplingMarketDataProvider
   - Import statement for resampling wrapper

### Verification Files (Priority 2)
3. **`core/models/pixityAI_config.json`** (verify only, no changes)
4. **`core/strategies/pixityAIMetaStrategy.py`** (verify only, no changes)
5. **`core/runner.py`** (verify timeframe tracking in `_update_runner_state()`)

### Testing Files (Priority 3)
6. **`scripts/validate_pixityAI_live.py`** (NEW, ~200 lines)
   - Mock data replay test
   - Signal comparison with backtest

## Verification Checklist

**Before deployment:**
- [ ] Resampling wrapper tested with mock data (1m → 15m alignment verified)
- [ ] PixityAI config validated (skip_meta_model=true, bar_minutes=15, correct symbols)
- [ ] Mock replay generates identical signals to backtest (100% match)
- [ ] Scanner displays PixityAI signals correctly (timeframe="15m")
- [ ] Database writes correct timeframe in runner_state

**During live testing:**
- [ ] 15m bars emit on schedule (9:30, 9:45, 10:00, ...)
- [ ] Signals generated at expected frequency (~10-15/day/symbol)
- [ ] No errors in logs for 5 consecutive trading days
- [ ] Paper PnL within 10% of backtest expectations

**Production ready when:**
- [ ] All verification checks pass
- [ ] 1 week of clean live observation
- [ ] 1 week of paper trading with validated PnL
- [ ] Operational runbook documented

## Risk Mitigation

**Risk 1: Bar Alignment Issues**
- Symptom: 15m bars at wrong times (9:31 instead of 9:30)
- Mitigation: Test resampler with mock data first, validate offset='15min' works
- Fix: Adjust offset parameter in resample_ohlcv()

**Risk 2: Data Lag**
- Symptom: Strategy processes stale bars (>30s old)
- Mitigation: Monitor `last_bar_age` in logs, tune poll_interval if needed
- Fix: Reduce LiveDuckDBMarketDataProvider poll_interval from 0.5s to 0.2s

**Risk 3: Signal Duplication**
- Symptom: Same signal appears multiple times
- Mitigation: Idempotency check in ExecutionHandler (already exists)
- Fix: Verify signal_id hash includes timestamp + symbol + strategy

**Risk 4: Incomplete 15m Bars**
- Symptom: Strategy receives partial bars (e.g., only 10 minutes of data)
- Mitigation: ResamplingWrapper only emits when period is complete
- Fix: Add validation that bar timestamp is on 15m boundary

## Success Metrics

**MVP Success (2 weeks):**
- PixityAI signals visible in scanner within 30s of 15m bar close
- runner_state updates every 15 minutes for both symbols
- Zero crashes over 5 consecutive trading days
- Signal alignment with backtest validated at 100%

**Production Success (1 month):**
- Paper trading PnL within 10% of backtest expectations
- >50% win rate observed in live paper trading
- All monitoring dashboards operational
- Ready for live capital allocation decision
