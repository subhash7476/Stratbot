# 🧪 Cross-Sectional Dispersion Research: Implementation Plan

## 1. Objective
To test whether 11:00 AM cross-sectional dispersion among NIFTY 50 constituents produces monetizable continuation between 11:01 AM and 15:00 PM, conditioned by the existing day-type regime model.

## 2. Infrastructure Requirements
Reusing existing institutional-grade modules to avoid duplication and leakage:
- **Data**: DuckDB minute-level NIFTY data (2023–2026).
- **Regime**: `DayTypeEngine` and `daytype_live_log.csv`.
- **Normalization**: `core/analytics/day_features._rolling_pct_rank`.
- **Cost Model**: 0.04% round-trip (conservative intraday basket).
- **Harness**: Year-by-year walk-forward protocol.

## 3. Critical Constraints
- **Beta**: Uses T-20 to T-1 daily data only (no same-day leakage).
- **Entry**: 11:01 AM Open (calculated from 11:00 AM snapshot).
- **No Stop/TP**: Pure structural hold until 15:00 PM.
- **No Optimization**: Fixed Top/Bottom 5 stocks (decile).

## 4. Components

### A. Core Engine (`core/analytics/dispersion.py`)
- `DispersionEngine` class.
- `get_snapshot_signals()`: 
    - Compute Open-to-11:00 returns.
    - Calculate Residuals = Stock_Ret - (Beta * Index_Ret).
    - CSAD/CSSD diagnostic metrics.
- `simulate_hold()`: 
    - 11:01 Open to 15:00 Close.
    - Equal-weighted legs, dollar-neutral.

### B. Research Harness (`scripts/research_dispersion.py`)
- Walk-forward simulation loop (2023–2026).
- Slices history for beta calculation per-day.
- Joins results with existing regime labels (`BullTrend`, `BearTrend`, `Choppy`).
- Produces performance table (E/trade, Sharpe, DD).

## 5. Execution Steps
1. **Develop Engine**: Implement `DispersionEngine` with vectorized ranking.
2. **Build Harness**: Implement walk-forward and regime integration.
3. **Run Validation**: Execute `python scripts/research_dispersion.py`.
4. **Analyze Results**: Review regime-conditional edge and subperiod stability.

## 6. Target Deliverables
- `data/features/dispersion_results.csv`: Detailed trade-by-trade log.
- Console Summary: Performance by regime and dispersion correlation.

**Status: READY FOR BUILD**
