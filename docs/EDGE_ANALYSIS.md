# Edge Analysis: Why Strategies Failed & Where Real Edge Exists

**Project:** PixityAI Trading Bot
**Last Updated:** 2026-02-22

---

## Table of Contents

1. [The Core Problem](#1-the-core-problem)
2. [Strategy-Level Failure Analysis](#2-strategy-level-failure-analysis)
3. [Systemic Issues Across All Versions](#3-systemic-issues-across-all-versions)
4. [The Fee Floor Problem](#4-the-fee-floor-problem)
5. [What Has Shown Positive Signal](#5-what-has-shown-positive-signal)
6. [Where Real Edge Exists in Indian Markets](#6-where-real-edge-exists-in-indian-markets)
7. [Proposed Next Strategy: OI-Anchored Intraday](#7-proposed-next-strategy-oi-anchored-intraday)
8. [What to Validate Before Building Anything New](#8-what-to-validate-before-building-anything-new)

---

## 1. The Core Problem

Every strategy in this project has been searching for **price-pattern edge on equity OHLCV data**. The fundamental issue is structural, not implementation:

**The three enemies of retail OHLCV-based strategies in India:**

1. **Institutional algo saturation.** Breakout, momentum, and mean-reversion signals on 1m/15m/daily bars are run by co-located algorithms with sub-millisecond execution. By the time a bar closes and your signal fires, algos have already moved the price. You are always filling after them.

2. **Fee floor.** NSE equity round-trip cost is 0.06–0.10% (brokerage + STT + exchange + stamp). Every trade needs a net directional move of at least this magnitude before generating any profit. Most OHLCV patterns produce moves in the 0.2–0.5% range, leaving only a thin margin after fees — which is quickly consumed by losing trades.

3. **No structural reason.** Patterns like "price broke out of a 20-day range" or "RSI < 35 after a 3% drop" have no first-principles reason to work. They are correlations found in historical data that disappear in future data (especially across market regimes). Every version tested here shows this: strong train-period results, weaker test results.

**The question to ask about any edge:** *Is there a structural, non-price reason why this inefficiency exists and persists?*

---

## 2. Strategy-Level Failure Analysis

### V3 & V4: Compression → Breakout

**Why it fails:**
A compressed (tight) range is a state of equilibrium — buyers and sellers are balanced. It is NOT stored energy. When equilibrium breaks, it resolves in the direction of whichever side has more pressure. That direction cannot be predicted from the compression pattern alone.

Expected outcome: ~50% directional win rate. After fees at 0.07%, even 50% win rate with 1:2 R:R gives negative expectancy.

**Test to confirm:** Count compression triggers; measure what % broke to the upside vs downside. If the answer is 48–52%, the premise is falsified.

### V5 & V6: 20-Day Momentum

**Why it fails:**
This is not alpha — it is Nifty beta. The 20-day high breakout with positive 20-day return fires primarily when the broad market is rising. In 2024 (bull market), it looked profitable. In 2025 H2 (consolidation/chop), it fails.

A simple test: correlate monthly v5 P&L with monthly Nifty return. If correlation > 0.7, you are buying Nifty beta with extra fees attached.

**Why long-only makes it worse:** In a falling or choppy market, every position is a loser. There is no offsetting short book.

### V7: Mean Reversion (RSI + 5d Return)

**Why it fails:**
The Nifty 200DMA gate is both the strategy's only useful feature AND its fatal flaw:
- It correctly gates out trades in bear markets (where oversold stocks keep falling)
- But it also gates out ALL trades in the exact market conditions where mean reversion works best (after sharp pullbacks in a sideways market)
- In practice, in TEST-B (2025-08 to 2026-02), if Nifty was below 200DMA for extended periods → zero trades → zero data to evaluate

The result: good gate performance in train, but near-zero trade sample in test.

### V8: 15m Intraday (OR Compression + Volume)

**Why it fails:**
- 5 simultaneous filters produce very few triggers: perhaps 2–5 trades per week. Small sample makes walk-forward unreliable.
- Force-flat at 15:20 is the worst exit time in the Indian session (low liquidity, institutional settlement pressure, wide spreads)
- Volume filter for individual stocks requires stable intraday volume profiles — these shift with index constituents, news, earnings calendars

### V9: PM Impulse at 13:00

**Why it is the most promising but still marginal:**
- Based on a trained model (80% holdout accuracy) — structural reason exists (day-type regime)
- BullTrend days genuinely do see PM new-day-highs 81% of the time — this is not pure noise
- BUT: expected value +0.036% vs 0.04% futures round-trip cost → net edge ≈ 0%
- The edge exists but is being consumed by costs and imprecise timing

---

## 3. Systemic Issues Across All Versions

### 3.1 No Regime Gate on Momentum Strategies

V5 and V6 take long momentum trades regardless of the Nifty's direction. Adding a simple filter — "only take longs when Nifty > 50-day MA" — would have eliminated most losing periods in 2025 H2.

### 3.2 Long-Only Bias

V5, V6, V7, and Combo are all long-only. This means when the market is falling:
- All positions are underwater simultaneously
- Maximum correlation at the worst possible time
- No short leg to offset losses

True market-neutral strategies (long strong / short weak, or long low-vol / short high-vol) are more resilient.

### 3.3 Position Correlation

Opening 5 positions in the v5/v6 momentum strategy on the same day means all 5 are long the same factor (Nifty direction). This is not 5 independent bets — it is one bet with 5x leverage.

True diversification requires:
- Multiple uncorrelated strategies (different signals, different time horizons)
- Or sector spread (one each from banking, IT, pharma, auto, energy)
- Or long-short pairs within a sector

### 3.4 Fixed Lookback Parameters

All strategies use fixed lookbacks: 20-day momentum, 14-period ATR, 5-day reversion.

Markets shift between regimes (trending → choppy → trending). In a fast-trending market, a 20-day breakout fires too late. In a choppy market, it fires and immediately reverses. An adaptive lookback (expanding window during low volatility, contracting during high) would better match the signal to current conditions.

### 3.5 ATR as a Universal Sizing Tool

Every strategy sizes stops and targets as multiples of ATR. The problem: ATR is a volatility measure, not a directional measure. In choppy markets, ATR is inflated — stops are wider — but the directional move never materializes to hit the target.

### 3.6 No Drawdown Circuit Breaker at Portfolio Level

Individual trades have stops. But there is no rule like: "If total portfolio is down 2% today, stop trading for the day." A single day with 5 simultaneous stops produces a 3.75% drawdown (5 × 0.75%). With no circuit breaker, the next day's losses compound.

---

## 4. The Fee Floor Problem

**The math every strategy must beat:**

```
NSE Equity Round-trip:
  Brokerage:        Rs 20 × 2 = Rs 40
  STT (sell side):  0.025%
  Exchange:         0.00345% × 2
  Stamp (buy):      0.003%
  Total on Rs 1L notional: ~Rs 70–80 = 0.07–0.08%

Nifty Futures Round-trip:
  Brokerage:        Rs 20 × 2
  STT (sell side):  0.0125%
  Exchange:         0.0019% × 2
  Total on Rs 10L lot: ~Rs 400 = 0.04%
```

**What this means for strategy design:**

For equity intraday trades targeting 0.3–0.5% moves:
- Fee consumption: 0.08% / 0.3% move = 27% of gross profit goes to fees
- One losing trade (-1.5R) = -0.45%: takes 6 winning trades (+0.3% each, net +0.22% after fees) to recover

For Nifty futures:
- Fee consumption: 0.04% / 0.2% target = 20% of gross
- Better economics, but smaller absolute moves in % terms

**Implication:** Strategies must either:
- Target larger moves (swing trades, not intraday) — but then execution matters less
- Use lower-cost instruments (Nifty futures, not F&O stocks)
- Achieve high win rates (>60%) with even R:R to offset fee drag

---

## 5. What Has Shown Positive Signal

From the entire research history, three things have shown genuine positive signal:

### 5.1 Day-Type Model at 13:00 (80% Holdout Accuracy)

The `logistic_13pm_prod` model achieves 80% accuracy on holdout data. BullTrend days produce new-day-highs 81% of the time in PM. This is real, reproducible, and structurally explainable (intraday order flow by 1pm is a strong predictor of closing direction).

The problem is not the signal — it is the trade structure and cost.

### 5.2 BullTrend PM Return (+0.076% Mean)

High-confidence BullTrend days average +0.076% PM return. Against 0.04% futures cost → net +0.036% E/trade. This is positive but marginal. Better entry timing and/or larger stop/target ratio could improve this.

### 5.3 Feature Engineering (53 Features, Strong Cluster Separation)

The day_features pipeline extracts genuinely discriminative features. The K-means clustering produces k=3 with ARI=0.981 and 0.892 cross-period centroid similarity. This means the market regime structure is real and stable across time. The infrastructure to trade it is solid — the trade execution is not yet optimized.

---

## 6. Where Real Edge Exists in Indian Markets

These are structurally motivated edges — each has a non-price, non-pattern reason to work:

### 6.1 Options Theta Decay (Most Reliable)

**Why it works:** NSE Nifty/BankNifty weekly options have implied volatility consistently above realized volatility by 3–5 vol points. Option sellers collect this premium systematically. The IV premium exists because retail buyers consistently overpay for protection (fear premium) and directional speculation.

**Implementation:**
- Short weekly straddle/strangle on Nifty (Monday or Tuesday entry)
- Exit Thursday before expiry (or at 50% premium capture)
- Delta hedge when |delta| > 0.30 using Nifty futures
- Hard stop: if premium doubles from entry, exit
- Position size: risk 1% of capital per straddle

**Requirements:** Options chain data, live delta monitoring, futures for hedging.

**Why not tried yet:** Requires options execution infrastructure. Current setup is equity/futures only.

### 6.2 Open Interest as Support/Resistance (Mechanical Flow)

**Why it works:** Market makers who sold options at the highest-OI strikes must delta-hedge as price moves. This creates mechanical buying/selling at those levels — not sentiment-driven, purely hedging-driven. The flow is predictable because it is non-discretionary.

**Implementation:**
- At 9:30 AM: pull NSE option chain (CE + PE OI by strike)
- Find max-pain strike (where total option premium loss for all buyers is maximum)
- If Nifty spot is > 0.8% away from max-pain, take position toward max-pain
- Stop: if distance widens by 0.4% more
- Target: within 0.2% of max-pain, or 3:00 PM
- Instrument: Nifty futures

**Why this is different:** Based on mechanical hedging flows. Not a price pattern — a structural microstructure effect.

### 6.3 Index Reconstitution (Passive Fund Forced Buying)

**Why it works:** When a stock is added to Nifty 50 / Nifty 100 / Nifty 200, passive funds (index ETFs, index funds) MUST buy it on or before the effective date. This is non-discretionary demand. The stock often rises 3–8% from announcement to effective date as passive funds accumulate.

**Implementation:**
- Monitor NSE index rebalancing announcements (quarterly)
- Buy additions 5–10 days before effective date
- Short deletions 5–10 days before effective date
- Exit on effective date open

**Data source:** NSE website publishes index reconstitution announcements.

### 6.4 Post-Earnings Announcement Drift (PEAD)

**Why it works:** Documented in academic literature on Indian markets (NSE-specific studies). When a company reports earnings that beat consensus significantly, institutional accumulation takes 3–5 days as positions are built. Retail investors systematically underreact to fundamental surprises.

**Implementation:**
- Screen for earnings beat > 15% above consensus EPS estimate
- Gap-up on announcement day + volume > 2× 20-day average
- Hold 3–5 days
- Stop: if price closes below announcement-day close

**Data source:** NSE bhav copy + quarterly earnings data (screener.in, Trendlyne).

### 6.5 Day-Type PM Impulse (Existing Infrastructure)

**Why it works:** 80% model accuracy at 13:00 is real. BullTrend days see new-day-highs 81% of the time. The edge exists.

**What needs to change to make it profitable:**
1. Switch from fixed stop/target to options-based execution:
   - Instead of futures, buy an ATM Nifty call (BullTrend) or put (BearTrend) at 13:00
   - Risk is limited to premium (1× cost if wrong)
   - Reward on BullTrend: call option moves 40–80% in value if new high prints
2. Increase holding time: instead of 35–45 min exit, hold to 15:15
3. Use only HIGH confidence (≥0.75) predictions to increase E/trade

---

## 7. Proposed Next Strategy: OI-Anchored Intraday

**Name:** OI Strike Fade
**Instrument:** Nifty 50 Futures
**Time window:** 9:30 AM entry, 3:00 PM or stop/target

### Setup

1. At 9:30 AM, fetch NSE Nifty option chain
2. Compute max-pain strike: `argmin_{K} sum over all strikes of max(0, K - S) × PE_OI + max(0, S - K) × CE_OI`
3. Compute distance: `dist = (Nifty_spot - max_pain_strike) / Nifty_spot`
4. Entry condition: `|dist| > 0.8%`
5. Direction: toward max-pain (if spot above max-pain → SHORT futures; if below → LONG futures)

### Trade Management

```
Stop:   if |dist| increases by 0.4% from entry (wrong direction, exit)
Target: when spot reaches within 0.2% of max-pain, or 3:00 PM
Size:   1% risk per trade (stop distance determines position size)
```

### Backtest Requirements

- NSE option chain data at 9:30 AM for each historical day
- Nifty 50 futures 1m data (already available)
- Compute max-pain from CE/PE OI by strike

**Data source:** NSE Bhav Copy (option) + existing DuckDB Nifty futures data.

### Why This Is Different

- Not a price pattern
- Not a momentum or mean-reversion signal
- Based on structural market-maker delta hedging mechanics
- Max-pain effect documented in Indian options literature
- Testable with available data

---

## 8. What to Validate Before Building Anything New

Before spending time on any new strategy, these questions should be answered with data:

### Q1: Does compression have directional edge?

```python
# For every compression trigger in v3/v4:
# What % of breakouts went in the "expected" direction?
# Expected direction = RS bias (long if RS > 0, short if RS < 0)
# If win rate < 53%: compression is not predictive → abandon v3/v4 entirely
```

### Q2: Is v5/v6 momentum just Nifty beta?

```python
# Compute monthly P&L from v5 backtest
# Compute monthly Nifty return
# Pearson correlation: if r > 0.7 → it is Nifty beta with extra fees
```

### Q3: Does day-type edge increase with higher confidence thresholds?

```python
# Stratify v9 results by confidence: [0.70-0.75), [0.75-0.80), [0.80+)
# Plot E/trade by confidence band
# If E/trade increases monotonically → higher threshold is better
# That threshold is the actionable operating point
```

### Q4: Does max-pain proximity predict Nifty direction?

```python
# For each day in 2023-2025:
# Compute max-pain strike at 9:30 AM
# If spot > max_pain + 0.8%: measure return to 3 PM
# What % of time did spot move toward max-pain by 3 PM?
# If > 60%: structural evidence exists
```

Answering these 4 questions with the existing data infrastructure will take 2–4 days of scripting and will either confirm or falsify the core premise of each approach. This is a more valuable use of time than building another v10 strategy.
