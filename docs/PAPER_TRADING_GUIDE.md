# 📝 Paper Trading System - User Guide

## Overview

The **Paper Trading System** allows you to test option recommendations with virtual money before going live. It integrates seamlessly with Page 13 (Options Analyzer) and provides real-time P&L tracking using live market data from Upstox API.

---

## 🎯 Key Features

### 1. Paper Trade Creation
- Create paper trades directly from option recommendations
- Automatic quantity calculation based on capital allocation
- Greeks tracking at entry

### 2. Live Order Book
- Real-time P&L updates using Upstox market data API
- Live LTP (Last Traded Price) fetching
- Unrealized P&L calculation for open positions
- Color-coded P&L display

### 3. Position Management
- Square off positions with live prices
- Realized P&L calculation
- Trade history log with statistics

### 4. Trade Analytics
- Win rate tracking
- Total P&L across all trades
- Strategy-wise breakdown
- Export to CSV for analysis

---

## 📊 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    PAGE 13: OPTIONS ANALYZER                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. Signal Analysis → Option Recommendations                    │
│  2. User clicks "📝 Paper Trade" button                         │
│  3. PaperTradingManager creates trade in database               │
│  4. Trade appears in Live Order Book                            │
│  5. Auto-refresh fetches live LTP from Upstox                   │
│  6. User squares off → Trade moves to Trade Log                 │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

Database: paper_trades table in DuckDB
API: Upstox Market Quote API for live LTP
```

---

## 🚀 How to Use

### Step 1: Generate Signals & Analyze Options

1. Go to **Page 13: Universal Options Analyzer**
2. Ensure you have active signals (from Page 4, 3, or 14)
3. Click **"🚀 Analyze Options for All Signals"**
4. Wait for option recommendations to appear

### Step 2: Create Paper Trade

For each option recommendation, you'll see 4 action buttons:

- **📝 Paper Trade** - Create virtual trade (THIS ONE!)
- **📈 Live Trade** - Coming in Phase 3
- **📊 Chain** - View full option chain
- **⏰ Alert** - Set price alerts

**To create a paper trade:**

1. Click the **"📝 Paper Trade"** button next to your chosen option
2. System will:
   - Generate unique trade ID (PAPER_YYYYMMDD_HHMMSS_XXX)
   - Calculate quantity based on capital allocation
   - Record entry price, Greeks, and timestamp
   - Store in database
3. Success message appears with trade details
4. Page refreshes to show new position in Order Book

### Step 3: Monitor Live P&L

The **Live Order Book** section shows:

```
📊 Live Order Book (Open Positions)
┌────────────┬────────┬──────────┬──────┬────────┬───────┬──────────┬─────┬──────────────┬───────┬────────────┐
│ Trade ID   │ Symbol │ Strategy │ Type │ Strike │ Entry │ Live LTP │ Qty │ Unrealized   │ P&L % │ Entry Time │
│            │        │          │      │        │       │          │     │ P&L          │       │            │
├────────────┼────────┼──────────┼──────┼────────┼───────┼──────────┼─────┼──────────────┼───────┼────────────┤
│ PAPER_...  │ RELI.. │ SQUEEZE  │ CE   │ 2450   │ ₹65.0 │ ₹72.5    │ 100 │ ₹+750.00     │ +11.5%│ 2026-01-17 │
│            │        │ 15M      │      │        │       │          │     │              │       │ 14:30      │
└────────────┴────────┴──────────┴──────┴────────┴───────┴──────────┴─────┴──────────────┴───────┴────────────┘
```

**Live Features:**
- **Live LTP**: Fetched from Upstox API in real-time
- **Unrealized P&L**: (Live LTP - Entry Price) × Quantity
- **P&L %**: Percentage gain/loss
- **🔄 Refresh P&L**: Manual refresh button (auto-refresh on page reload)

### Step 4: Square Off Position

When you want to close a position:

1. Scroll to **"Square Off Position"** section below the order book
2. Click the **"Close [SYMBOL] (+XXX)"** button
   - Button shows current unrealized P&L
3. System will:
   - Fetch current live LTP from Upstox
   - Calculate realized P&L
   - Update status to CLOSED
   - Move trade to Trade Log
4. Success message appears with realized P&L

### Step 5: Review Trade History

Expand the **📜 Trade Log (Closed Positions)** section to see:

- All closed trades (last 50)
- Entry and exit prices
- Realized P&L for each trade
- Trade log statistics:
  - Total Trades
  - Total P&L
  - Wins / Losses
  - Win Rate %
- Export to CSV for detailed analysis

---

## 📈 Paper Trading Statistics

At the top of Page 13, you'll see:

```
📈 Paper Trading Overview
┌────────────────┬──────────────────┬───────────────┬───────────┬──────────┐
│ Open Positions │ Capital Deployed │ Closed Trades │ Total P&L │ Win Rate │
├────────────────┼──────────────────┼───────────────┼───────────┼──────────┤
│      3         │    ₹15,000       │      12       │ ₹+2,450   │  75.0%   │
└────────────────┴──────────────────┴───────────────┴───────────┴──────────┘
```

**Metrics Explained:**
- **Open Positions**: Number of active paper trades
- **Capital Deployed**: Total capital locked in open positions
- **Closed Trades**: Total trades squared off
- **Total P&L**: Cumulative realized P&L across all closed trades
- **Win Rate**: % of profitable trades

---

## 🔧 Database Schema

### `paper_trades` Table

```sql
CREATE TABLE paper_trades (
    trade_id VARCHAR PRIMARY KEY,          -- PAPER_20260117_143015_001
    signal_id VARCHAR NOT NULL,            -- Reference to unified_signals
    symbol VARCHAR NOT NULL,               -- RELIANCE, TATASTEEL, etc.
    strategy VARCHAR NOT NULL,             -- SQUEEZE_15M, EHMA_MTF, etc.

    -- Option details
    option_instrument_key VARCHAR NOT NULL, -- NSE_FO|...
    option_type VARCHAR NOT NULL,          -- CE or PE
    strike_price DECIMAL(12,2) NOT NULL,
    expiry_date VARCHAR NOT NULL,

    -- Trade details
    side VARCHAR NOT NULL,                 -- BUY (for long positions)
    entry_price DECIMAL(12,2) NOT NULL,    -- Premium at entry
    quantity INTEGER NOT NULL,             -- Total lots × lot_size
    lot_size INTEGER NOT NULL,

    -- Entry info
    entry_time TIMESTAMP NOT NULL,
    entry_greeks TEXT,                     -- JSON: {delta, gamma, theta, vega, iv}

    -- Exit info (NULL for open positions)
    exit_price DECIMAL(12,2),
    exit_time TIMESTAMP,
    exit_greeks TEXT,

    -- P&L
    realized_pnl DECIMAL(12,2),            -- After square off
    unrealized_pnl DECIMAL(12,2),          -- For display only

    -- Status
    status VARCHAR DEFAULT 'OPEN',         -- OPEN or CLOSED
    notes TEXT,

    -- Timestamps
    created_at TIMESTAMP,
    updated_at TIMESTAMP
)
```

---

## 🔑 API Integration

### Upstox Market Quote API

**Endpoint**: `https://api.upstox.com/v2/market-quote/ltp`

