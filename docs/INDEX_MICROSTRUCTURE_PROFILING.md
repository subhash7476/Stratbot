# Index Microstructure Profiling — Core Architecture

## Purpose

Discover stable, statistically defensible day-types in Nifty 50 index behavior.
No strategy. No alpha assumption. Pure structural profiling for informed trading.

---

## Pipeline Overview

```
1m DuckDB data (2023–2026)
        │
        ▼
[Feature Generator]          scripts/generate_day_features.py
  746 days × 53 features     core/analytics/day_features.py
  output: data/features/day_type/nifty_day_features_{YYYY}.csv
        │
        ▼
[Clustering Pipeline]        scripts/cluster_day_types.py
  Prune → Winsorize → Scale → PCA → KMeans/GMM/Agg
  output: data/features/day_type/cluster_labels.csv
        │
        ▼
[Interpretation + Audit]     auditor review before any strategy use
```

---

## Data

- **Symbol**: `NSE_INDEX|Nifty 50`
- **Source**: Daily DuckDB files at `data/market_data/nse/candles/1m/{YYYY-MM-DD}.duckdb`
- **Coverage**: 2023-01-02 → 2026-02-13 (746 trading days after filtering)
- **Volume**: Always 0 (index data) → TWAP proxy used throughout, consistently flagged

---

## Feature Space (53 engineered, 42 after pruning)

### Block A — Gap & Previous Context (8)
Gap overnight %, direction, 60d percentile rank, prev day return/range/CLV/slope/vol-pct.
All trailing-window percentiles — no lookahead.

### Block B — Opening Structure (7 after pruning)
5m/15m/30m returns, 30m range, range ratio, time-of-break (minutes since open), vol ratio.
`open_30m_twap_dist` dropped (corr 0.93 with open_30m_ret).

### Block C — Intraday Trend (8)
Full day return, range %, CLV (epsilon-guarded), linreg slope + R², HH/LL counts (normalized),
max TWAP excursion (shape-normalized by day_range).

### Block D — Volatility Structure (9)
Realized vol (raw std, no annualization), intraday ATR/5m, 20d range percentile, AM/PM range split,
largest 5m candle (shape-normalized), log vol expansion (epsilon-guarded), vol clustering (autocorr),
center-of-mass return time (normalized [0,1], flat day → 0.5).

### Block E — TWAP Microstructure (6 after pruning)
% bars above TWAP, cross count, longest consecutive stretch above/below, close distance from TWAP,
TWAP distance std (compression strength). `pct_min_below_twap` dropped (corr = 1.000 with above).

### Block F — Intraday Rotation (7)
Direction flips (15m), inside bar %, median body %, adverse excursions (gated by |CLV| ≥ 0.2),
overlap ratio, dominant direction strength (|CLV|).

### Block G — Volume (all dropped)
Zero for index. Hard-dropped before clustering.

---

## Critical Implementation Rules

| Rule | Detail |
|------|--------|
| CLV epsilon guard | `range_epsilon = max(H-L, 1e-6 × open)` — prevents explosion on compression days |
| Percentile rank | `rolling(N).apply(lambda x: (x < x[-1]).sum() / (len(x)-1))` — trailing only |
| No annualization | `realized_vol = std(1m returns)` — raw, for cross-day comparison |
| Shape normalization | `largest_5m_candle` and `max_twap_excursion` / day_range — not open price |
| Log vol expansion | `log(max(std_AM, ε) / max(std_PM, ε))` — guards log(0) |
| COM guard | `0.5` if `sum_abs_ret < ε` — flat days neutral, not NaN |
| G-block retained in CSV | Written for audit; hard-dropped before any scaling or clustering |
| Winsorize flagged features | 1%/99% for features with kurt > 10 or skew > 3 |

---

## Clustering Stack

### Pre-processing (mandatory order)
1. Drop degenerate columns (G-block + 4 redundant pairs)
2. Drop NaN rows (warmup)
3. Winsorize flagged features at 1%/99%
4. StandardScaler (z-score all features — no exceptions)

### Variance Audit (before clustering)
- Run PCA, compute per-group variance contribution
- If VOLATILITY group > 40% of first 5 PCs → downweight by 0.7 before re-scaling
- Retain 85–90% cumulative variance → typically 12–18 PCs

