# Signal Quality Filter System - Usage Guide

## Overview

The Signal Quality Filter System is a modular pipeline that evaluates and filters trading signals based on configurable quality criteria. Filters can be enabled/disabled and chained in different modes without code changes.

## Architecture

```
Raw Events → Filter Pipeline → Filtered Events
                    ↓
              [Filter 1] (optional)
                    ↓
              [Filter 2] (optional)
                    ↓
              [Filter 3] (optional)
```

## Quick Start

### 1. Basic Usage (With Filtering)

```python
from core.strategies.pixityAI_batch_events import batch_generate_events_with_quality_filter

# Load your OHLCV data
df = load_market_data(symbol="NSE_EQ|INE155A01022")

# Generate filtered events
filtered_events, stats = batch_generate_events_with_quality_filter(
    df=df,
    config_path="core/models/signal_quality_config.json",
    bar_minutes=15
)

print(f"Generated {len(filtered_events)} high-quality signals")
print(f"Acceptance rate: {stats['acceptance_rate_pct']:.1f}%")
```

### 2. Baseline (No Filtering)

```python
from core.strategies.pixityAI_batch_events import batch_generate_events

# Generate raw events without filtering
raw_events = batch_generate_events(df=df, bar_minutes=15)

print(f"Generated {len(raw_events)} raw signals")
```

## Configuration

Edit `core/models/signal_quality_config.json`:

### Example 1: Kalman Filter Only

```json
{
  "signal_quality_pipeline": {
    "enabled": true,
    "mode": "SEQUENTIAL",
    "filters": [
      {
        "name": "kalman",
        "enabled": true,
        "params": {
          "lookback_periods": 50,
          "min_signal_noise_ratio": 2.0,
          "trend_alignment_required": true
        }
      }
    ]
  }
}
```

### Example 2: Multiple Filters (Sequential)

```json
{
  "signal_quality_pipeline": {
    "enabled": true,
    "mode": "SEQUENTIAL",
    "filters": [
      {
        "name": "kalman",
        "enabled": true,
        "params": {...}
      },
      {
        "name": "volatility",
        "enabled": true,
        "params": {
          "min_volatility_bps": 75,
          "max_volatility_bps": 500
        }
      }
    ]
  }
}
```

### Example 3: Weighted Mode

```json
{
  "signal_quality_pipeline": {
    "enabled": true,
    "mode": "WEIGHTED",
    "min_confidence_threshold": 0.65,
    "filters": [
      {
        "name": "kalman",
        "enabled": true,
        "weight": 0.4,
        "params": {...}
      },
      {
        "name": "volatility",
        "enabled": true,
        "weight": 0.6,
        "params": {...}
      }
    ]
  }
}
```

### Example 4: Disable All Filters

```json
{
  "signal_quality_pipeline": {
    "enabled": false
  }
}
```

## Pipeline Modes

| Mode | Behavior | When to Use |
|------|----------|-------------|
| **SEQUENTIAL** | Stop at first rejection | Fast rejection, order matters |
| **AND** | All must pass | Conservative filtering |
| **OR** | Any can pass | Catch signals from multiple criteria |
| **WEIGHTED** | Aggregate confidence scores | Fine-tuned scoring |

## Available Filters

### 1. Kalman Filter

**Purpose:** Track price trend and momentum, filter noisy signals

**Parameters:**
- `lookback_periods` (default: 50): Number of bars for initialization
- `min_signal_noise_ratio` (default: 2.0): Minimum S/N ratio to accept signal
- `trend_alignment_required` (default: true): Require Kalman trend to align with signal direction
- `process_variance` (default: 0.01): Process noise (lower = smoother tracking)
- `measurement_variance` (default: 0.1): Measurement noise

**When to use:**
- To filter out counter-trend signals
- To require strong momentum confirmation
- To reduce whipsaw trades in choppy markets

**Example config:**
```json
{
  "name": "kalman",
  "enabled": true,
  "params": {
    "lookback_periods": 50,
    "min_signal_noise_ratio": 2.0,
    "trend_alignment_required": true
  }
}
```

### 2. Volatility Filter (Coming Soon)

**Purpose:** Skip trades in low/high volatility regimes

**Parameters:**
- `min_volatility_bps`: Minimum volatility to overcome fees (e.g., 75 bps)
- `max_volatility_bps`: Maximum volatility (skip if too wild, e.g., 500 bps)
- `method`: "garch_kalman", "ewma", or "parkinson"

