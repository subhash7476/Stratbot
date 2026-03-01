# CLAUDE.md — Trading Platform

## Project Overview
Production-grade, deterministic algorithmic trading platform.
- **Language**: Python 3.10+
- **Database**: DuckDB (single source of truth)
- **Broker**: Upstox V2 (REST + WebSocket)
- **UI**: Flask + Tailwind CSS
- **Shell**: Use Unix syntax (forward slashes, `/dev/null` not `NUL`)

---

## Architecture Principles (DO NOT VIOLATE)

1. **Strategies Stay Dumb** — emit `SignalEvent` only; no broker/sizing/risk logic inside strategies
2. **Analytics Produce Facts** — all indicators pre-computed offline; runtime is read-only
3. **Execution Owns Reality** — risk, sizing, and broker interaction live exclusively in `core/execution/`
4. **Runner is Neutral** — single-threaded orchestrator; live and backtest data treated identically
5. **Audit-First** — every trade must be explainable by exact analytical facts

### Layer Flow
```
CLI Scripts → DuckDB → Core Logic → Facade → Flask UI
```

---

## Key Directories

| Path | Purpose |
|------|---------|
| `core/strategies/` | Strategy implementations (emit signals only) |
| `core/execution/` | Risk, sizing, broker interaction |
| `core/backtest/runner.py` | `BacktestRunner` with `_run_pixityAI_batch()` |
| `core/strategies/pixityAI_batch_events.py` | Vectorized event generation |
| `core/strategies/precomputed_signals.py` | Feed pre-computed events to backtest engine |
| `core/filters/` | Signal quality filters (Kalman, pipeline) |
| `core/strategies/regime/` | HMM regime observer/classifier/executor |
| `core/models/pixityAI_config.json` | PixityAI strategy config |
| `core/models/nifty_shield_config.json` | NiftyShield strategy config |
| `core/strategies/nifty_shield_strategy.py` | NiftyShield — self-contained weekly options seller |
| `scripts/nifty_shield_runner.py` | NiftyShield live daemon (30s poll) |
| `scripts/nifty_shield_backtest.py` | NiftyShield walk-forward backtest |
| `flask_app/blueprints/niftyshield.py` | NiftyShield Flask blueprint (`/nifty-shield/`) |
| `flask_app/` | Thin Flask UI — display only, no computation |
| `scripts/` | CLI entry points for backtests, scans, training |
| `data/market_data/nse/candles/1m/` | 1-min DuckDB candle files by date |
| `docs/` | Strategy research logs and implementation summaries |
| `docs/NIFTYSHIELD_IMPLEMENTATION.md` | Full NiftyShield design + API reference |

---

## Data Layout

- **1-min candles**: `data/market_data/nse/candles/1m/{YYYY-MM-DD}.duckdb`
  - Equities (`NSE_EQ|INE...`): 2024-10-17 to present
  - `NSE_INDEX|Nifty 50`: 2023-01-02 to present
  - `NSE_INDEX|Nifty Bank`: 2023-01-02 to present (backfilled Feb 2026, 292K bars)
- **Daily intermarket**: `data/market_data/nse/candles/1d/{date}.duckdb` (Nifty 50, Bank Nifty, India VIX)
- **Symbol format**: `NSE_EQ|INE...` (equities), `NSE_INDEX|Nifty 50` / `NSE_INDEX|Nifty Bank` (index)
- **ALL NSE_INDEX symbols have volume=0** — never use VWAP or vol_z filters on index data
- **BankNifty ingest script**: `scripts/fetch_intermarket_data.py --include-1m` (uses 10-day chunks for 1m — 29-day chunks cause sporadic 400s)

---

## Backtesting Rules

- **Disable idempotency guard**: `execution._is_signal_already_executed = lambda sid: False`
- **90-day warmup**: data loading extends before `start_time` for indicator computation
- **Swing detection is CAUSAL**: use `result.iloc[i + period]` assignment — never centered window
- **Position stacking guard**: handler must block new entry while a position is open on same symbol
- **Position tracker must update on paper fills**: `FillEvent` → `position_tracker.update_from_fill()`
- **Fee model**: NSE equity intraday — Rs 20 brokerage + STT 0.025% + exchange/SEBI/GST/stamp

---

## DayTypeEngine — Feature Blocks

| Block | Features | Notes |
|-------|----------|-------|
| A | gap_pct, prev_day_return, etc. | Excluded from 13pm prod model |
| B | open_5m_ret, open_30m_range, etc. | Opening structure |
| C–F | partial_return, partial_clv, TWAP, rotation | Intraday Nifty structure |
| **H** | **bn_nf_open_5m_spread, bn_nf_correlation_5m, etc.** | **BankNifty intermarket (new)** |

- **logistic_13pm_prod**: 41 features, Block A excluded, trained 2023–2025, **80% val accuracy**
- **Block H** computed in `build_intraday_features.py` + `DayTypeEngine._compute_block_h()`
- Live: `DayTypeEngine.on_bn_bar(bar)` feeds BN bars; `v9_pm_runner` fetches BN from live buffer
- Retrain: `python scripts/build_intraday_features.py && python scripts/train_daytype_classifier.py`

---

## NiftyShield Strategy — Current Config

- **Type**: Weekly short straddle, Nifty index options, premium selling
- **Entry**: 13:05pm after DayType checkpoint fires (13pm)
- **Sizing**: Choppy=2 lots, Trend=1 lot, VIX>16→–1 lot, VIX>20→skip
- **Exit**: profit_target 50% | stop_loss 2× | time_exit 15:15 | delta_adjustment >0.55
- **IV model**: VIX daily close ÷ 100 (flat); Black-76 synthetic pricing
- **DB tables**: `ns_paper_signals`, `ns_paper_trades` in trading.db
- **Dashboard**: `/nifty-shield/` (state, open position, Greeks, trade history)
- **Backtest**: `python scripts/nifty_shield_backtest.py --walkforward`
- **Full doc**: `docs/NIFTYSHIELD_IMPLEMENTATION.md`

---

## PixityAI Strategy — Current Config

- **Timeframe**: 15m (better than 1h — more trades, better edge)
- **Meta-model**: DISABLED (`skip_meta_model=True`) — anti-predictive on equities
- **Signal quality filter**: DISABLED — regime-dependent, catastrophic in hostile periods
- **R:R**: SL = 1×ATR, TP = 2×ATR, time stop = 12 bars
- **Profitable symbols** (Phase 6 scan): VEDL, BDL, KALYANKJIL, PNBHOUSING

---

## Known Pitfalls

- Trailing stops on intraday equity **hurt** — cut winners on normal pullbacks
- Directional filters (daily EMA trend) **removed winning counter-trend trades**
- Fee impact is massive at Rs 500 risk — STT alone is 0.025% of turnover per leg
- Single-period validation is misleading — always run full walk-forward
- Index data (Nifty) has volume=0 — kills vol_z and VWAP filters silently
- Position tracker not updated → equity=cash only, DD wrong, TP/SL/time stops never fire

---

## Development Conventions

- **No over-engineering** — don't add error handling, helpers, or abstractions for one-time use
- **No docstrings/comments** on code you didn't change
- **No backwards-compatibility shims** — delete unused code completely
- **Validate with train/test split** — in-sample results are meaningless
- Before modifying any file, **read it first** — understand existing patterns
- Prefer editing existing files over creating new ones
