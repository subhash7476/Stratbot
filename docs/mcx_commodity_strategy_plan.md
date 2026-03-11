# MCX Gold Options Strategy Plan (Implementation-Ready)

## Objective
Design and execute a rules-based **MCX Gold Options** trading system with institutional risk controls, event discipline, and a phased deployment workflow.

## Design Basis
This plan follows the documentation style used in [docs/STRATEGY LAYOUT.md](docs/STRATEGY LAYOUT.md):
- layered architecture (regime -> signal -> execution -> risk)
- implementation-first sections
- explicit phase gates and acceptance criteria

## Trading Universe and Scope

### Primary Instrument
Trade **MCX Gold Options** only (CE/PE), using near-liquid contracts selected by liquidity filters.

### Instrument Selection Rules
- Underlying: MCX Gold contract family (exchange-resolved instrument keys from Upstox instrument master)
- Prefer current-month options with adequate OI and spread quality
- Strike selection by delta band (example: 0.25-0.40 for directional entries)
- Avoid illiquid far OTM strikes and contracts with abnormal bid-ask spread

### Secondary Expansion (Post-Stability)
Add Silver options only after 8+ weeks of stable live metrics and no risk-limit breaches.

## Data Architecture

### Mandatory Historical Inputs
- MCX Gold Options OHLCV + OI (from Upstox)
- MCX Gold futures/spot proxy for trend context
- **USDINR historical data from Upstox (mandatory for backtest and regime filter)**
- Event calendar tags (US CPI, NFP, FOMC)

### Data Contracts
- Normalize option candles and OI to a unified schema (`timestamp, instrument_key, expiry, strike, option_type, ohlcv, oi`)
- Normalize USDINR to the same session clock and resampling frequency used by strategy
- Persist cleaned datasets for deterministic replay backtests

## Strategy Architecture

### Layer 1: Regime Filter (Daily + Intraday Overlay)
Classify market into bullish, bearish, neutral using:
- Gold trend (moving average and breakout state)
- **USDINR direction and momentum (from Upstox historical feed)**
- event-risk window flags

Regime logic:
- Bullish: gold uptrend + supportive/stable USDINR context for INR-denominated pricing
- Bearish: gold downtrend + adverse macro-currency context
- Neutral: mixed signals -> reduced size or no-trade

### Layer 2: Signal Engine (Options-Focused)
Directional entries are generated on the underlying regime and executed through options:
- Long-bias setup -> buy CE
- Short-bias setup -> buy PE
- Trigger: breakout/continuation confirmation + liquidity checks
- Filters: minimum OI, max spread threshold, no-trade around extreme spread expansion

### Layer 3: Execution and Position Management
- Entry in two tranches: 60% confirmation, 40% pullback/continuation
- Initial stop via underlying ATR mapped to option premium risk
- Partial profit at +1.5R; trail residual using premium structure rules
- Time stop if setup does not progress in defined bar count

## Risk Management Framework (Non-Negotiable)
- Risk per trade: 0.5% of equity
- High-conviction cap: 0.75% of equity
- Daily loss cap: 1.5% of equity
- Weekly loss cap: 3.5% of equity
- Max concurrent bullish or bearish exposure: 2 option positions
- Hard kill-switch on daily cap breach

## Backtesting Framework (USDINR Integrated)

### Backtest Design
- Engine type: event-driven replay
- Instruments: MCX Gold options + USDINR series
- Period: multi-regime sample (trending, mean-reverting, event-heavy months)
- Costs: brokerage, slippage by spread bucket, and realistic option fill assumptions

### USDINR Usage in Backtest
- Regime feature: trend + volatility state of USDINR
- Signal filter: block trades when USDINR movement invalidates gold setup context
- Attribution: compare performance with and without USDINR filter to quantify edge contribution

### Evaluation Metrics
- Expectancy (R), win rate, avg win/loss
- Max drawdown, ulcer index, consecutive loss runs
- Event-day vs non-event-day performance
- Spread/liq rejection rate and realized slippage

## Phased Implementation Plan

### Phase 1: Data and Instrument Bedrock
Deliverables:
- Upstox historical fetch for MCX Gold options
- Upstox historical fetch for USDINR
- unified storage schema + validation checks

Acceptance Criteria:
- 12+ months of clean, replayable data
- <1% missing-bar gaps after market-hour normalization
- deterministic reload and query performance within target latency

### Phase 2: Regime + Signal Prototype
Deliverables:
- regime module using gold + USDINR
- options entry/exit rules with liquidity filters
- configurable strike/expiry selection policy

Acceptance Criteria:
- reproducible signals on historical replay
- parameter set versioned and auditable
- no look-ahead bias in feature generation

### Phase 3: Backtest and Robustness Validation
Deliverables:
- full backtest runs across train/validation windows
- walk-forward testing with rolling recalibration
- stress tests on event and spread shock periods

Acceptance Criteria:
- positive expectancy across validation windows
- drawdown within risk budget
- strategy performance remains viable after realistic costs

### Phase 4: Paper Trading (Shadow Live)
Deliverables:
- live data feed, paper orders, and trade journal
- real-time risk guardrails and kill-switches
- monitoring dashboards (fills, slippage, rule compliance)

Acceptance Criteria:
- 30+ paper trades with no critical rule breach
- stable execution quality vs modeled assumptions
- journal completeness >= 95%

### Phase 5: Small Live Deployment
Deliverables:
- go-live at 50% planned risk
- daily governance checklist and weekly risk review
- rollback protocol for anomaly sessions

Acceptance Criteria:
- sustained positive risk-adjusted performance for 6-8 weeks
- no repeated process/control failure
- drawdown and cap adherence fully intact

### Phase 6: Controlled Scale-Up
Deliverables:
- scale to full planned risk in steps
- monthly model drift and parameter stability review
- readiness gate for secondary instruments

Acceptance Criteria:
- stable equity curve behavior after scaling
- no degradation in slippage-adjusted expectancy
- operational discipline retained

## Governance and Operations
- verify MCX circular/spec updates weekly
- revalidate instrument mapping/expiry roll logic
- enforce no-trade windows near expiry delivery complexity
- maintain immutable trade logs and weekly post-trade audit

## Final Summary
This plan is now fully shifted from MCX gold futures to **MCX Gold Options**, integrates **USDINR historical data from Upstox as a mandatory backtesting and regime input**, and is broken into **six implementation phases** with explicit deliverables and acceptance criteria. The execution sequence is: data reliability first, then signal quality, then robustness validation, then staged deployment.
