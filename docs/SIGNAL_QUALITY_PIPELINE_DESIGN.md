# Signal Quality Pipeline Design

## Architecture

```
Event Generator (swing/reversion)
         ↓
   Filter Pipeline
         ↓
    ┌─────────────────┐
    │ Filter Registry │
    └─────────────────┘
         ↓
    [Enabled Filters in Order]
         ↓
    ┌──────────────┐
    │ Kalman Filter│ (Optional)
    └──────────────┘
         ↓
    ┌──────────────────┐
    │Volatility Filter │ (Optional)
    └──────────────────┘
         ↓
    ┌──────────────────┐
    │ OU Reversion     │ (Optional)
    └──────────────────┘
         ↓
    Final Decision (Accept/Reject + Score)
         ↓
    Execution Handler
```

---

## Base Filter Interface

All filters implement:

```python
class BaseSignalFilter(ABC):
    """Base class for all signal quality filters."""

    @abstractmethod
    def evaluate(self, signal: SignalEvent, context: FilterContext) -> FilterResult:
        """
        Evaluate signal quality.

        Args:
            signal: The trading signal/event to evaluate
            context: Market data, state, history needed for evaluation

        Returns:
            FilterResult with:
                - passed: bool (True = accept signal)
                - confidence: float (0-1, filter's confidence)
                - reason: str (why passed/rejected)
                - metadata: dict (filter-specific diagnostics)
        """
        pass

    @abstractmethod
    def initialize(self, config: dict, market_data: pd.DataFrame):
        """Initialize filter state (e.g., fit Kalman, estimate OU params)."""
        pass
```

---

## Filter Result Object

```python
@dataclass
class FilterResult:
    passed: bool              # Accept or reject
    confidence: float         # 0.0 to 1.0
    reason: str              # Human-readable explanation
    metadata: dict           # Filter-specific diagnostics
    filter_name: str         # Which filter produced this
```

---

## Pipeline Modes

### 1. **AND Mode** (All Must Pass)
```python
# Signal passes ONLY if ALL enabled filters accept
if all(result.passed for result in filter_results):
    execute_trade()
```

### 2. **OR Mode** (Any Can Pass)
```python
# Signal passes if ANY enabled filter accepts
if any(result.passed for result in filter_results):
    execute_trade()
```

### 3. **WEIGHTED Mode** (Confidence Scoring)
```python
# Aggregate confidence scores, threshold to decide
total_confidence = sum(r.confidence * r.weight for r in filter_results)
if total_confidence > min_threshold:
    execute_trade()
```

### 4. **SEQUENTIAL Mode** (Short-Circuit)
```python
# Stop at first rejection (fast rejection)
for filter in pipeline:
    result = filter.evaluate(signal, context)
    if not result.passed:
        reject_trade(result.reason)
        break
```

---

## Configuration Schema

```json
{
    "signal_quality_pipeline": {
        "enabled": true,
        "mode": "SEQUENTIAL",  // "AND" | "OR" | "WEIGHTED" | "SEQUENTIAL"
        "min_confidence_threshold": 0.6,  // For WEIGHTED mode

        "filters": [
            {
                "name": "kalman",
                "enabled": true,
                "weight": 0.4,  // For WEIGHTED mode
                "params": {
                    "lookback_periods": 50,
                    "min_signal_noise_ratio": 2.0,
                    "trend_alignment_required": true
                }
            },
            {
                "name": "volatility",
                "enabled": true,
                "weight": 0.3,
                "params": {
                    "method": "garch_kalman",  // "garch_kalman" | "ewma" | "parkinson"
                    "min_volatility_bps": 75,  // 0.75% (must exceed fee impact)
                    "max_volatility_bps": 500, // 5% (too wild = skip)
                    "lookback_days": 20
                }
            },
            {
                "name": "ou_reversion",
                "enabled": false,
                "weight": 0.3,
                "params": {
                    "min_mean_reversion_speed": 0.5,  // theta parameter
                    "estimation_window_days": 60,
                    "refit_frequency": "weekly"
                }
            }
        ]
    }
}
```

---

## File Structure

```
core/filters/
├── __init__.py
├── base.py                     # BaseSignalFilter, FilterResult, FilterContext
├── registry.py                 # FilterRegistry (plugin system)
├── pipeline.py                 # SignalQualityPipeline (orchestration)
├── kalman_filter.py           # KalmanSignalFilter
├── volatility_filter.py       # VolatilityRegimeFilter
├── ou_reversion_filter.py     # OUReversionFilter
└── models.py                  # FilterResult, FilterContext dataclasses

core/models/
└── signal_quality_config.json  # Default filter configuration
```

---

## Integration Points

### 1. Event Generator → Pipeline
```python
# In pixityAI_batch_events.py or pixityAI_event_generator.py
from core.filters.pipeline import SignalQualityPipeline

pipeline = SignalQualityPipeline.from_config("core/models/signal_quality_config.json")

for event in raw_events:
    context = FilterContext(
        symbol=event.symbol,
        current_price=ltp,
        recent_bars=df.tail(100),
        market_state=regime_data
    )

    result = pipeline.evaluate(event, context)

    if result.passed:
        filtered_events.append(event)
        logger.info(f"Signal ACCEPTED | {result.reason} | Confidence: {result.confidence:.2f}")
    else:
        logger.info(f"Signal REJECTED | {result.reason}")
```

