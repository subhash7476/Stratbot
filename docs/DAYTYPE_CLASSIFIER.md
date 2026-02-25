# Day-Type Classifier

**Project:** PixityAI Trading Bot
**Document:** Intraday Day-Type Clustering + Classification System
**Last Updated:** 2026-02-22

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Phase 1: Feature Engineering](#2-phase-1-feature-engineering)
3. [Phase 2: Unsupervised Clustering](#3-phase-2-unsupervised-clustering)
4. [Phase 3: Intraday State Engine](#4-phase-3-intraday-state-engine)
5. [Phase 4: PM Session Expectancy](#5-phase-4-pm-session-expectancy)
6. [Phase 5: V9 Backtest](#6-phase-5-v9-backtest)
7. [Model Files](#7-model-files)
8. [Known Accuracy Results](#8-known-accuracy-results)
9. [Key Scripts](#9-key-scripts)
10. [Production Usage](#10-production-usage)

---

## 1. Purpose

The day-type classifier answers one question: **given data available at a checkpoint (10am / 11am / 1pm), what is today's market regime?**

Three regimes are used:
- **BullTrend** — market trending up for the day
- **BearTrend** — market trending down for the day
- **Choppy** — range-bound / directionless day

This classification drives the v9 PM impulse strategy: at 13:00, if the model predicts BullTrend or BearTrend with ≥70% confidence, take a direction trade.

---

## 2. Phase 1: Feature Engineering

**Script:** `scripts/generate_day_features.py`
**Engine:** `core/analytics/day_features.py`
**Output:** `data/features/day_type/nifty_day_features_{YYYY}.csv`
**Instrument:** Nifty 50 Index (index, not futures)
**Data used:** 1-minute bars, 9:15–15:29

### Feature Blocks

#### Block A: Gap & Previous Context (8 features)

| Feature | Description |
|---------|-------------|
| `gap_pct` | Gap open as % of previous close |
| `gap_dir` | Gap direction: +1 up, -1 down, 0 flat |
| `gap_size_pct60` | Percentile rank of |gap| over 60-day rolling window |
| `prev_day_return` | Previous day (close/open - 1) |
| `prev_day_range` | Previous day (high - low) / open |
| `prev_day_clv` | Previous day Close Location Value: (close - low) / (high - low) × 2 - 1 |
| `prev_day_slope` | Previous day linear regression slope normalized by range |
| `prev_day_vol_pct` | Percentile rank of previous day realized vol over 20-day rolling |

**Note:** Block A is EXCLUDED from the production 13pm model. It produced higher accuracy WITHOUT Block A features (likely because gap context provides stale prior-day information that does not predict intraday behavior reliably at 13:00).

#### Block B: Opening Structure (9 features)

| Feature | Description |
|---------|-------------|
| `open_5m_ret` | Return: open to 5m bar close |
| `open_15m_ret` | Return: open to 15m bar close |
| `open_30m_ret` | Return: open to 30m bar close |
| `open_30m_range` | (high - low) / open of first 30m |
| `open_30m_range_ratio` | First 30m range / full day range |
| `open_30m_high_break_min` | Minute when first 30m high was broken (or -1 if never) |
| `open_30m_low_break_min` | Minute when first 30m low was broken (or -1 if never) |
| `open_30m_twap_dist` | TWAP distance at bar 30 as % |
| `open_30m_vol_ratio` | First 30m volume / total volume (always 0 for index) |

#### Block C: Intraday Trend (8 features)

| Feature | Description |
|---------|-------------|
| `full_day_return` | (close - open) / open |
| `day_range_pct` | (high - low) / open |
| `clv` | Close Location Value for full session |
| `linreg_slope` | Linear regression slope on 1m closes / open |
| `linreg_r2` | R² of linear regression (trend linearity) |
| `hh_count_15m` | Ratio of 15m bars with higher high than prior bar |
| `ll_count_15m` | Ratio of 15m bars with lower low than prior bar |
| `max_twap_excursion` | Max distance from TWAP / day_range |

#### Block D: Volatility Structure (9 features)

| Feature | Description |
|---------|-------------|
| `realized_vol` | Std of 1m returns (not annualized) |
| `intraday_atr_5m` | ATR from 5m bars / open |
| `range_pct_vs20d` | Percentile rank of day_range_pct over 20-day rolling |
| `range_pct_before_11am` | (high - low) / day_range for bars 0–105 (9:15–10:59) |
| `range_pct_after_130pm` | (high - low) / day_range for bars 255+ (13:30–15:29) |
| `largest_5m_candle` | Max 5m range / day_range |
| `log_vol_expansion` | log(AM std / PM std) — AM vs PM volatility ratio |
| `vol_clustering` | Autocorrelation of |1m returns| at lag-1 |
| `center_of_mass_return_time` | Weighted center of return activity in [0, 1] time window |

#### Block E: TWAP/VWAP Microstructure (7 features)

| Feature | Description |
|---------|-------------|
| `pct_min_above_twap` | % of 1m bars where close > TWAP |
| `pct_min_below_twap` | % of 1m bars where close < TWAP |
| `twap_cross_count` | Number of times close crosses TWAP |
| `longest_above_twap` | Longest consecutive stretch above TWAP (bars) |
| `longest_below_twap` | Longest consecutive stretch below TWAP (bars) |
| `close_dist_twap` | (close - TWAP) / open as % |
| `twap_dist_std` | Std of (close - TWAP) across all bars |

**TWAP computation:** True VWAP if volume > 0; TWAP fallback (expanding mean of HLC3) if volume = 0 (index has no volume data).

#### Block F: Intraday Rotation (7 features)

| Feature | Description |
|---------|-------------|
| `flip_count_15m` | Direction changes in 15m returns (trend interruptions) |
| `inside_bar_pct_15m` | % of 15m bars entirely inside prior bar |
| `median_body_pct_15m` | Median |close - open| / open across 15m bars |
| `avg_adverse_excursion` | Average adverse excursion (gated: only when |CLV| ≥ 0.2) |
| `max_adverse_excursion` | Maximum adverse excursion |
| `overlap_ratio_15m` | Price overlap ratio between consecutive 15m bars |
| `dominant_direction_strength` | |CLV| if |CLV| ≥ 0.2, else 0 |

#### Block G: Volume (5 features — always 0 for Nifty index)

`total_vol_pct20`, `first_hour_vol_pct`, `vol_skew_ampm`, `vol_acceleration`, `vol_wtd_momentum`

All zero because Nifty 50 index has no volume data. These are dropped before clustering.

### Timing Constants

```python
AM_CUTOFF_BAR   = 105   # 11:00 AM (9:15 + 105 minutes)
PM_START_BAR    = 255   # 13:30 PM (9:15 + 255 minutes)
FIRST_HOUR_END  = 60    # 10:15 AM
EXPECTED_BARS   = 375   # 9:15 to 15:29 inclusive
CLV_THRESHOLD   = 0.2   # Gate for adverse excursion computation
```

### Causal Guarantee

All rolling percentile features use only historical windows (trailing). No look-ahead.

Features that use full-day data (e.g., `full_day_return`, `clv`, `day_range_pct`) are only meaningful as labels/targets at day-end — not usable at 10am/11am checkpoints. The intraday engine constructs partial feature sets at each checkpoint using only available data.

---

## 3. Phase 2: Unsupervised Clustering

**Script:** `scripts/cluster_day_types.py`
**Input:** `data/features/day_type/nifty_day_features_*.csv`
**Algorithm:** K-means
**Optimal k:** 3

### Pipeline Steps

1. Load all yearly CSVs (2023–2026)
2. Prune degenerate/redundant columns:
   - Block G (volume — all zeros)
   - 4 correlated pairs identified by audit
   - 18 total columns dropped → 35 features remain
3. Drop NaN rows (from rolling warmup periods)
4. Winsorize flagged features at 1%/99% limits
5. StandardScaler (z-score normalization)
6. PCA (retain 87% cumulative variance → typically 12–18 components)
7. K-means with k=3..8; select best k using 4 diagnostics
8. Stability tests:
   - Seed ARI: 10 random seeds, mean ARI (target: >0.9)
   - Subperiod similarity: centroids from 2023-24 vs 2025-26 (target: >0.8)
9. Post-cluster ANOVA + centroid interpretation

### Cluster Selection Diagnostics

```python
def select_k(X_pca, k_range=range(3, 9)):
    # For each k:
    # - Silhouette score (higher = better)
    # - Calinski-Harabasz score (higher = better)
    # - Davies-Bouldin score (lower = better)
    # - Min cluster size % (avoid degenerate tiny clusters)
```

### Stability Functions

```python
def seed_stability(X_pca, k, n_seeds=10) -> float:
    # Computes mean ARI across 10 random seeds
    # ARI = 1.0 means identical clusters regardless of init

def hungarian_match(centroids_a, centroids_b) -> float:
    # Optimal matching of cluster centroids between two runs
    # Returns mean cosine similarity

def subperiod_stability(X_pca, df_clean, k) -> float:
    # Fits model on 2023-24 data, another on 2025-26 data
    # Compares centroids with hungarian_match
```

### Results: k=3

**Stability metrics (from `INDEX_MICROSTRUCTURE_PROFILING.md`):**
- ARI (seed stability): **0.981** — near-perfect reproducibility
- Centroid similarity (2023-24 vs 2025-26): **0.892** — stable regime structure across years

### Cluster Labels

| Cluster | Name | Size | Key Characteristics |
|---------|------|------|---------------------|
| 0 | **BearTrend** | 28.2% of days | CLV −1.0σ, linreg_r2 high, hh_count low, ll_count high, new-day-low rate 80.7%, pm_return −0.05% |
| 1 | **BullTrend** | 32.8% of days | CLV +1.0σ, linreg_r2 high, hh_count high, ll_count low, new-day-high rate 81.2%, pm_return +0.076% |
| 2 | **Choppy** | 39.0% of days | Low linreg_r2 (non-linear price path), high twap_cross_count (many crossings), range concentrated early, pm_range compressed |

### Diagnostic Features (Post-cluster ANOVA)

Most discriminative features between clusters:
- `day_range_pct` — total range magnitude
- `linreg_r2` — trend linearity
- `clv` — close location (where close sits in day range)
- `pct_min_above_twap` — time above TWAP
- `flip_count_15m` — intraday direction changes
- `center_of_mass_return_time` — when returns concentrate
- `range_pct_before_11am` — AM range contribution
- `range_pct_after_130pm` — PM range contribution
- `hh_count_15m` / `ll_count_15m` — trend structure

---

## 4. Phase 3: Intraday State Engine

**Script:** `scripts/live_daytype_engine.py`
**Engine class:** `DayTypeEngine` (in `core/state/`)
**Model used:** Logistic regression classifiers per checkpoint

### Checkpoints

| Checkpoint | Time | Input bars | Bar index |
|-----------|------|-----------|-----------|
| 10am | 10:00 | 9:15–10:00 (45 bars) | 45 |
| 11am | 11:00 | 9:15–11:00 (105 bars) | 105 |
| 13pm | 13:00 | 9:15–13:00 (225 bars) | 225 |

At each checkpoint, the engine computes partial features (only features that can be computed from available bars) and runs the corresponding classifier.

### Lock Mechanism

Once a checkpoint reaches high confidence (≥0.70), the engine enters a 5-bar stability watch. If the prediction remains stable across 5 consecutive bars → state is **locked**. Locked state persists for the rest of the session (not re-evaluated).

### Usage: Replay Mode

```bash
# Single day replay
python scripts/live_daytype_engine.py --date 2025-06-03

# Range replay
python scripts/live_daytype_engine.py --start 2025-01-01 --end 2025-12-31

# JSON output
python scripts/live_daytype_engine.py --date 2025-06-03 --json
```

**Output per day:**
```json
[
  {
    "checkpoint": "10am",
    "predicted_state": "BullTrend",
    "confidence": 0.73,
    "conf_tier": "high",
    "locked": false,
    "actual": "BullTrend",
    "correct": true
  },
  ...
]
```

### AM Checkpoint Edge Analysis

**Script:** `scripts/diagnose_am_checkpoint_edge.py`

Evaluated whether 10am and 11am checkpoints produce tradeable edge from checkpoint to 12:59.

```python
ENTRY_BAR = {"10am": 45, "11am": 105}  # bar indices
# Entry: open of bar at entry_bar index
# Exit: close of last AM bar (12:59)
# Cost: 0.04% round-trip
```

**Decision gate:**
```
High-conf E/trade >= 0.10% AND t-stat >= 2.5 → "OPEN — Path A viable"
E/trade 0.05% with t < 1.5 → "BORDERLINE"
Otherwise → "CLOSE — insufficient edge"
```

**Result:** AM checkpoints (10am, 11am) → **CLOSED** (insufficient edge in AM session).

---

## 5. Phase 4: PM Session Expectancy

**Script:** `scripts/run_pm_expectancy.py`
**Input:** `logistic_13pm_prod` model + 1m bars 13:00–15:30
**Output:** `data/features/day_type/pm_expectancy_raw.csv`

### PM Metrics Computed Per Day

```python
def compute_pm_metrics(df_1m, d) -> dict:
    # pm_return        — return from 13:00 to 15:30 close
    # pm_max_gain      — max favorable excursion from entry
    # pm_max_loss      — max adverse excursion from entry
    # pm_range         — (max_high - min_low) / entry
    # pm_vol           — realized vol of 1m PM returns
    # pm_trend_strength— linreg slope of PM session
    # pm_close_loc     — (close - pm_low) / pm_range (CLV for PM)
    # pm_close_vs_twap — (close - PM_TWAP) / entry
    # pm_adverse       — adverse excursion in trade direction
    # pm_favorable     — favorable excursion in trade direction
    # pm_positive      — 1 if pm_return > 0 else 0
    # pm_new_day_high  — 1 if PM high > AM high
    # pm_new_day_low   — 1 if PM low < AM low
    # pm_bar_to_high   — bar index where day high printed
    # pm_bar_to_low    — bar index where day low printed
```

### Confidence Tiers

| Tier | Threshold |
|------|-----------|
| HIGH | confidence ≥ 0.70 |
| MED | 0.55 ≤ confidence < 0.70 |
| LOW | confidence < 0.55 |

### PM Metrics by Day Type (High Confidence, all 743 days)

| Metric | BullTrend (n=170) | BearTrend (n=124) | Choppy (n=130) |
|--------|------------------|------------------|----------------|
| pm_positive rate | 59.4% | 46.8% | 47.7% |
| new_day_high rate | **81.2%** | 23.8% | 23.8% |
| new_day_low rate | 19.4% | **80.7%** | 38.9% |
| mean pm_return | +0.076% | -0.05% | ~0% |
| adverse_p50 | 0.095% | 0.119% | — |

**Key insight:** BullTrend days have 81.2% chance of printing a new session high in PM. BearTrend days have 80.7% chance of printing a new session low. This is the structural basis for v9.

---

## 6. Phase 5: V9 Backtest

**Script:** `scripts/run_v9_backtest.py`
**Full details:** See `STRATEGY_RESEARCH_LOG.md` → Section 9

### Best Result

- **Strategy:** BullTrend prediction → LONG Nifty 50 Futures at 13:02
- **Expected value:** +0.036% per trade (hold to PM close)
- **Win rate:** 52.9%
- **Cost:** 0.04% round-trip (futures)
- **Net edge:** ~0% after costs (marginal positive, not reliably above noise)

### Path Forward

The thin edge suggests the model is directionally correct but the entry/exit window is not capturing the full PM move. Options:
1. Widen time exit to 15:15 (capture more of the trend)
2. Use Nifty options instead of futures (limited risk, asymmetric payoff)
3. Stack with other filters to increase precision (reduce trades, increase E/trade)

---

## 7. Model Files

```
models/daytype/
├── lgbm_10am/
│   ├── model.joblib       # LightGBM, trained on 10am features
│   └── config.json        # Features used, thresholds
│
├── lgbm_11am/
├── lgbm_13pm/
│
├── logistic_10am/
├── logistic_11am/
│
├── logistic_13pm/
│   # Logistic regression, with Block A features
│   # Lower accuracy than prod model
│
└── logistic_13pm_prod/
    # PRODUCTION MODEL
    # Logistic regression, Block A EXCLUDED
    # Validation accuracy: 72.1%
    # Holdout accuracy: 80.0%
    # Features: Blocks B, C, D, E, F (no gap context)
```

**Train/test split:**
- Train: 2023-01-02 → 2024-12-31
- Validation: within training (cross-validation)
- Holdout: 2025-01-01 → 2026-02-13

---

## 8. Known Accuracy Results

| Model | Checkpoint | Val Accuracy | Holdout Accuracy | Notes |
|-------|-----------|-------------|-----------------|-------|
| `logistic_13pm` | 13pm | ~68% | ~71% | With Block A |
| `logistic_13pm_prod` | 13pm | **72.1%** | **80.0%** | Without Block A — PRODUCTION |
| `lgbm_13pm` | 13pm | ~70% | ~74% | LightGBM variant |
| `logistic_10am` | 10am | ~58% | ~61% | Insufficient edge at 10am |
| `logistic_11am` | 11am | ~62% | ~65% | Borderline at 11am |

**Key finding:** The 13pm model without Block A features achieves the best holdout accuracy (80%). This suggests prior-day context (gap, previous day return) is not helpful — the intraday patterns by 13:00 already contain all relevant information.

---

## 9. Key Scripts

| Script | Purpose |
|--------|---------|
| `scripts/generate_day_features.py` | Build 53-feature CSV from 1m Nifty data |
| `scripts/cluster_day_types.py` | K-means clustering, k=3, stability tests |
| `scripts/train_daytype_classifier.py` | Train LightGBM + Logistic models |
| `scripts/live_daytype_engine.py` | Replay or live checkpoint prediction |
| `scripts/run_pm_expectancy.py` | PM distribution by state + confidence |
| `scripts/diagnose_am_checkpoint_edge.py` | Test 10am/11am edge in AM session |
| `scripts/pm_timing_stats.py` | When does PM extreme print? |
| `scripts/run_v9_backtest.py` | Full backtest on PM impulse strategy |

---

## 10. Production Usage

### Generating predictions for a new day

```python
from core.state.daytype_engine import DayTypeEngine

engine = DayTypeEngine()
engine.reset()

# Feed 1m bars as they arrive
for bar in nifty_1m_bars:
    result = engine.process_bar(bar)
    if result.checkpoint_reached:
        print(f"Checkpoint: {result.checkpoint}")
        print(f"Prediction: {result.predicted_state}")
        print(f"Confidence: {result.confidence:.2f}")
        print(f"Locked: {result.locked}")
```

### Live action logic (v9 style)

```python
# At 13:00 bar
if engine.locked and engine.confidence >= 0.70:
    if engine.predicted_state == "BullTrend":
        # Enter LONG Nifty futures
        # Stop: 0.15% below entry
        # Target: 0.18% above entry
        # Time exit: 45 minutes (13:45 PM)
    elif engine.predicted_state == "BearTrend":
        # Enter SHORT Nifty futures
        # Mirror stop/target
```

### Monitoring accuracy over time

Run `scripts/live_daytype_engine.py --start {last_month} --end {today}` periodically to verify holdout accuracy is not degrading. If accuracy drops below 65% for 20+ consecutive days, retrain models.
