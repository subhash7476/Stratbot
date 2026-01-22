# 🎯 Unified Signal System - User Guide

## Overview

The **Unified Signal System** allows multiple strategy pages to write signals to a central database, and **Page 13 (Options Analyzer)** becomes the single hub for converting ALL signals into options recommendations.

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    STRATEGY PAGES (Signal Generators)             │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  Page 4: Indian Market Squeeze (15m)                             │
│    └─→ Generates Score 5/4 signals                               │
│    └─→ Writes to SignalManager                                   │
│                                                                   │
│  Page 3: EHMA MTF Strategy                                       │
│    └─→ Generates EHMA signals (future)                           │
│    └─→ Writes to SignalManager                                   │
│                                                                   │
│  Page 14: Volatility Contraction Breakout                        │
│    └─→ Generates VCB signals (future)                            │
│    └─→ Writes to SignalManager                                   │
│                                                                   │
└────────────────────┬─────────────────────────────────────────────┘
                     │
                     ▼
          ┌──────────────────────┐
          │   SIGNAL MANAGER     │
          │  (unified_signals    │
          │     DuckDB table)    │
          └──────────┬───────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────────┐
│             PAGE 13: OPTIONS ANALYZER (Central Hub)               │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  1. Reads ALL signals from unified_signals table                 │
│  2. Filters by strategy, score, confidence                       │
│  3. Analyzes options using OptionRecommender                     │
│  4. Displays Greeks-based recommendations                        │
│  5. Multi-strategy portfolio view                                │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 📊 Database Schema

### `unified_signals` Table

```sql
CREATE TABLE unified_signals (
    signal_id VARCHAR PRIMARY KEY,          -- SQUEEZE_15M_RELIANCE_20260117_1315
    strategy VARCHAR NOT NULL,              -- SQUEEZE_15M, EHMA_MTF, VCB, etc.
    symbol VARCHAR NOT NULL,                -- RELIANCE, TATASTEEL, etc.
    instrument_key VARCHAR NOT NULL,        -- NSE_EQ|INE002A01018

    signal_type VARCHAR NOT NULL,           -- LONG or SHORT
    timeframe VARCHAR NOT NULL,             -- 15minute, 5minute, etc.
    timestamp TIMESTAMP NOT NULL,

    entry_price DECIMAL(12,2) NOT NULL,
    sl_price DECIMAL(12,2) NOT NULL,
    tp_price DECIMAL(12,2) NOT NULL,

    score DECIMAL(6,2),                     -- Strategy-specific score
    confidence DECIMAL(6,2),                -- 0-100 normalized confidence

    reasons TEXT,                           -- Why signal was generated
    metadata TEXT,                          -- Additional strategy data (JSON)

    status VARCHAR DEFAULT 'ACTIVE',        -- ACTIVE, FILLED, CANCELLED, EXPIRED
    created_at TIMESTAMP,
    updated_at TIMESTAMP
)
```

---

## 🚀 How to Use

### Step 1: Generate Signals (Any Strategy Page)

#### Example: Page 4 (Indian Market Squeeze)

1. Go to **Page 4: Indian Market Squeeze**
2. Click **"🔄 Refresh Live Data"** (if needed)
3. Click **"📊 Rebuild & Scan"**
4. You'll see signals generated:
   - Score 5 signals (TRADABLE NOW)
   - Score 4 signals (READY SOON)
5. **Automatic**: Signals are written to `unified_signals` table
6. Success message appears: "✅ X signals written to unified storage"

### Step 2: Analyze Options (Page 13)

1. Go to **Page 13: Universal Options Analyzer**
2. View signal statistics at the top
3. Configure filters in sidebar:
   - **All Strategies** or select specific ones
   - **Minimum Score** (default: 4.0)
   - **Minimum Confidence %** (default: 70%)
   - **Capital per Trade** (default: ₹50,000)
