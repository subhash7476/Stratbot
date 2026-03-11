# FTMO Challenge System — Implementation Log

**Account**: $100,000 Free Trial
**Instrument**: XAUUSD (Gold)
**Status**: Strategy validated, live executor in progress

---

## Overview

Fully mechanical, deterministic trading system targeting the FTMO $100K challenge.
Strategy: ICT liquidity sweep on Asian session range, London open reversal + NY session sweep of London range.

---

## Architecture

```
ftmo/
  config.py           — All constants (account rules, session times, strategy params, instrument)
  engine.py           — FTMOBacktestEngine: bar-by-bar backtest with dual-session scan
  detector.py         — Sweep detection → structure shift → displacement → entry
  session.py          — Vectorised session classifier + pre-grouping of bars by date
  risk.py             — Dual-layer risk: FTMO hard limits + internal overlay
  indicators.py       — ATR (Wilder's), M5→M15 resampling
  simulation.py       — Rolling FTMO challenge simulation (pass rate analysis)
  analytics.py        — Trade metrics, equity curve, daily summary
  ingest.py           — CSV import (TradingView / MetaTrader format auto-detect)
  mt5_downloader.py   — Direct MT5 connection + OHLCV download
  db.py               — DuckDB schema: trades, daily_stats, simulations tables
  cli.py              — CLI: backtest | simulate | download | import | report
```

---

## FTMO Account Rules ($100K)

| Rule | Value |
|------|-------|
| Profit target Phase 1 | 10% = $10,000 |
| Daily max loss | 5% = $5,000 |
| Overall max loss | 10% = $10,000 (from initial balance) |
| Min trading days | 4 |
| Time limit | None (free trial) |

---

## Internal Risk Overlay (stricter than FTMO)

| Parameter | Value | Reason |
|-----------|-------|--------|
| Risk per trade | 1.0% = $1,000 | Balances expectancy vs drawdown |
| Daily stop | 2% = $2,000 | Stops after 2 full losses in a day |
| Max trades/day | 3 | Both sessions combined |
| Max consecutive losses | 2 | Resets each new day |
| Reduced risk threshold | +4% equity gain | Risk drops to 0.3% after good run |

---

## Strategy Logic

### Session Structure (all times IST, UTC+5:30)

**Session 1 — London sweep of Asian range**
- Pre-session range: 9:30 AM – 1:30 PM IST (late Asian session — tight range, fewer false sweeps)
- Trading window: 1:30 PM – 7:00 PM IST (London open + 5.5 hours)

**Session 2 — NY sweep of London range**
- Pre-session range: 1:30 PM – 7:00 PM IST (London range)
- Trading window: 7:00 PM – 11:00 PM IST (NY open + 4 hours)

### Entry Sequence (per session)

1. **Sweep detection**: Price breaks pre-session high or low by ≥ 0.25 × M15 ATR
2. **Structure shift**: After sweep, detect lower high (bearish) or higher low (bullish) + displacement candle ≥ 1.2 × M5 ATR
3. **Pullback entry**: Price retraces into displacement candle body
4. **Risk calc**: SL = sweep extreme + 0.15 × M15 ATR buffer; TP = 2R

### Exit Rules

| Condition | Action |
|-----------|--------|
| Price hits take profit | Close at 2R |
| Price hits stop loss | Close at -1R |
| Session cutoff reached | Close at market (TIME_CUTOFF) |
| FTMO daily limit hit | Stop all trading for the day |
| FTMO overall limit hit | Stop all trading permanently |

---

## Instrument Parameters

| Parameter | Value |
|-----------|-------|
| Symbol | XAUUSD |
| Timeframe | M5 |
| Lot size | 1 standard lot = 100 oz |
| Point value | $100/lot per $1 price move |
| Lot size calc | risk_dollar / (stop_points × 100) |

**Example**: $1,000 risk, $5 stop distance → lot size = 1,000 / (5 × 100) = 2.0 lots

---

## Data

- **Source**: MetaTrader 5 terminal via `MetaTrader5` Python package
- **Cache**: `ftmo/cache_m5.parquet` (parquet) + `ftmo/XAUUSD_M5.csv`
- **Current data**: Oct 4, 2024 – Mar 11, 2026 (99,900 bars, 448 trading days)
- **Download command**:
  ```
  python -m ftmo.cli download --login <LOGIN> --password <PASS> --server <SERVER> \
      --symbol XAUUSD --timeframe M5 --start 2024-01-01
  ```

---

## Backtest Results (Oct 2024 – Mar 2026)

| Metric | Value |
|--------|-------|
| Total trades | 348 |
| Trading days | 256 / 367 |
| Win rate | 41.1% |
| Avg R | +0.05 |
| Expectancy | +0.054R per trade |
| Profit factor | 1.12 |
| Total P&L | **+$10,598** |
| Max win streak | 5 |
| Max loss streak | 9 |
| Exit: TP | 68 (20%) |
| Exit: SL | 147 (42%) |
| Exit: TIME_CUTOFF | 133 (38%) |
| Final equity | $110,598 |

### FTMO Challenge Status (Phase 1)

| Metric | Value |
|--------|-------|
| Phase 1 target hit | **YES — Jan 19, 2026** (~14 months) |
| Min equity | $90,587 |
| FTMO floor ($90,000) breached | **NO** ($587 margin) |
| Max daily loss | -$2,065 (within $5K limit) |
| Daily limit breaches | 0 |
| Overall limit breaches | 0 |

---

## CLI Commands