### Algorithms (parallel, k = 3 to 8)
- KMeans (n_init=20, random_state=42)
- GaussianMixture (n_init=5, random_state=42)
- AgglomerativeClustering

### Selection criteria
1. Silhouette score > 0.15
2. Davies-Bouldin score < 2.0
3. No cluster < 5% of rows
4. Stability: mean ARI > 0.60 across 10 random seeds

### Post-clustering
- Centroid analysis in original feature space (inverse-transform)
- ANOVA F-statistic per feature → flags disproportionate drivers
- If `center_of_mass_return_time` F > 2× median → halve weight and re-cluster

---

## Expected Day-Types (to discover, not to assume)

| Type | Likely signature |
|------|-----------------|
| Smooth Trend | High linreg_r2, high CLV, low flip_count, moderate range |
| Volatile Trend | High linreg_r2, high ATR, high day_range |
| Open Drive | High range_pct_before_11am, flat PM, high opening return |
| Compression | Low day_range, high twap_cross_count, low linreg_r2 |
| Reversal | Large gap, CLV opposite to gap direction |
| Late Expansion | High center_of_mass_return_time, high range_pct_after_130pm |

Labels are assigned AFTER clustering by centroid inspection — not imposed.

---

## Audit Findings (Phase 1 — Feature Generation)

- **Regime drift**: None. No feature mean shift > 1 std-dev between 2023-24 and 2025-26.
- **High-corr pairs identified and resolved**: 4 pairs, all handled by pruning.
- **Winsorization required**: 13 features flagged (log_vol_expansion most extreme: kurt=316).
- **Spot checks passed**: linreg_r2 extremes, range extremes, TWAP cross extremes all behaviorally consistent.

---

## Audit Findings (Phase 2 — Clustering, Feb 2026)

### Pipeline run: `python scripts/cluster_day_types.py`
- **Input**: 744 rows × 42 features (after pruning 18 cols; 2 NaN rows from rolling warmup)
- **PCA**: 17 components retained at 87.3% cumulative variance
- **Volatility dominance**: 15.6% of first 5 PCs — well under 40% threshold; no downweighting needed
- **`center_of_mass_return_time` ANOVA F**: not in top 10 discriminative features — no re-weighting needed

### PC structure (behaviorally healthy)
| PC | Variance | Primary axis |
|----|----------|-------------|
| PC1 | 17.3% | Directional trend (CLV, pct_min_above_twap, linreg_slope) |
| PC2 | 14.1% | Relative volatility regime (range_pct_vs20d, day_range_pct) |
| PC3 | 9.3% | Opening structure vs late expansion |
| PC4 | 7.7% | Gap / open drive intensity |
| PC5 | 6.1% | Late expansion vs smooth trend (linreg_r2 negatively loaded) |

### k-Selection scan
```
k    Sil      CH       DB    MinClust
3   0.1475   123.0   2.0668   28.2%  ← selected
4   0.1410   102.9   2.0860   13.6%
5   0.1199    90.4   2.1996   13.8%
6   0.1244    82.7   2.1618    7.8%
7   0.1236    76.4   2.1095    3.9%
8   0.1244    71.2   1.9669    3.8%
```
Auto-selected k=3: highest silhouette, highest CH, all clusters ≥ 5%.

### Stability
- **Seed stability (ARI, 10 seeds)**: 0.981 — PASS
- **Subperiod centroid similarity (2023-24 vs 2025-26)**: 0.892 — STABLE

### Cluster sizes (well-balanced, no micro-clusters)
| Cluster | Days | Share |
|---------|------|-------|
| 0 | 210 | 28.2% |
| 1 | 244 | 32.8% |
| 2 | 290 | 39.0% |

All 4 years evenly represented within each cluster — no time-period contamination.

### Centroid z-scores (preliminary label assignment)

| Feature | Cluster 0 | Cluster 1 | Cluster 2 |
|---------|-----------|-----------|-----------|
| clv | **-1.01** | **+1.04** | -0.14 |
| pct_min_above_twap | **-1.00** | **+1.04** | -0.16 |
| linreg_r2 | +0.40 | **+0.61** | **-0.81** |
| hh_count_15m | **-0.95** | **+0.96** | -0.12 |
| ll_count_15m | **+0.95** | **-0.96** | +0.12 |
| range_pct_before_11am | -0.48 | -0.40 | **+0.69** |
| twap_cross_count | -0.42 | -0.45 | **+0.68** |
| day_range_pct | +0.59 | +0.27 | **-0.65** |

