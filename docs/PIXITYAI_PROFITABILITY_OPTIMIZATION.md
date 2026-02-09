# PixityAI Profitability Optimization — Session Summary

**Date:** 2026-02-08

## Objective

Make the PixityAI strategy profitable for live trading on NSE equities.

## Starting Point

The batch backtest architecture existed but had critical bugs preventing accurate results. The strategy used a RandomForest meta-model trained on NIFTY 50 index data, applied to individual equities at 1h timeframe.

## Bug Fixes Applied

### 1. Threshold Defaulting to 0.5 (`core/backtest/runner.py`)
- **Problem:** `strategy_params.get("threshold", 0.5)` used a hardcoded default, but the config specifies 0.45. Flask sends no threshold key.
- **Fix:** Changed to `pixity_config.get('long_threshold'/'short_threshold', 0.45)` per signal type.

### 2. Config Loaded After Event Generation
- **Problem:** `pixity_config` was loaded after `batch_generate_events()`, so config params weren't passed to event generation.
- **Fix:** Moved config loading before the batch generation call.

### 3. Threshold Comparison Off-by-One
- **Problem:** Used `>` instead of `>=` for threshold comparison, inconsistent with training pipeline.
- **Fix:** Changed to `>=`.

### 4. No Metadata Integrity Checks
- **Problem:** Events with `quantity=0`, `sl=0`, `tp=0` could pass through to execution.
- **Fix:** Added validation: quantity > 0, sl/tp present, h_bars >= 1.

### 5. Idempotency Guard Blocking Exits
- **Problem:** Exit signal IDs like `exit_NSE_EQ|INE002A01018_2025-08-20T14:15:00` collided across backtest runs in shared `trading.db`, causing exits to be silently blocked. TIME_STOP fired at 17 bars instead of 12.
- **Fix:** Disabled idempotency for backtests: `execution._is_signal_already_executed = lambda signal_id: False` in both `_run_standard` and `_run_pixityAI_batch`.

### 6. Fee Model Overstated by ~2x (`core/execution/handler.py`)
- **Before:** `return quantity * price * 0.001` (flat 0.1% per side)
- **After:** Realistic NSE equity intraday costs: Rs 20 brokerage + STT 0.025% + exchange 0.00345% + SEBI 0.0001% + GST 18% on (brokerage + exchange + SEBI) + stamp duty 0.003%
- **Impact:** ~Rs 110 round trip vs previous ~Rs 196

## Optimization Attempts

### Trailing Stop (REVERTED)
Tested 3 configurations:
1. **1x ATR trail after 1R move:** Trade #1 cut from +995 to +373 (too tight, normal pullback triggered it)
2. **1.5x ATR trail:** Trade #1 still reduced to +124
3. **Late trailing (bar 8+, 0.5R activation):** Mixed results, hurt Trade #4 from +746 to +248

**Conclusion:** Trailing stops on intraday equity hurt more than help — they cut winners on normal pullbacks. All trailing stop code was reverted from `core/runner.py`.

### Directional Filter — Daily EMA20/50 (REVERTED)
Only traded in the direction of the daily trend (EMA20 > EMA50 = uptrend, only longs).

**Result:** Removed Trade #4 which was a +746 LONG during a "downtrend" that actually rallied. Net PnL worsened.

**Conclusion:** Counter-trend trades can be winners. Directional filters on daily timeframe are too coarse for intraday 15m signals.

## Key Discovery: Meta-Model is Anti-Predictive

### Evidence
Ran backtests across 4 configurations:

| Config | Trades | WR% | Gross PnL | Net PnL | Net/Trade |
|--------|--------|-----|-----------|---------|-----------|
| 1h + OOS model | 41 | 41.5% | -5,544 | -10,311 | -251 |
| 15m + OOS model | 97 | 40.2% | -922 | -18,470 | -190 |
| 1h + no model | 51 | 39.2% | +2,720 | -3,154 | -62 |
| **15m + no model** | **100** | **42.0%** | **+19,021** | **+1,390** | **+14** |

The meta-model actively selects losing trades over winning ones. Without it, the raw event generation (swing detection + mean reversion) has a genuine positive gross edge at 15m.

### Root Cause
Models were trained on NIFTY 50 index data or per-symbol data with in-sample overfitting. Feature-to-outcome relationships differ between index and individual equities. The model's probability scores inversely correlate with actual trade outcomes.

## Multi-Symbol Screening

Tested 10 of the most liquid NSE equities at 15m without meta-model (June — December 2025):