### 3. OU Reversion Filter (Coming Soon)

**Purpose:** Validate mean-reversion strength for reversion signals

**Parameters:**
- `min_mean_reversion_speed`: Minimum theta parameter
- `estimation_window_days`: Days of data for parameter estimation

## Testing

### Run Demo Tests

```bash
python scripts/test_signal_quality_filters.py
```

This will run three tests:
1. **Baseline**: No filtering (shows raw event count)
2. **Kalman Filter**: Shows filtering effectiveness
3. **Threshold Analysis**: Tests different S/N ratios

### Expected Output

```
TEST 1: NO FILTER (Baseline)
Baseline: 245 raw events generated

TEST 2: KALMAN FILTER ONLY
Filtered Events: 127/245
Acceptance Rate: 51.8%

Rejection Reasons:
  Weak signal: S/N=1.45 < 2.0: 89
  Trend misalignment: 29

TEST 3: KALMAN FILTER - VARYING THRESHOLDS
Threshold     Accepted     Acceptance Rate
--------------------------------------------------
1.0           189          77.1%
1.5           156          63.7%
2.0           127          51.8%
2.5           98           40.0%
3.0           71           29.0%
```

## Integration with Backtesting

### Option 1: Compare With/Without Filters

```python
from core.backtest.runner import BacktestRunner

# Test 1: Baseline (no filters)
config_nofilter = load_config()
config_nofilter['signal_quality_pipeline']['enabled'] = False

results_baseline = BacktestRunner(config_nofilter).run()

# Test 2: With Kalman filter
config_kalman = load_config()
config_kalman['signal_quality_pipeline']['filters'][0]['enabled'] = True

results_filtered = BacktestRunner(config_kalman).run()

# Compare
print(f"Baseline: {results_baseline.total_trades} trades, {results_baseline.sharpe:.2f} Sharpe")
print(f"Filtered: {results_filtered.total_trades} trades, {results_filtered.sharpe:.2f} Sharpe")
```

### Option 2: Test All Combinations

```python
filter_combinations = [
    [],                                    # Baseline
    ["kalman"],                           # Just Kalman
    ["volatility"],                       # Just volatility
    ["kalman", "volatility"],             # Both
]

results = {}
for combo in filter_combinations:
    config = load_config()
    # Enable only specified filters
    for filter_cfg in config['signal_quality_pipeline']['filters']:
        filter_cfg['enabled'] = filter_cfg['name'] in combo

    results[tuple(combo)] = BacktestRunner(config).run()

# Find best combination
best_combo = max(results, key=lambda k: results[k].sharpe_ratio)
print(f"Best combination: {best_combo} with Sharpe {results[best_combo].sharpe_ratio:.2f}")
```

## Walk-Forward Validation

```python
# Train period: Oct 2024 - May 2025
train_data = load_data("2024-10-17", "2025-05-31")
train_results = backtest_with_filters(train_data, config)

# Test period: Jun 2025 - Dec 2025
test_data = load_data("2025-06-01", "2025-12-31")
test_results = backtest_with_filters(test_data, config)

# Compare
print(f"Train: {train_results.win_rate:.1f}% WR, Rs {train_results.net_pnl:,.0f}")
print(f"Test:  {test_results.win_rate:.1f}% WR, Rs {test_results.net_pnl:,.0f}")
```

## Monitoring

### Track Filter Performance

```python
pipeline = SignalQualityPipeline.from_config("core/models/signal_quality_config.json")

# After running...
stats = pipeline.get_stats()

print(f"Total evaluated: {stats['total_evaluated']}")
print(f"Accepted: {stats['total_accepted']}")
print(f"Rejected: {stats['total_rejected']}")
print(f"Acceptance rate: {stats['acceptance_rate_pct']:.1f}%")
```

### Log Rejection Reasons

The `batch_generate_events_with_quality_filter()` function returns detailed stats:

```python
events, stats = batch_generate_events_with_quality_filter(df)

print(f"\nRejection breakdown:")
for reason, count in stats['rejection_reasons'].items():
    print(f"  {reason}: {count}")
```

Example output:
```
Rejection breakdown:
  Weak signal: S/N=1.45 < 2.0: 89
  Trend misalignment: signal=BUY, kalman_velocity=-0.0234: 29
  insufficient_data: 3
```