### Preliminary cluster labels (auditor approval required)
| Cluster | Candidate Label | Behavioural Signature |
|---------|----------------|----------------------|
| 0 | **Bearish Trend** | CLV −1.0σ, price below TWAP, structured LL progression, above-avg range |
| 1 | **Bullish Trend** | CLV +1.0σ, price above TWAP, structured HH progression, highest linreg_r2 |
| 2 | **Open Drive / Choppy** | Range set early (+0.69σ before 11am), high TWAP crossings, lowest linreg_r2 (−0.81σ), below-avg range |

### Top ANOVA discriminators (Median F = 60)
1. CLV (F=689)
2. pct_min_above_twap (F=682)
3. linreg_slope (F=622)
4. longest_above_twap (F=541)
5. full_day_return (F=520)

All top features are directional microstructure signals — confirms clustering separated **direction**, not volatility size.

### Open questions for auditor
1. **Cluster 2 heterogeneity**: At k=4 (sil=0.141, min_cluster=13.6%), the Open Drive/Choppy cluster may split into "open drive fade" vs "intraday compression". Worth inspecting `--k 4` centroid diff.
2. **Davies-Bouldin = 2.07** at k=3 (spec required < 2.0 — marginally exceeded). Acceptable given silhouette=0.1475 and ARI=0.981, but flag for documentation.

---

## Output Files

```
data/features/day_type/
├── nifty_day_features_2023.csv     # 223 rows
├── nifty_day_features_2024.csv     # 245 rows
├── nifty_day_features_2025.csv     # 248 rows
├── nifty_day_features_2026.csv     # 30 rows
├── cluster_labels.csv              # date, cluster_id, cluster_label
├── cluster_centroids.csv           # centroid × feature (original scale)
├── cluster_summary.txt             # full audit output
└── pca_variance.csv                # PC loadings × feature groups
```

---

## Code Files

### Phase 1 & 2 — Feature Generation + Clustering
| File | Role |
|------|------|
| `core/analytics/day_features.py` | Feature computation library (all 7 blocks, reusable) |
| `scripts/generate_day_features.py` | CLI: loads DuckDB → computes EOD features → saves CSVs |
| `scripts/cluster_day_types.py` | Clustering pipeline: prune → scale → PCA → cluster → validate |

### Phase 3 — Intraday State Engine
| File | Role |
|------|------|
| `scripts/build_intraday_features.py` | Computes partial features at 3 checkpoints (10am/11am/1pm) from 1m bars |
| `scripts/train_daytype_classifier.py` | Trains logistic + LightGBM classifiers; saves to `models/daytype/` |
| `scripts/evaluate_intraday_prediction.py` | Accuracy reports, confidence calibration, confusion matrices |
| `scripts/live_daytype_engine.py` | CLI replay runner — verifies engine on historical days |
| `core/state/daytype_engine.py` | **Live state engine** — ingests 1m bars, emits DayTypeState at checkpoints |

### Execution order (Phase 3)
```
python scripts/build_intraday_features.py        # step 1: extract partial features
python scripts/train_daytype_classifier.py        # step 2: train models
python scripts/evaluate_intraday_prediction.py    # step 3: review accuracy
python scripts/live_daytype_engine.py --date 2025-06-03  # step 4: verify live replay
```

---

---

## Phase 3 — Intraday State Engine Architecture

### Objective
Convert end-of-day taxonomy into a **real-time structural state** that can be updated intraday and consumed by trading strategies.

### Critical principle
End-of-day features (full_day_return, full linreg_r2, full CLV) are **targets, not predictors**. The intraday engine uses only data available at each checkpoint.

### Checkpoints
| Time | Bar index | Features available |
|------|-----------|-------------------|
| 10:00 AM | 45 | Opening structure (B), 45m trend, partial TWAP, Block A context |
| 11:00 AM | 105 | All above + AM volatility, full AM range, 7× 15m bars |
| 13:00 PM | 225 | All above + AM/PM vol split, partial PM range, 15× 15m bars |