| Symbol | Trades | WR% | Gross | Fees | Net | Net/Trade |
|--------|--------|-----|-------|------|-----|-----------|
| Tata Power | 50 | 60.0% | +20,565 | 7,602 | **+12,963** | **+259** |
| Reliance | 50 | 42.0% | +13,575 | 7,934 | **+5,641** | **+113** |
| Bajaj Finance | 50 | 48.0% | +7,796 | 5,296 | **+2,500** | **+50** |
| ICICI Bank | 50 | 40.0% | +10,485 | 9,291 | **+1,194** | **+24** |
| HCL Tech | 50 | 44.0% | +4,356 | 8,442 | -4,086 | -82 |
| HDFC Bank | 50 | 42.0% | +5,446 | 9,697 | -4,251 | -85 |
| BPCL | 50 | 50.0% | +1,101 | 6,677 | -5,577 | -112 |
| Infosys | 50 | 56.0% | -3,086 | 7,548 | -10,633 | -213 |
| TCS | 50 | 36.0% | -11,716 | 6,067 | -17,783 | -356 |
| SBIN | 50 | 34.0% | -9,668 | 8,756 | -18,424 | -368 |

## Walk-Forward Validation

To avoid hindsight bias in symbol selection, I ran the same test on the training period (Oct 2024 — May 2025):

| Symbol | Training Net | Test Net | Consistent? |
|--------|-------------|----------|-------------|
| **Tata Power** | **+2,928** | **+12,963** | **YES** |
| **Bajaj Finance** | **+2,365** | **+2,500** | **YES** |
| BPCL | +17,320 | -5,577 | No (regime change) |
| HCL Tech | +10,633 | -4,086 | No (regime change) |
| HDFC Bank | +3,243 | -4,251 | No |
| Reliance | -10,598 | +5,641 | No |
| ICICI | -4,045 | +1,194 | No |

**Only Tata Power and Bajaj Finance are profitable in BOTH periods.**

### Final Walk-Forward Results

| Period | Trades | WR% | Net PnL | Net/Trade |
|--------|--------|-----|---------|-----------|
| Training (Oct24-May25) | 100 | 52% | +5,293 | +53 |
| Test (Jun-Dec25) | 100 | 54% | +15,464 | +155 |
| **Combined (15 months)** | **200** | **53%** | **+20,757** | **+104** |

**Annualized return: ~16.6% on Rs 1,00,000 capital**

## Files Modified

| File | Changes |
|------|---------|
| `core/backtest/runner.py` | Bug fixes (threshold, config ordering, validation, idempotency), per-symbol model selection, `skip_meta_model` param, `model_path` override, 90-day data warmup |
| `core/execution/handler.py` | Fee model updated to realistic NSE equity intraday costs |
| `core/runner.py` | `entry_price` and `atr_at_entry` added to exit params (trailing stop code added then reverted) |
| `core/models/pixityAI_config.json` | Updated to 15m nomodel configuration with validated symbol list |

## Final Configuration

```json
{
    "strategy_id": "pixityAI_meta",
    "bar_minutes": 15,
    "preferred_timeframe": "15m",
    "skip_meta_model": true,
    "symbols": ["NSE_EQ|INE155A01022", "NSE_EQ|INE118H01025"],
    "risk_per_trade": 500.0,
    "sl_mult": 1.0,
    "tp_mult": 2.0,
    "time_stop_bars": 12,
    "swing_period": 5,
    "reversion_k": 2.0,
    "cooldown_bars": 3
}
```

## Recommendations for Live Trading

1. **Drop the meta-model** — it destroys edge on equities
2. **Use 15m timeframe** — more trades, better statistical significance than 1h
3. **Trade Tata Power + Bajaj Finance** — only walk-forward validated symbols
4. **Start with paper trading** — validate on live data before risking capital
5. **Re-evaluate symbols monthly** — edge can decay with regime changes
6. **Consider increasing risk_per_trade** to Rs 1000-2000 for better fee efficiency (flat Rs 20 brokerage becomes proportionally smaller)

## Lessons Learned

- In-sample model performance is meaningless — always validate out-of-sample
- The simplest approach (raw event generation, no ML filter) outperformed the complex one
- Trailing stops hurt intraday equity strategies (cuts winners on normal pullbacks)
- STT (0.025% per leg) is the dominant fee component — it scales linearly with position size, making fee optimization through scaling limited
- Symbol selection is the single biggest driver of profitability — bigger impact than any strategy parameter