### 2. Backtest Integration
```python
# In core/backtest/runner.py
# Option 1: Disable all filters for baseline comparison
config = load_config()
config['signal_quality_pipeline']['enabled'] = False

# Option 2: Test specific filter combinations
filter_combinations = [
    ["kalman"],
    ["volatility"],
    ["kalman", "volatility"],
    ["kalman", "volatility", "ou_reversion"],
    []  # no filters (baseline)
]

for filters in filter_combinations:
    config['signal_quality_pipeline']['filters'] = [
        f for f in config['filters'] if f['name'] in filters
    ]
    run_backtest(config)
```

---

## Implementation Priority

### Phase 1: Core Infrastructure (Day 1)
- [ ] `core/filters/base.py` - Base classes
- [ ] `core/filters/pipeline.py` - Pipeline orchestration
- [ ] `core/filters/registry.py` - Plugin system
- [ ] Config schema in `core/models/signal_quality_config.json`

### Phase 2: Filter #1 - Kalman (Day 2-3)
- [ ] `core/filters/kalman_filter.py` - Implement from repo 5.1
- [ ] Unit tests
- [ ] Integration with pixityAI event generator
- [ ] Backtest comparison (with/without)

### Phase 3: Filter #2 - Volatility (Day 4-5)
- [ ] `core/filters/volatility_filter.py` - Implement from repo 5.3
- [ ] Unit tests
- [ ] Backtest comparison (kalman vs volatility vs both)

### Phase 4: Filter #3 - OU Reversion (Day 6-7)
- [ ] `core/filters/ou_reversion_filter.py` - Implement from repo 6.1
- [ ] Parameter estimation batch job
- [ ] Backtest all combinations

### Phase 5: Optimization (Day 8+)
- [ ] Walk-forward test all filter combinations
- [ ] Optimize weights for WEIGHTED mode
- [ ] Add telemetry/monitoring
- [ ] Document best practices

---

## Usage Examples

### Example 1: Just Kalman
```json
"filters": [
    {"name": "kalman", "enabled": true}
]
```

### Example 2: Kalman → Volatility
```json
"mode": "SEQUENTIAL",
"filters": [
    {"name": "kalman", "enabled": true},
    {"name": "volatility", "enabled": true}
]
```

### Example 3: All Filters (Weighted)
```json
"mode": "WEIGHTED",
"min_confidence_threshold": 0.65,
"filters": [
    {"name": "kalman", "enabled": true, "weight": 0.4},
    {"name": "volatility", "enabled": true, "weight": 0.35},
    {"name": "ou_reversion", "enabled": true, "weight": 0.25}
]
```

### Example 4: No Filters (Baseline)
```json
"enabled": false
```

---

## Testing Strategy

### 1. Unit Tests (Per Filter)
```python
def test_kalman_filter_rejects_noisy_signal():
    filter = KalmanSignalFilter(config)
    noisy_signal = generate_whipsaw_signal()
    result = filter.evaluate(noisy_signal, context)
    assert not result.passed
    assert "low signal/noise" in result.reason.lower()
```

### 2. Integration Tests (Pipeline)
```python
def test_pipeline_sequential_short_circuits():
    # If first filter rejects, should not call second filter
    pipeline = SignalQualityPipeline(mode="SEQUENTIAL", filters=[filter1, filter2])
    result = pipeline.evaluate(bad_signal, context)
    assert not result.passed
    assert filter2.call_count == 0  # Never reached
```

### 3. Backtest Validation
```python
# Compare all combinations on Tata Power walk-forward data
for combo in all_combinations(["kalman", "volatility", "ou_reversion"]):
    metrics = run_backtest(
        symbol="INE155A01022",
        start="2024-10-17",
        end="2025-12-31",
        filters=combo
    )
    results[tuple(combo)] = metrics

# Find optimal combination
best_combo = max(results, key=lambda k: results[k].sharpe_ratio)
```

---

## Monitoring & Telemetry

Track filter effectiveness:

```python
# In execution handler
filter_stats = {
    "kalman": {"accepted": 0, "rejected": 0, "avg_confidence": []},
    "volatility": {"accepted": 0, "rejected": 0, "avg_confidence": []},
    "ou_reversion": {"accepted": 0, "rejected": 0, "avg_confidence": []}
}

# Log rejection reasons
rejected_reasons = Counter()
for result in filter_results:
    if not result.passed:
        rejected_reasons[result.reason] += 1

# Daily report
print(f"Top rejection reasons: {rejected_reasons.most_common(5)}")
```

---

## Migration Path

### Existing Code (Before)
```python
# pixityAI_batch_events.py
events = generate_raw_events(df)
# No filtering, straight to execution
return events
```

### With Pipeline (After)
```python
# pixityAI_batch_events.py
raw_events = generate_raw_events(df)

# Apply quality filters
pipeline = get_signal_quality_pipeline()
filtered_events = []

for event in raw_events:
    context = build_filter_context(event, df)
    result = pipeline.evaluate(event, context)

    if result.passed:
        event.quality_score = result.confidence
        event.filter_metadata = result.metadata
        filtered_events.append(event)

return filtered_events
```

**Key Point:** Existing backtests/workflows are unaffected if `pipeline.enabled = false` in config.