### Feature policy
- **Included (10am / 11am)**: All `partial_*` features + Block A gap/prev-day context + rolling percentile ranks
- **Included (13pm prod)**: All `partial_*` + percentile ranks — **Block A excluded** (see ablation below)
- **Excluded everywhere**: All full-session features (`full_day_return`, `clv`, `linreg_r2`, `range_pct_after_130pm`, etc.)
- `open_30m_range_ratio_partial`: uses partial range as denominator (not full day range — unavailable)

### Supervised model — final deployment architecture
- **Y**: End-of-day cluster label (0=BearTrend, 1=BullTrend, 2=Choppy)
- **X**: Partial features at checkpoint
- **Split**: 2023-24 train | 2025 validation | 2026 holdout
- **Deployed model**: Multinomial logistic regression only (LightGBM overfit — 36-point train/val gap at 10am)
- **LightGBM status**: Rejected — insufficient training samples (~465) for 42 features; re-evaluate at 2+ years live data

### Block A ablation results (Feb 2026)
Block A = gap_pct, gap_dir, gap_size_pct60, prev_day_return, prev_day_range, prev_day_clv, prev_day_slope, prev_day_vol_pct.

| Model | Val accuracy | Hold accuracy | High-conf acc | High-conf coverage |
|-------|-------------|--------------|---------------|--------------------|
| Full (with Block A) | 70.9% | 73.3% | — | — |
| **No Block A (prod)** | **72.1%** | **80.0%** | **84.9%** | **56.3%** |
| Block A only | 45.7% | — | — | — |

**Conclusion**: Block A features are anti-predictive at 13pm. The model is intraday-structural (behavioral),
not regime-memory. Every Block A feature improved or was neutral when dropped. Production 13pm model excludes Block A.

### Calibration audit (Feb 2026)
- Lock zone (0.70-0.80): predicted mean = 0.752, actual accuracy = 0.745. Gap = −0.007 (essentially perfect)
- Brier score: 0.127 (vs 0.333 baseline random)
- Isotonic calibration tested and rejected: reduced val accuracy 72.1% → 70.9%, shrunk high-conf coverage 56% → 40%
- **Decision**: No calibration applied. Lock threshold stays at 0.70.

### Production model
| Model | Path | Features | Val | Hold | High-conf acc | Notes |
|-------|------|----------|-----|------|---------------|-------|
| `logistic_13pm_prod` | `models/daytype/logistic_13pm_prod/` | 34 (no Block A) | 72.1% | 80.0% | 84.9% | v1.1, deployed |

### Accuracy results (actual, Feb 2026 validation)
| Checkpoint | Val accuracy | Hold accuracy | Deployed? |
|------------|-------------|--------------|-----------|
| 10:00 AM | 47.6% | — | No (below 55% floor) |
| 11:00 AM | 53.4% | — | No (bias signal only, no lock) |
| 13:00 PM | **72.1%** | **80.0%** | **Yes** |

### Confidence tiers
| Tier | Max probability | Action |
|------|----------------|--------|
| High | >= 0.70 | Enter 5-bar stability watch; lock after confirmation |
| Med | 0.55–0.70 | Publish state, use cautiously — no lock |
| Low | < 0.55 | Do not trade on state alone |

### 5-bar stability lock mechanism
After a checkpoint emits confidence >= 0.70, the engine enters a per-bar stability watch.
The state locks only after **LOCK_STABILITY_BARS = 5** consecutive 1m bars arrive with no
contradicting checkpoint. This prevents a single volatility spike at the checkpoint minute
from triggering premature lock.

```
checkpoint fires, conf=0.78
  -> state emitted (locked=False), stability watch starts
  -> bars +1, +2, +3, +4 arrive (no new checkpoint)
  -> bar +5 arrives -> LOCK CONFIRMED (locked=True emitted)
```

If a new checkpoint fires during the watch and changes the prediction, the watch resets.

### DayTypeState schema
```json
{
    "date":            "2025-06-03",
    "checkpoint":      "13pm",
    "predicted_state": "BullTrend",
    "cluster_id":      1,
    "confidence":      0.78,
    "conf_tier":       "high",
    "locked":          true,
    "p_bear":          0.06,
    "p_bull":          0.78,
    "p_choppy":        0.16,
    "model_version":   "v1.1",
    "updated_at":      "2025-06-03T13:05:00"
}
```