## Adding Custom Filters

### 1. Create Filter Class

Create `core/filters/my_custom_filter.py`:

```python
from core.filters.base import BaseSignalFilter
from core.filters.models import FilterResult, FilterContext
import pandas as pd

class MyCustomFilter(BaseSignalFilter):
    def __init__(self, config: dict, filter_name: str = "my_custom"):
        super().__init__(config, filter_name)
        self.my_param = config.get('my_param', 1.0)

    def initialize(self, market_data: pd.DataFrame) -> None:
        # Fit your model/compute baseline stats here
        self.mean_price = market_data['close'].mean()
        self.is_initialized = True

    def evaluate(self, context: FilterContext) -> FilterResult:
        # Your filter logic here
        price_deviation = abs(context.current_price - self.mean_price) / self.mean_price

        passed = price_deviation < self.my_param
        confidence = 1.0 - price_deviation

        return self._create_result(
            passed=passed,
            confidence=confidence,
            reason=f"Price deviation: {price_deviation:.2%}"
        )

# Register the filter
from core.filters.registry import FilterRegistry
FilterRegistry.register("my_custom", MyCustomFilter)
```

### 2. Add to Config

```json
{
  "signal_quality_pipeline": {
    "enabled": true,
    "mode": "SEQUENTIAL",
    "filters": [
      {
        "name": "my_custom",
        "enabled": true,
        "params": {
          "my_param": 0.05
        }
      }
    ]
  }
}
```

### 3. Import in Code

```python
from core.strategies.pixityAI_batch_events import batch_generate_events_with_quality_filter
import core.filters.my_custom_filter  # Register filter

events, stats = batch_generate_events_with_quality_filter(df)
```

## Best Practices

### 1. Always Compare to Baseline

Before deploying filtered signals, compare:
- Number of trades (should reduce by 30-50%)
- Win rate (should improve by 5-10%)
- Net PnL (should improve or stay flat)
- Sharpe ratio (should improve)

### 2. Use Walk-Forward Validation

Don't trust in-sample results. Always validate on out-of-sample data.

### 3. Start Conservative

Begin with strict filters (e.g., `min_signal_noise_ratio: 3.0`), then relax if too few signals.

### 4. Monitor Rejection Reasons

Track why signals are rejected. If most rejections are from one filter, consider adjusting thresholds.

### 5. Symbol-Specific Tuning

Different symbols may need different thresholds. Consider per-symbol configs:

```python
symbol_configs = {
    "INE155A01022": {"min_signal_noise_ratio": 2.0},  # Tata Power
    "INE118H01025": {"min_signal_noise_ratio": 2.5},  # Bajaj Finance
}
```

## Troubleshooting

### Problem: All signals rejected

**Solution:** Lower thresholds in config (e.g., `min_signal_noise_ratio: 1.5`)

### Problem: No improvement in metrics

**Solution:** Filters may be too lenient. Increase thresholds or add more filters.

### Problem: ImportError when loading filters

**Solution:** Ensure filter module is imported:
```python
import core.filters.kalman_filter  # Registers the filter
```

### Problem: "Filter not initialized" error

**Solution:** Call `pipeline.initialize(historical_data)` before evaluating signals.

## Performance Considerations

- **Initialization**: Done once per backtest (O(n) where n = historical bars)
- **Evaluation**: O(m * f) where m = signals, f = filters
- **Memory**: ~50-100 KB per filter for state storage

For large backtests (>100k signals), use `mode: "SEQUENTIAL"` for fast rejection.

## Roadmap

### Phase 2: Volatility Filter (Week 2)
- GARCH-Kalman volatility estimation
- Dynamic fee-adjusted thresholds

### Phase 3: OU Reversion Filter (Week 3)
- Ornstein-Uhlenbeck parameter estimation
- Mean-reversion strength scoring

### Phase 4: Advanced Features (Week 4+)
- Per-symbol filter tuning
- Adaptive thresholds based on regime
- Real-time telemetry integration

## Support

- **Issues**: Report bugs in project issue tracker
- **Documentation**: See `docs/SIGNAL_QUALITY_PIPELINE_DESIGN.md` for architecture details
- **Examples**: Run `scripts/test_signal_quality_filters.py` for working examples