**Usage**: Fetch live LTP for option contracts

**Example Request**:
```
GET /v2/market-quote/ltp?instrument_key=NSE_FO|12345
Headers:
  Authorization: Bearer {access_token}
  Accept: application/json
```

**Example Response**:
```json
{
  "status": "success",
  "data": {
    "NSE_FO|12345": {
      "last_price": 72.5
    }
  }
}
```

**Implementation**: See [core/paper_trading.py](../core/paper_trading.py) → `fetch_live_ltp()`

---

## 💡 Usage Examples

### Example 1: Basic Paper Trade

**Scenario**: RELIANCE 2450 CE recommendation appears

**Steps**:
1. Recommendation shows:
   - Strike: 2450 CE
   - Premium: ₹65.00
   - Capital Required: ₹6,500 (100 qty)
   - Rank Score: 87/100
2. Click **"📝 Paper Trade"**
3. Trade created:
   - Trade ID: `PAPER_20260117_143015_001`
   - Entry: ₹65.00
   - Quantity: 100
4. Appears in Live Order Book immediately

### Example 2: Monitoring P&L

**Scenario**: Market moves in your favor

**Before**:
```
Entry: ₹65.00 | Live LTP: ₹65.00 | Unrealized P&L: ₹0.00 (0.0%)
```

**After 15 minutes**:
```
Entry: ₹65.00 | Live LTP: ₹72.50 | Unrealized P&L: ₹+750.00 (+11.5%)
```

**What happened**:
- Underlying moved toward target
- Option premium increased by ₹7.50
- P&L = (₹72.50 - ₹65.00) × 100 = ₹750

### Example 3: Square Off at Profit

**Scenario**: Target hit, time to exit

**Steps**:
1. Live LTP shows ₹85.50 (Entry was ₹65.00)
2. Unrealized P&L: ₹+2,050 (+31.5%)
3. Click **"Close RELIANCE (+2050)"**
4. System fetches current LTP (₹85.50)
5. Realized P&L calculated: ₹+2,050
6. Trade moves to Trade Log
7. Stats updated:
   - Closed Trades: 1
   - Total P&L: ₹+2,050
   - Win Rate: 100%

### Example 4: Square Off at Loss

**Scenario**: Stop loss hit

**Steps**:
1. Underlying breached stop loss
2. Live LTP: ₹50.00 (Entry was ₹65.00)
3. Unrealized P&L: ₹-1,500 (-23.1%)
4. Click **"Close RELIANCE (-1500)"**
5. Realized P&L: ₹-1,500
6. Trade closed and logged

---

## 📊 Performance Tracking

### Win Rate Calculation