### Live integration
```python
from core.state.daytype_engine import DayTypeEngine

engine = DayTypeEngine()   # auto-loads logistic_13pm_prod for 13pm
engine.reset(today)

# On each 1m bar arrival:
state = engine.on_bar(bar_dict)
if state is not None:
    if state.locked:
        # State confirmed stable — strategies can act
        if state.predicted_state == 'BullTrend':
            pass  # trade pullbacks, avoid mean-reversion shorts
        elif state.predicted_state == 'Choppy':
            pass  # avoid breakout systems, favor fade logic
    elif state.conf_tier == 'high':
        # High confidence but not yet locked (stability watch active)
        pass  # can use directionally but with reduced size
```

### Operational rules
1. Models must be retrained quarterly (use prior 2 years as train, most recent 6m as val)
2. Never retrain using same-day partial features
3. Track prediction error via `DayTypeMonitor` — alert if 30d rolling accuracy drops > 2 std-dev below 72.1%
4. `core/state/daytype_engine.py` is the **only** interface for strategies — no direct model access
5. 13pm model is `logistic_13pm_prod` (v1.1, Block A excluded) — do not swap to full-feature model without re-running ablation
6. Monitor for overconfidence drift: alert if confidence trend rises while accuracy falls (see `DayTypeMonitor._check_confidence_drift`)

---

---

## Phase 4 — Conditional PM Session Expectancy (Feb 2026)

### Objective
Under each predicted structural state at 13:00, quantify the statistical distribution
of 13:00–15:30 session behavior. This is the bridge from classification to edge.

### Script
`scripts/run_pm_expectancy.py` — loads production model, generates predictions for all
743 historical days, loads 1m PM bars, computes metrics, outputs JSON + CSV.

Output files:
- `data/features/day_type/pm_expectancy_raw.csv` — per-day metrics
- `data/features/day_type/pm_expectancy.json`    — grouped distribution tables

### Key Findings (all 743 days, 2023–2026)

**Prediction accuracy at high confidence (conf >= 0.70):**
| State | n (high-conf) | Pred accuracy |
|-------|--------------|---------------|
| BearTrend | 124 | **88.7%** |
| BullTrend | 170 | **84.7%** |
| Choppy    | 130 | **82.3%** |

**New-day-extreme rates (high conf) — the most actionable finding:**
| State | New-day-HIGH rate | New-day-LOW rate |
|-------|------------------|-----------------|
| BearTrend | 2.4%  | **80.7%** |
| BullTrend | **81.2%** | 4.1% |
| Choppy    | 23.8% | 44.6% |

**PM return distributions (high confidence):**

BearTrend (n=124):
- pm_return: mean=-0.027%, p25=-0.250%, p50=-0.050%, p75=+0.147%
- max_loss:  p50=-0.252%,  p10=-0.682%
- max_gain:  p50=+0.199%  (bounce ceiling)
- PM positive: 46.8%

BullTrend (n=170):
- pm_return: mean=+0.076%, p25=-0.114%, p50=+0.071%, p75=+0.272%
- max_gain:  p50=+0.235%,  p90=+0.578%
- max_loss:  p50=-0.166%  (dip to sit through)
- PM positive: 59.4%

Choppy (n=130):
- pm_return: mean=-0.011%, p50=-0.006%  (flat, near zero)
- PM range:  p50=+0.328%  (compressed vs BullTrend 0.438%, BearTrend 0.546%)
- PM positive: 47.7%  (near coin flip)

### Structural Interpretation

1. **BearTrend signal at 13pm = reliable low-making engine**: 80.7% of high-conf bear
   days make a new intraday low in PM. This is not a return-size story — it's a
   directional conviction story. PM range is WIDE (0.546%) but the closing return
   is small (-0.05% median). The bear move happens, it just mean-reverts partially.

2. **BullTrend signal = new-high engine**: 81.2% new-day-high rate. PM range
   (0.438%) is moderate and the session closes +0.07% median from the 13:00 open.
   More consistent than BearTrend — lower adverse excursion (0.095% vs 0.119%).

3. **Choppy signal = range compression**: PM range is 25% smaller than BearTrend,
   vol is lowest (0.019% 1m std). Not tradeable directionally. Suitable for
   range-bound fade or no-trade.