4. Click **"🚀 Analyze Options for All Signals"**
5. Wait for option analysis (fetches chains + calculates Greeks)
6. Review recommendations grouped by signal
7. Export to CSV if needed

---

## 📝 For Strategy Page Developers

### Adding Signal Writing to Your Strategy Page

Use the convenience function `write_squeeze_signal()` or create your own:

#### Method 1: Use Convenience Function

```python
from core.signal_manager import write_squeeze_signal

# After generating your signals
for signal in my_signals:
    success = write_squeeze_signal(
        symbol="RELIANCE",
        instrument_key="NSE_EQ|INE002A01018",
        signal_type="LONG",
        entry=2450.0,
        sl=2430.0,
        tp=2490.0,
        score=5.0,
        reasons="SuperTrend bullish, WaveTrend cross",
        timestamp=datetime.now()  # Optional, defaults to now()
    )

    if success:
        print(f"✅ Signal written for {symbol}")
```

#### Method 2: Use SignalManager Directly (Custom Strategy)

```python
from core.signal_manager import SignalManager, UnifiedSignal, generate_signal_id
from datetime import datetime

manager = SignalManager()

# Create signal
signal = UnifiedSignal(
    signal_id=generate_signal_id("MY_STRATEGY", "RELIANCE", datetime.now()),
    strategy="MY_STRATEGY",  # Your strategy name
    symbol="RELIANCE",
    instrument_key="NSE_EQ|INE002A01018",
    signal_type="LONG",
    timeframe="15minute",
    timestamp=datetime.now(),
    entry_price=2450.0,
    sl_price=2430.0,
    tp_price=2490.0,
    score=5.0,  # Your scoring system
    confidence=90.0,  # 0-100 confidence
    reasons="Your signal reasons here",
    metadata='{"extra": "data"}'  # Optional JSON metadata
)

# Write to database
success = manager.write_signal(signal)
```

#### Method 3: Batch Write Multiple Signals

```python
from core.signal_manager import SignalManager

manager = SignalManager()
signals_list = [signal1, signal2, signal3, ...]  # List of UnifiedSignal objects

count = manager.write_signals_batch(signals_list)
print(f"✅ Written {count}/{len(signals_list)} signals")
```

---

## 🔧 SignalManager API

### Reading Signals

```python
manager = SignalManager()

# Get all active signals
all_signals = manager.get_active_signals()

# Filter by strategy
squeeze_signals = manager.get_active_signals(strategy="SQUEEZE_15M")

# Filter by symbol
reliance_signals = manager.get_active_signals(symbol="RELIANCE")

# Filter by score/confidence
high_quality = manager.get_active_signals(min_score=5, min_confidence=90)

# Get signals ready for options analysis
options_ready = manager.get_signals_for_options(min_score=4.0)
```

### Updating Signal Status

```python
# Mark signal as filled
manager.update_signal_status(
    signal_id="SQUEEZE_15M_RELIANCE_20260117_1315",
    status="FILLED"
)

# Statuses: ACTIVE, FILLED, CANCELLED, EXPIRED
```

### Maintenance

```python
# Expire old signals (>24 hours)
count = manager.expire_old_signals(hours=24)

# Get statistics
stats = manager.get_signal_stats()
# Returns:
# {
#     'by_status': {'ACTIVE': 10, 'FILLED': 5, 'EXPIRED': 2},
#     'active_by_strategy': {'SQUEEZE_15M': 8, 'EHMA_MTF': 2}
# }

# Clear all signals (use with caution!)
manager.clear_all_signals()
```

---

## 🎯 Signal ID Format

Signals have unique IDs in the format:
```
{STRATEGY}_{SYMBOL}_{YYYYMMDD}_{HHMM}
```

Examples:
- `SQUEEZE_15M_RELIANCE_20260117_1315`
- `EHMA_MTF_TATASTEEL_20260117_1430`
- `VCB_HDFCBANK_20260117_1045`

This ensures uniqueness while being human-readable.

---

## 📊 Strategy Naming Convention

