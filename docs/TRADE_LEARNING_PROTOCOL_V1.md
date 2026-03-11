# 🧠 Trade Learning Protocol V1 (TLP V1)

**Status:** Implementation Complete  
**Version:** 1.0.0 (TLP_V1_CORE)  
**Universe:** NIFTY_UNIVERSE_V1

---

## 1. Overview
TLP V1 transforms PixityAI from a strategy executor into a high-fidelity **Research Instrument**. Every trade is now captured with its full structural context at the exact moment of execution, allowing for deep statistical analysis of where alpha concentrates.

## 2. Structural Truth Captured
Every trade recorded in `trading.db` now has a corresponding 1:1 entry in the `trade_context` table, capturing:

### A) Market Context (The "Where")
*   **Regime State**: Finalized HMM state from the previous session (Expansion/Shock/Contraction).
*   **Session Type**: AM (Entry < 12:30 IST) vs PM (Entry >= 12:30 IST).
*   **Dispersion**: Raw CSAD value and frozen 60-day rolling percentile.
*   **Volatility**: Raw ATR value and frozen 60-day rolling percentile.
*   **Breadth**: Universe Adv/Dec ratio at the time of entry.

### B) Signal Quality (The "Why")
*   **Signal Rank**: Relative strength of the signal within the universe today.
*   **Signal Percentile**: Normalized confidence score.
*   **Standardized Risk**: Mandatory `sl_distance` and `risk_r` defined at entry.

### C) Execution Quality (The "How")
*   **Intended Entry**: The price at which the signal fired (model target).
*   **Actual Entry**: The fill price received from the broker.
*   **Slippage**: Precise calculation in basis points (bps).

### D) Outcome Diagnostics (The "Result")
*   **MAE (Max Adverse Excursion)**: Maximum heat taken during the trade (Points & R).
*   **MFE (Max Favorable Excursion)**: Maximum profit seen during the trade (Points & R).
*   **Exit Efficiency**: Ratio of realized profit to maximum potential profit.
*   **Holding Time**: Precise duration from entry to exit bars.

---

## 3. Core Components

| Component | File | Responsibility |
| :--- | :--- | :--- |
| **CaptureEngine** | `core/analytics/capture.py` | Snapshots structural state at signal generation. |
| **MetricsService** | `core/analytics/metrics_service.py` | Maintains historical buffers for frozen percentiles. |
| **DiagnosticsEngine** | `core/analytics/diagnostic_engine.py` | Computes MAE/MFE using high-res 1m bars. |
| **ExecutionHandler** | `core/execution/handler.py` | Enforces risk and performs atomic context persistence. |

---

## 4. Enforcement Rules
1.  **Mandatory Risk**: Any signal without `sl_distance` and `risk_r` in metadata is **REJECTED** immediately.
2.  **Temporal Atomicity**: Context is captured at signal-time and saved at fill-time in a single transaction.
3.  **Frozen Research**: All percentiles and labels are "frozen" at capture. Changes to calculation logic require a version bump to `TLP_V2_CORE`.

## 5. How to Run Structural Review
Use the updated review script to see expectancy heatmaps across regimes and sessions:

```bash
python scripts/perform_structural_review.py --min-trades 30
```

This will output:
*   Expectancy by Regime & Session.
*   Signal Strength Decay curve.
*   MAE/MFE Efficiency Diagnostics.

---

*This protocol ensures that every loss is a paid lesson and every win is a reproducible fact.*