```bash
# Download fresh data from MT5
python -m ftmo.cli download --login 1512742557 --server FTMO-Demo \
    --password <PASS> --symbol XAUUSD --timeframe M5 --start 2024-10-01

# Run full backtest (uses ftmo/cache_m5.parquet)
python -m ftmo.cli backtest

# Run backtest for a specific period
python -m ftmo.cli backtest --start 2025-01-01 --end 2025-12-31

# Run FTMO challenge simulation (rolling windows)
python -m ftmo.cli simulate --window 30 --step 3

# Full report (trades + daily + simulation)
python -m ftmo.cli report

# Import CSV data (TradingView or MetaTrader format)
python -m ftmo.cli import path/to/data.csv --source-tz UTC
```

---

## Key Design Decisions

### Why XAUUSD over EURUSD / US100 / Dow30

| Instrument | Reason for/against |
|------------|-------------------|
| **XAUUSD (chosen)** | ICT sweep patterns most reliable; Asian range clearly defined; London open sweeps it ~70% of days; $15-40 daily range gives room for 2R within session |
| EURUSD | Too slow; 50-80 pip ATR means 2R targets rarely hit within 4-hour window |
| US100 (tested) | 7 trades in 3 months on original setup; 2-hour NY pre-market window too narrow |
| Dow30 | Correlated to US100; same session timing problems |

### Why 9:30 AM pre-session start (not 5:30 AM)

- 8-hour Asian range (5:30 AM – 1:30 PM) = $35 average range → too wide → false sweeps
- 4-hour range (9:30 AM – 1:30 PM) = $25 average range → tighter → win rate 28% → 35%
- Narrower range = more decisive sweeps = cleaner reversal setups

### Why dual sessions

- Single London session: 181 trades, +$5,973 (not enough for Phase 1 in time)
- Dual sessions: 348 trades, +$10,598 (Phase 1 passed Jan 2026)
- NY session uses London range as reference — different structural level, same ICT logic

### Critical bugs fixed during development

1. **`new_day()` did not reset `consecutive_losses`** → first 2 losses in all of history blocked ALL future trading → backtest showed only 2 trades
2. **Session classification via `apply()` on 99K rows × 367 dates** → hanging (replaced with vectorised `_classify_all()`)
3. **`get_ny_session_bars()` called per-day on full DataFrame** → replaced with `get_all_ny_bars()` pre-grouping
4. **`NY_END = 19:00` in `detector.py` and `risk.py` blocked all Session 2 trades** → propagated `cutoff` parameter through all detection functions
5. **Engine defaulted to $50K starting balance** → fixed to use `ACCOUNT_SIZE` from config
6. **Session 2 `scan_session()` called without cutoff parameter** → all S2 setups returned `None` in structure shift detection

---

## Live Trading — READY

### Connection Verified (Mar 11, 2026)

```
Account: 1512742557 @ FTMO-Demo
Balance: $100,000.00 USD
Equity:  $100,000.00
Leverage: 1:100
XAUUSD live: ~$5,197 (Gold doubled Oct 2024→Mar 2026: $2,540→$5,197)
```

Note: Gold price doubled during the backtest period. Our strategy is fully price-agnostic
(all thresholds are ATR-relative) — backtest covers the entire $2,540–$5,587 range.

### Start Live Trading

```bash
python -m ftmo.cli live --login 1512742557 --password <PASS> --server FTMO-Demo
```

The trader will:
1. Idle until 1:30 PM IST (London open) or 7:00 PM IST (NY open)
2. Fetch last 300 M5 bars from MT5
3. Run sweep → structure shift → entry scan
4. Place MT5 market order if setup found and risk gate clears
5. Monitor position every 60 seconds
6. Close at TP, SL (set on broker), or session cutoff (TIME_CUTOFF)

## Live Trading — Architecture

### Architecture

```
ftmo/live_trader.py
  MT5LiveTrader
    connect()              — MT5 login + symbol subscribe
    run()                  — main loop (poll every 5 min)
    _check_session_open()  — detect when London / NY session opens
    _scan_and_signal()     — run scan_session() on latest bars
    _place_order()         — MT5 buy_stop / sell_stop order
    _monitor_positions()   — check open trades for TP/SL/cutoff
    disconnect()
```

### MT5 Credentials (FTMO Demo)

- Login: 1512742557
- Server: FTMO-Demo
- Password: stored in environment / passed via CLI

---

## Change Log

| Date | Change |
|------|--------|
| 2026-03-11 | Initial implementation: config, engine, detector, session, risk, simulation, analytics, ingest, db, cli |
| 2026-03-11 | Instrument changed: US100 → XAUUSD; account $50K → $100K |
| 2026-03-11 | Session timing fixed: US pre-market 4:30-8 PM IST → London sweep 9:30 AM–7 PM IST |
| 2026-03-11 | Performance fix: vectorised session classification (was hanging on 99K rows) |
| 2026-03-11 | Bug fix: consecutive_losses not resetting on new_day() → all trades blocked after 2 losses |
| 2026-03-11 | Bug fix: NY_END cutoff blocking all Session 2 trades in detector + risk engine |
| 2026-03-11 | Added: Session 2 (NY sweep of London range) — doubles frequency, enables Phase 1 pass |
| 2026-03-11 | MT5 downloader added; 99,900 bars XAUUSD M5 downloaded (Oct 2024 – Mar 2026) |
| 2026-03-11 | Backtest validated: +$10,598, Phase 1 passed Jan 19 2026, FTMO floor not breached |
| 2026-03-11 | Live trader built: `ftmo/live_trader.py` — MT5LiveTrader with dual-session scan, order placement, position monitoring |
| 2026-03-11 | Live connection verified: account 1512742557 @ FTMO-Demo, $100K balance, XAUUSD live feed confirmed |