Use clear, descriptive strategy names:

| Strategy | Name | Example |
|----------|------|---------|
| Indian Market Squeeze (15m) | `SQUEEZE_15M` | ✅ |
| EHMA MTF | `EHMA_MTF` | ✅ |
| Volatility Contraction Breakout | `VCB` | ✅ |
| SuperTrend | `SUPERTREND` | ✅ |
| Custom Strategy | `MY_STRATEGY` | ✅ |

**Guidelines:**
- Use UPPERCASE
- Use underscores for spaces
- Keep it concise (2-3 words max)
- Be consistent across your codebase

---

## 🔄 Signal Lifecycle

```
1. CREATED → Strategy page generates signal
      ↓
2. WRITTEN → SignalManager writes to DB (status=ACTIVE)
      ↓
3. DISPLAYED → Page 13 shows signal with options
      ↓
4. TRADED → User executes option trade
      ↓
5. UPDATED → Status changed to FILLED/CANCELLED
      ↓
6. EXPIRED → After 24h (or manual expiry)
```

---

## 🎨 Page 13 Features

### Filters (Sidebar)

- **Strategy Filter**: Show all or specific strategies
- **Minimum Score**: Filter low-quality signals
- **Minimum Confidence**: Filter by confidence percentage
- **Capital per Trade**: Set capital allocation
- **Options per Signal**: Top N recommendations

### Signal Statistics

- Active Signals count
- Filled/Expired/Cancelled counts
- Breakdown by strategy

### Options Analysis

- Greeks-based ranking (Delta, Theta, IV, Liquidity)
- Top 3 options per signal
- Capital requirements
- Potential ROI estimates
- Moneyness (ATM/ITM/OTM)

### Portfolio Summary

- Total signals analyzed
- Total options recommended
- Total capital required
- Average ROI
- Strategy breakdown

### Export

- CSV export with all signal + option details
- Includes Greeks, pricing, and metadata

---

## ⚙️ Configuration

### Risk-Free Rate (for Greeks)

Default: 6.5% (RBI repo rate)

To change:
```python
# In greeks_calculator.py
calculator = GreeksCalculator(risk_free_rate=0.065)  # 6.5%
```

### Signal Expiry Time

Default: 24 hours

To change:
```python
# In Page 13 sidebar
manager.expire_old_signals(hours=48)  # 48 hours
```

### Capital Allocation

Default: ₹50,000 per trade

Adjust in Page 13 sidebar.

---

## 🐛 Troubleshooting

### "No signals found"

**Cause**: No active signals in database
**Solution**: Go to strategy pages and run scans

### "Error writing signal"

**Cause**: Database connection issue or invalid data
**Solution**: Check logs for error details, ensure DB is initialized

### "Option chain not available"

**Cause**: Stock doesn't have F&O options or API issue
**Solution**: Check if stock is in F&O segment, try during market hours

### Signals not appearing in Page 13

**Cause**: Filtered out by score/confidence thresholds
**Solution**: Lower minimum score/confidence in sidebar

---

## 📚 Next Steps

### Phase 2 (Coming Soon)
- [ ] Paper trading integration
- [ ] Auto-expire signals on TP/SL hit
- [ ] Real-time position tracking

### Phase 3 (Future)
- [ ] Live order execution via Upstox API
- [ ] P&L tracking
- [ ] Performance analytics by strategy

### Phase 4 (Advanced)
- [ ] Multi-leg option strategies
- [ ] Hedging recommendations
- [ ] Greeks-based alerts

---

## 📞 Support

For issues or questions about the unified signal system:

1. Check signal stats in Page 13 sidebar
2. Review database using `SignalManager().get_signal_stats()`
3. Check logs for error messages
4. Verify signal format matches `UnifiedSignal` schema

---

**Created**: 2026-01-17
**Version**: 1.0
**Architecture**: Multi-Strategy Unified Signal System
**Central Hub**: Page 13 - Universal Options Analyzer