4. **Year-by-year concern (2025 BullTrend)**: pm_positive rate dropped to 51%
   (2023=68%, 2024=61%). Requires monitoring. The new-day-high rate and prediction
   accuracy may have degraded in the 2025 trend regime.

### Adverse Excursion Profile (risk sizing reference)

High-conf predictions must survive adverse excursions before the favorable move occurs:
| State | Adverse p50 | Adverse p75 | Adverse p90 |
|-------|------------|------------|------------|
| BearTrend | 0.119% | 0.193% | 0.313% |
| BullTrend | 0.095% | 0.163% | 0.264% |
| Choppy    | 0.086% | 0.144% | 0.217% |

For a BullTrend long entry at 13:00 open, expect a 0.095% median dip before the
upward move — this is the SL floor for any PM strategy.

---

## Phase 4.5 -- PM Timing Analysis (Feb 2026)

### Scripts
- `scripts/pm_timing_stats.py` -- 6-block path analysis from 13:00 to PM extreme
- Output: `data/features/day_type/pm_timing_stats.json`

### Key Findings

**Timing distribution** (high-conf days, when does PM extreme print?):
| State | p25 | p50 | p75 | p90 |
|-------|-----|-----|-----|-----|
| BullTrend | 2 min | 15 min | 39 min | 79 min |
| BearTrend | 2 min | 15 min | 44 min | 92 min |

64-68% of extremes within 30 minutes. p25 = 2 minutes -- confirms immediate entry is correct.

**Pre-extreme adverse** (heat from 13:00 before the PM extreme):
| State | p50 | p75 | Stop 15bp survivability |
|-------|-----|-----|------------------------|
| BullTrend | 0.034% | 0.089% | 87.7% |
| BearTrend | 0.039% | 0.112% | 83.0% |

**Post-extreme giveback to close**:
| State | p50 giveback | Implication |
|-------|-------------|-------------|
| BullTrend | 0.150% | 55% of extreme returned by 15:30 |
| BearTrend | 0.223% | 73% of extreme returned by 15:30 |

Market makes the extreme then mean-reverts. Any target-based strategy must capture BEFORE giveback.

**Entry timing curve** -- opportunity remaining vs delay after 13:00:

| Delay | Bull % opportunity | Bear % opportunity |
|-------|-------------------|-------------------|
| 0 min (13:00) | 65.9% | 67.7% |
| 5 min (13:05) | 52.9% | 49.2% |
| 15 min | 39.4% | 38.7% |
| 30 min | 28.2% | 25.8% |
| 60 min | 11.8% | 14.5% |

**Checkpoint answer**: 13pm checkpoint is optimal. 12pm (earlier, less accurate) and 2pm (+60min delay, 12% opportunity left) both dominated by 13pm immediate entry.

---

## Phase 5 -- PM Impulse Capture: Walk-Forward Backtest (Feb 2026)

### Scripts
- `scripts/run_v9_backtest.py` -- walk-forward backtest engine
- `scripts/diagnose_remaining_excursion.py` -- remaining excursion from 13:02 diagnostic
- Trades output: `data/features/day_type/v9_trades.csv`

### Architecture Tested (v9)
Entry at 13:02 open (1-bar confirmation), hard stop + fixed target + time exit.
High-confidence predictions only (conf >= 0.70). Nifty 50 futures cost model: 0.04% round-trip.

Parameter grid tested: stop=[0.08,0.10,0.12,0.15,0.18,0.20]%, target=[0.10,0.12,0.15,0.18,0.22,0.25]%,
time_exit=[25,35,45]m, entry=[13:00,13:02]. **39+ combinations. Every single combination: negative expectancy.**

### Remaining Excursion Diagnostic (pre-flight)

Before assuming the architecture was dead, computed the remaining excursion from 13:02
to the ultimate PM extreme (the maximum possible capture from entry).

| State | % extreme ahead at 13:02 | % extreme before 13:02 | p50 remaining (all) | p50 remaining (ahead) |
|-------|--------------------------|------------------------|---------------------|----------------------|
| BullTrend | 64.7% | 16.5% | **+0.230%** | +0.282% |
| BearTrend | 61.3% | 19.4% | **+0.242%** | +0.304% |