```
Win Rate = (Number of Profitable Trades / Total Closed Trades) × 100
```

**Example**:
- Closed Trades: 20
- Wins: 15 (P&L > 0)
- Losses: 5 (P&L < 0)
- Win Rate: (15 / 20) × 100 = 75%

### Average P&L

```
Avg P&L = Total Realized P&L / Total Closed Trades
```

**Example**:
- Total P&L: ₹+10,000
- Closed Trades: 20
- Avg P&L: ₹+500 per trade

### Profit Factor

```
Profit Factor = Total Winning Amount / Total Losing Amount
```

**Example**:
- Total Wins: ₹15,000
- Total Losses: ₹5,000
- Profit Factor: 3.0 (for every ₹1 lost, you make ₹3)

---

## ⚠️ Important Notes

### Live LTP Fetching

- **Market Hours**: LTP is only updated during market hours (9:15 AM - 3:30 PM IST)
- **After Hours**: LTP will show last traded price from the day
- **Weekends**: LTP remains at Friday's close
- **API Limits**: Upstox has rate limits; don't spam the refresh button

### Position Sizing

- Default capital per trade: ₹50,000 (configurable in sidebar)
- Quantity = Capital / (Premium × Lot Size)
- Minimum 1 lot will be traded
- Adjust capital in sidebar before creating trades

### Greeks Tracking

Currently tracking at entry:
- Delta: Directional exposure
- Gamma: Delta sensitivity
- Theta: Time decay
- Vega: Volatility sensitivity
- IV: Implied Volatility

**Note**: Live Greeks updating will be added in Phase 3

### Database Persistence

- All trades stored in DuckDB (`paper_trades` table)
- Database file: `data/trading.duckdb`
- Trades persist across sessions
- Safe to close/restart application

---

## 🐛 Troubleshooting

### "Could not fetch exit price"

**Cause**: Upstox API not returning LTP

**Solutions**:
1. Check if market is open
2. Verify access token is valid (go to Page 1)
3. Check instrument_key is correct
4. Try again after a few seconds

### "Error creating paper trade"

**Cause**: Missing recommendation data

**Solutions**:
1. Ensure option chain was fetched successfully
2. Check if recommendation object has all required fields
3. Try re-analyzing options
4. Check logs for specific error

### Live LTP not updating

**Cause**: Auto-refresh is manual

**Solutions**:
1. Click **"🔄 Refresh P&L"** button
2. Reload the entire page (F5)
3. Check if market is open
4. Verify internet connection

### Negative P&L showing green

**Cause**: Display formatting issue

**Solutions**:
- This is fixed in current version
- Losses show in red, profits in green
- Check the `delta_color` parameter

---

## 📈 Best Practices

### 1. Risk Management

- Start with small quantities (1-2 lots)
- Don't deploy more than 20% of capital at once
- Use stop losses from underlying signals
- Square off before market close (3:20 PM)

### 2. Trade Selection

- Only trade Score 5 signals initially
- Add Score 4 after gaining confidence
- Prefer options with Rank Score > 80/100
- Check liquidity (OI > 10,000)

### 3. Position Monitoring

- Refresh P&L every 15-30 minutes
- Don't over-trade based on small movements
- Trust your original signal and strategy
- Keep emotions in check

### 4. Exit Discipline

- Set profit targets (e.g., +30% ROI)
- Respect stop losses from signals
- Don't hold overnight unless confident
- Book partial profits if unsure

### 5. Record Keeping

- Export trade log weekly
- Review what worked and what didn't
- Track performance by strategy
- Adjust capital allocation based on results

---

## 🚀 Next Steps (Phase 3)

Planned enhancements:

1. **Live Order Execution**
   - Connect to Upstox Order API
   - Place real orders from recommendations
   - Order status tracking

2. **Advanced P&L Tracking**
   - Live Greeks updates
   - Intraday high/low tracking
   - MTM (Mark-to-Market) calculation

3. **Position Management**
   - Trailing stop loss automation
   - Partial exit functionality
   - Position adjustment tools

4. **Analytics Dashboard**
   - Strategy-wise performance
   - Daily/weekly/monthly reports
   - Win rate by time of day
   - Drawdown analysis

5. **Alerts & Notifications**
   - P&L threshold alerts
   - Target/SL hit notifications
   - Option chain change alerts
   - Greeks change alerts

---

## 📚 Related Documentation

- [Unified Signal System Guide](UNIFIED_SIGNAL_SYSTEM_GUIDE.md)
- [Squeeze → Options Pipeline](SQUEEZE_OPTIONS_QUICKSTART.md)
- [Option Recommender Technical Docs](SQUEEZE_OPTIONS_PIPELINE.md)

---

**Created**: 2026-01-17
**Version**: 1.0
**Status**: Production Ready
**Module**: [core/paper_trading.py](../core/paper_trading.py)
**UI**: [Page 13 - Options Analyzer](../pages/13_Option_Analyzer.py)
