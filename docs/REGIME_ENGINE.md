# Regime Detection Engine

The Regime Detection Engine is a centralized service that classifies market conditions into actionable states. It replaces hardcoded placeholders and provides a "Source of Truth" for all trading strategies in the project.

## 1. Classification Logic

The engine uses a combination of **Trend Strength**, **Directional Bias**, and **Volatility Context** to determine the regime.

### Indicators Used:
- **EMA (20, 50, 200):** Determines directional bias and trend alignment.
- **ADX (14):** Measures the strength of the current trend (regardless of direction).
- **ATR (14):** Measures market volatility normalized as a percentage of price.

### Regime Definitions:

| Regime | Criteria | Interpretation |
| :--- | :--- | :--- |
| `BULL_TREND` | ADX > 22, Price > EMA20 > EMA50 | Strong upward movement. Ideal for trend-following entries. |
| `BEAR_TREND` | ADX > 22, Price < EMA20 < EMA50 | Strong downward movement. Ideal for shorting. |
| `BULLISH_CONSOLIDATION` | ADX <= 22, Price > EMA20 > EMA50 | Bullish bias but momentum is stalling. |
| `BEARISH_CONSOLIDATION` | ADX <= 22, Price < EMA20 < EMA50 | Bearish bias but momentum is stalling. |
| `RANGING` | ADX < 20, Low Volatility | Sideways market. Ideal for Mean Reversion strategies. |
| `VOLATILE_RANGE` | ADX < 20, High Volatility | High noise, unpredictable swings. High risk for most strategies. |

---

## 2. Usage for Strategy Developers

Strategies receive the regime data automatically via the `StrategyContext`. You do not need to calculate these indicators manually.

### Accessing Regime Data:
```python
def process_bar(self, bar, context):
    regime_data = context.market_regime
    regime = regime_data.get("regime")
    strength = regime_data.get("trend_strength")
    volatility = regime_data.get("volatility_level")
```

### Common Implementation Patterns:

#### A. Trend Filter
Only allow entries when the market is in a confirmed trend.
```python
if regime not in ["BULL_TREND", "BEAR_TREND"]:
    return None # Skip trading in choppy markets
```

#### B. Dynamic Stop Loss
Widen stops during high volatility to avoid "stop hunts."
```python
base_stop = 2.0
if volatility == "HIGH":
    stop_price = bar.close - (base_stop * 1.5 * atr)
else:
    stop_price = bar.close - (base_stop * atr)
```

#### C. Regime-Specific Logic (Adaptive)
```python
if regime == "RANGING":
    return self.execute_mean_reversion(bar)
else:
    return self.execute_trend_following(bar)
```

---

## 3. System Integration

The `AnalyticsPopulator` runs the `RegimeDetector` periodically for all symbols and saves the results to the database. Strategies then retrieve this data through the `MarketDataQuery` layer during both backtesting and live execution.