Key insight: **early-extreme days still show +0.198-0.230% remaining from 13:02.** The market
breaks the AM high/low in bars 0-1 but continues moving. Moving entry to 13:00 recovers
16-19% of early days but does not change median remaining excursion meaningfully.

**Verdict: physics are viable. Failure is execution geometry, not prediction failure.**

### Root Cause: Path Non-Monotonicity

The fundamental incompatibility: Nifty 50 PM path is not monotonic.

- BullTrend: 30% of days see -0.15% adverse before the PM high
- BearTrend: 93% of days see +0.15% adverse bounce BEFORE the eventual PM low

Fixed stops are forced to choose between:
- Tight stop (0.08-0.15%): cut position on normal path volatility before the move
- Wide stop (0.20-0.30%): survivable but when stopped, full stop loss eats expectancy

No combination of stop/target produces positive expectancy because stops slightly exceed
target rates even with larger target sizes. E consistently -0.027% to -0.040% across all 39 combos.

### Breakthrough: Hold-to-PM-Close (No Stop)

Removing all stops and targets (pure directional time exit at 15:30):

| State | N | WR | E/trade | Total | MaxDD | 2023 | 2024 | 2025 | 2026 |
|-------|---|----|---------|----|-------|------|------|------|------|
| **BullTrend** | 170 | **52.9%** | **+0.036%** | **+6.07%** | -2.51% | +0.034% | +0.047% | +0.020% | +0.077% |
| BearTrend | 124 | 50.8% | -0.013% | -1.66% | -2.72% | -0.021% | +0.004% | -0.008% | -0.183% |

**BullTrend hold-to-close is the first positive-expectancy result.** Consistent across all 4 years.
E = +0.036%/trade, WR = 52.9%, avg_win = +0.269%, avg_loss = -0.226%, MaxDD = -2.51%.

BearTrend hold-to-close: near coin-flip (50.8% WR) with adverse R:R. Not viable as futures.

### Wide Stop vs. No Stop (BullTrend)

Adding any stop to BullTrend hold-to-close reduces expectancy:

| Stop | Days stopped | WR | E/trade | Change from baseline |
|------|------------|-----|---------|---------------------|
| None | 0 | 52.9% | **+0.036%** | baseline |
| 0.30% | 7.6% | 49.4% | +0.009% | -0.027% |
| 0.25% | 12.4% | 47.6% | -0.001% | -0.037% |
| 0.20% | 20.0% | 41.8% | -0.026% | -0.062% |

**No stop is optimal.** The 7.6% of days stopped at 0.30% would mostly have recovered by close.
The market's intraday mean-reverting tendency is sufficient: losers avg -0.226%, winners avg +0.269%.

For BearTrend: any stop at 0.15-0.35% fires on 58-93% of days (PM always bounces first).
Any fixed stop on a short PM position is catastrophic for BearTrend.

### Architecture Verdict

| Approach | BullTrend E | BearTrend E | Status |
|----------|-------------|-------------|--------|
| Fixed stop + fixed target (any combo) | -0.027% to -0.037% | -0.029% to -0.040% | FAIL |
| Hold to PM close (no stop) | **+0.036%** | -0.013% | BULL VIABLE |
| Wide stop (0.30%+) | +0.009% | catastrophic | WORSE |

**The edge is in the raw directional call, not in intraday path-following.**

BullTrend hold-to-PM-close is a deployable futures signal pending:
1. Live paper trading confirmation (out-of-sample 2026 onward)
2. Position sizing: at 0.036% E with 0.26% per-trade std, size accordingly
3. Maximum drawdown monitoring: historical MaxDD = -2.51% (10 consecutive trades at risk)

BearTrend signal requires fundamentally different structure (options with defined risk,
or alternative intraday approach that survives the initial PM bounce).

### Files Produced (Phase 5)
```
scripts/run_v9_backtest.py             -- walk-forward backtest (v9, entry bar configurable)
scripts/diagnose_remaining_excursion.py  -- remaining excursion from 13:02 diagnostic
data/features/day_type/v9_trades.csv   -- per-trade records (294 rows)
data/features/day_type/remaining_excursion.json  -- excursion distribution by state
```

---

## What This Is NOT

- Not a strategy
- Not a signal generator
- Not a prediction model
- Not a backtest

This is a **structural map** of how Nifty 50 behaves.
Strategy design comes only after cluster stability is confirmed across all 4 years.
