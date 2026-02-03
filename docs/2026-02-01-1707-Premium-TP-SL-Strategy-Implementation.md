# Premium TP/SL Strategy Implementation
**Date:** February 1, 2026  
**Time:** 17:07

## Overview
This document summarizes the implementation of a new premium TP/SL strategy that trades both long and short using SELL entry, with strict exit priority: TP/SL first, then time-stop, then opposite premium signal.

## Strategy Requirements Implemented

### Entry Rules (Flat Only)
- On each bar, read analytics snapshot fields already produced (premium flags)
- Pine parity definition (already implemented upstream):
  - `premiumBuy = utBuy and macdBullish and rsiBullish and not rsiOverbought and aboveVWAP`
  - `premiumSell = utSell and macdBearish and rsiBearish and not rsiOversold and belowVWAP`
- If `current_position == 0`:
  - If `premiumBuy`: emit `BUY` (open long)
  - Else if `premiumSell`: emit `SELL` (open short)
- Assumption: buy and sell will not both be true on the same candle

### Exit Rules (In Position)
The strategy maintains state:
- `entry_price`
- `entry_bar_index` (or entry timestamp)
- `side / position_sign` (+1 long, -1 short)

#### TP/SL Levels Calculation at Entry:
- Long: `tp = entry_price * (1 + tp_pct)`, `sl = entry_price * (1 - sl_pct)`
- Short: `tp = entry_price * (1 - tp_pct)`, `sl = entry_price * (1 + sl_pct)`

#### Exit Evaluation Priority (on every next bar while in position):
1. **TP/SL first** (highest priority) using OHLC:
   - Long TP hit if `bar.high >= tp`, Long SL hit if `bar.low <= sl`
   - Short TP hit if `bar.low <= tp`, Short SL hit if `bar.high >= sl`
   - If both TP and SL are inside the same bar, SL wins (conservative approach)

2. **Time stop**:
   - If `(bar_index - entry_bar_index) >= max_hold_bars`: emit `EXIT`

3. **Opposite premium signal** (lowest priority):
   - If long and `premiumSell`: emit `EXIT`
   - If short and `premiumBuy`: emit `EXIT`

> **Important**: If EXIT is emitted because of opposite premium signal, no new BUY/SELL is emitted on that same bar—wait for the next bar when position is flat.

### Critical Engine Change: Full Position Close
- Added support for `metadata={"close_all": True}` in SignalEvent
- In `ExecutionHandler.process_signal()`, if signal type is EXIT and `close_all` is true, set order quantity = `abs(current_position)` ignoring confidence
- This ensures TP/SL/time/opposite exits close the whole position

## Files Created/Modified

### New Files
- `core/strategies/premium_tp_sl.py` - The new premium TP/SL strategy implementation

### Modified Files
- `core/execution/handler.py` - Updated to support full close on EXIT signals with `close_all` metadata
- `core/strategies/registry.py` - Registered the new strategy in STRATEGY_MAP
- `scripts/test_premium_tp_sl.py` - Test script to validate strategy behavior

## Strategy Configuration Parameters
- `tp_pct` (float, e.g., 0.006) - Take profit percentage
- `sl_pct` (float, e.g., 0.003) - Stop loss percentage  
- `max_hold_bars` (int) - Maximum number of bars to hold a position

## Signal Schema
The strategy emits `SignalEvent` with `signal_type` in `{BUY, SELL, EXIT}`:
- `BUY` opens long, `SELL` opens short when flat
- `EXIT` closes whatever current position exists (sell-to-close long, buy-to-cover short internally)

## Test Results
The strategy was validated with the following test cases:
- ✅ Entries only on premiumBuy/premiumSell signals
- ✅ Exits happen with correct priority (TP/SL → time-stop → opposite signal)
- ✅ No same-bar flipping behavior
- ✅ Position becomes exactly 0 after EXIT signals
- ✅ Proper SL/TP hit detection
- ✅ Time stop functionality

## Key Features
1. **Strict Exit Priority**: Implements the exact priority order specified
2. **No Same-Bar Flip**: Prevents entering new positions on the same bar as an exit
3. **Full Position Close**: Ensures complete closure on exit signals
4. **Flexible Configuration**: Allows customization of TP%, SL%, and max hold bars
5. **Robust State Management**: Tracks entry price, bar index, and position side
6. **Conservative Conflict Resolution**: SL wins when both TP and SL are hit in same bar

## Integration
The strategy is now registered in the system and available for backtesting and live trading through the existing infrastructure.