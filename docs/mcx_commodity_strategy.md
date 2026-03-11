# MCX Gold Options Strategy - Final Implementation Plan

## Strategy Goal
Build and deploy a rules-based **MCX Gold Options** trading strategy with strict risk controls and USDINR-assisted regime filtering.

## Core Design
- Instrument: MCX Gold Options (CE/PE), liquidity-screened
- Regime inputs: Gold trend + **USDINR historical regime from Upstox**
- Signal style: breakout/continuation on underlying context, expressed via options
- Risk: fixed equity risk, daily/weekly kill-switches, controlled concurrent exposure

## Implementation Phases

### Phase 1: Data Foundation
- Integrate Upstox historical pulls for MCX Gold options and USDINR
- Standardize schema and resampling
- Add data quality validations

### Phase 2: Regime and Signal Engine
- Implement gold + USDINR regime classifier
- Add options contract selection (expiry, strike, liquidity)
- Implement CE/PE directional entry rules and exit logic

### Phase 3: Backtesting and Validation
- Run event-driven replay with realistic costs/slippage
- Perform walk-forward validation and stress tests
- Produce attribution report (with/without USDINR filter)

### Phase 4: Paper Trading
- Deploy strategy in shadow mode
- Capture execution quality, slippage, and rule compliance
- Validate operations for at least 30 trades

### Phase 5: Small Live Rollout
- Start at 50% target risk
- Enforce hard risk caps and governance checklist
- Conduct weekly performance and risk reviews

### Phase 6: Controlled Scale
- Increase risk in steps only after stability gates pass
- Monitor drift, liquidity behavior, and drawdown profile
- Keep rollback guardrails active

## Risk Rules
- Risk per trade: 0.5% equity (0.75% cap for high-conviction)
- Daily loss cap: 1.5%
- Weekly loss cap: 3.5%
- Stop trading when kill-switch triggers

## Backtest Requirements
- USDINR from Upstox is mandatory
- Multi-regime dataset (trend, range, event-heavy periods)
- Include spread-based slippage and option liquidity filters

## Final Summary
The strategy is implementation-ready as a phased roadmap focused on **MCX Gold Options**, explicitly aligned to layered strategy design and using **Upstox USDINR historical data** as a core feature for both backtesting and live regime filtering.
