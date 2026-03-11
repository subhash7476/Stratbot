"""
Live Day-Type State Engine
===========================
Maintains real-time structural state for the current trading day.

Architecture:
  - Fed 1m bars as they arrive (or via replay)
  - At each checkpoint minute (10am / 11am / 1pm), runs the classifier
  - Emits a DayTypeState object with predicted cluster + confidence
  - Once confidence >= lock threshold, state is LOCKED (no further updates)
  - Strategies consume state via get_state() — no direct classifier access

State object:
  {
      "date":            "YYYY-MM-DD",
      "checkpoint":      "11am",
      "predicted_state": "BullTrend",
      "cluster_id":      1,
      "confidence":      0.74,
      "conf_tier":       "high",
      "locked":          True,
      "p_bear":          0.08,
      "p_bull":          0.74,
      "p_choppy":        0.18,
      "model_version":   "v1.0",
      "updated_at":      "2025-06-03T11:00:00",
  }

Usage (live):
  engine = DayTypeEngine()
  engine.on_bar(bar_dict)   # call each 1m bar
  state = engine.get_state()

Usage (replay):
  engine = DayTypeEngine(date=date(2025, 6, 3))
  for bar in bars:
      engine.on_bar(bar)
  state = engine.get_state()

Usage (from 1m DataFrame):
  engine = DayTypeEngine()
  engine.replay_session(df_1m)
  state = engine.get_state()
"""

from __future__ import annotations

import json
import logging
import math
import pickle
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_DIR   = ROOT / "models" / "daytype"
FEATURE_DIR = ROOT / "data" / "features" / "day_type"

logger = logging.getLogger(__name__)

# ── Checkpoint definitions ─────────────────────────────────────────────────────
# Bar count thresholds assume continuous 1m bars from 9:15 open.
# These are kept as reference but NOT used for triggering — wall-clock time
# is the primary trigger (robust to WebSocket gaps / late starts).
CHECKPOINT_BARS = {
    '10am': 45,
    '11am': 105,
    '13pm': 225,
}

# Wall-clock IST times for each checkpoint (primary trigger)
CHECKPOINT_WALL_TIMES = {
    '10am': time(10, 0),
    '11am': time(11, 0),
    '13pm': time(13, 0),
}

# Minimum bars required — lowered to tolerate ~50% WebSocket coverage.
# These are floor values: checkpoint fires at wall-clock time IF bar count >= min.
MIN_BARS_REQUIRED = {
    '10am': 20,
    '11am': 50,
    '13pm': 100,
}

CLUSTER_NAMES = {0: 'BearTrend', 1: 'BullTrend', 2: 'Choppy'}
CONF_HIGH = 0.70
CONF_MED  = 0.55

# Lock state when confidence reaches this level
LOCK_THRESHOLD = 0.70

# 5-bar stability: confidence must stay >= threshold for this many consecutive
# bars before lock fires. Prevents single-bar volatility spike from locking.
LOCK_STABILITY_BARS = 5

# Production model override for 13pm checkpoint (Block A excluded, v1.1)
# Maps checkpoint name -> model directory name
PROD_MODEL_OVERRIDES = {
    '13pm': 'logistic_13pm_prod',
}

# Block A features excluded from 13pm production model
BLOCK_A_COLS = {
    'gap_pct', 'gap_dir', 'gap_size_pct60',
    'prev_day_return', 'prev_day_range', 'prev_day_clv',
    'prev_day_slope', 'prev_day_vol_pct',
}

EPSILON = 1e-8


@dataclass
class DayTypeState:
    """Immutable snapshot of the current day-type prediction."""
    date:            str
    checkpoint:      str              # '10am' / '11am' / '13pm' / 'pending'
    predicted_state: str              # 'BullTrend' / 'BearTrend' / 'Choppy' / 'Unknown'
    cluster_id:      int              # 0 / 1 / 2 / -1 (unknown)
    confidence:      float            # max class probability
    conf_tier:       str              # 'high' / 'med' / 'low' / 'none'
    locked:          bool             # True = no further updates
    p_bear:          float = 0.0
    p_bull:          float = 0.0
    p_choppy:        float = 0.0
    model_version:   str   = 'v1.0'
    updated_at:      str   = ''

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def pending(cls, d: date) -> 'DayTypeState':
        return cls(
            date=str(d), checkpoint='pending', predicted_state='Unknown',
            cluster_id=-1, confidence=0.0, conf_tier='none', locked=False,
        )


class DayTypeEngine:
    """
    Real-time day-type state engine.
    Maintains a rolling 1m bar buffer, runs classifier at checkpoints,
    emits DayTypeState objects.
    """

    def __init__(
        self,
        model_name: str = 'logistic',
        lock_threshold: float = LOCK_THRESHOLD,
        eod_feature_path: Optional[Path] = None,
    ):
        self.model_name     = model_name
        self.lock_threshold = lock_threshold
        self._models: dict  = {}    # {cp: (model, scaler, meta)}
        self._bars: list    = []    # accumulated 1m Nifty bar dicts
        self._bn_bars: list = []    # accumulated 1m BankNifty bar dicts (Block H)
        self._state: Optional[DayTypeState] = None
        self._session_date: Optional[date]  = None
        self._checkpoints_run: set = set()
        self._lock_streak: int = 0    # consecutive bars above lock threshold (stability counter)
        self._pending_lock_state: Optional[DayTypeState] = None  # state awaiting stability confirmation

        # Load EOD feature history for Block A rolling percentile context
        self._eod_df: Optional[pd.DataFrame] = None
        self._load_eod_context(eod_feature_path)

        self._load_models()

    # ── Model loading ──────────────────────────────────────────────────────────

    def _load_models(self) -> None:
        for cp in CHECKPOINT_BARS:
            # Use production override if defined (e.g. logistic_13pm_prod for 13pm)
            dir_name = PROD_MODEL_OVERRIDES.get(cp, f"{self.model_name}_{cp}")
            base = MODEL_DIR / dir_name
            if not (base / "model.pkl").exists():
                logger.warning(f"Model not found for {dir_name} @ {cp}. "
                               f"Run train_daytype_classifier.py first.")
                continue
            with open(base / "model.pkl", 'rb') as f:
                model = pickle.load(f)
            with open(base / "scaler.pkl", 'rb') as f:
                scaler = pickle.load(f)
            with open(base / "metadata.json", 'r') as f:
                meta = json.load(f)
            self._models[cp] = (model, scaler, meta)
            logger.info(f"Loaded model '{dir_name}' for checkpoint {cp} "
                        f"(block_a_excluded={meta.get('block_a_excluded', False)})")

        if not self._models:
            raise RuntimeError(
                f"No models found in {MODEL_DIR}. "
                f"Run: python scripts/train_daytype_classifier.py"
            )

    def _load_eod_context(self, path: Optional[Path] = None) -> None:
        """Load historical EOD features for Block A rolling percentiles."""
        paths_to_try = [path] if path else [
            FEATURE_DIR / f"nifty_day_features_{yr}.csv"
            for yr in [2023, 2024, 2025, 2026]
        ]
        dfs = []
        for p in paths_to_try:
            if p and p.exists():
                dfs.append(pd.read_csv(p, index_col='date', parse_dates=True))
        if dfs:
            self._eod_df = pd.concat(dfs).sort_index()
            logger.info(f"Loaded EOD context: {len(self._eod_df)} days")

    # ── Bar ingestion ──────────────────────────────────────────────────────────

    def reset(self, session_date: date) -> None:
        """Reset engine for a new trading day."""
        self._bars = []
        self._bn_bars = []
        self._session_date = session_date
        self._state = DayTypeState.pending(session_date)
        self._checkpoints_run = set()
        self._lock_streak = 0
        self._pending_lock_state = None
        logger.info(f"DayTypeEngine reset for {session_date}")

    def on_bar(self, bar: dict) -> Optional[DayTypeState]:
        """
        Ingest one 1m bar. Bar must have keys:
          timestamp, open, high, low, close, volume
        Returns updated DayTypeState if a checkpoint was triggered or lock confirmed, else None.
        """
        if self._state and self._state.locked:
            return None   # state locked — no further processing

        self._bars.append(bar)
        n_bars = len(self._bars)

        # Infer session date from first bar
        if self._session_date is None:
            ts = bar.get('timestamp')
            if isinstance(ts, (datetime, pd.Timestamp)):
                self._session_date = ts.date()
            self._state = DayTypeState.pending(self._session_date)

        # ── Stability countdown after high-confidence checkpoint ───────────────
        # After a checkpoint emits confidence >= lock_threshold, we run a
        # per-bar stability watch: the state only locks once LOCK_STABILITY_BARS
        # consecutive bars have arrived without a contradicting checkpoint.
        # This prevents a single volatility spike at the checkpoint minute from
        # locking into a premature state.
        if self._pending_lock_state is not None:
            self._lock_streak += 1
            if self._lock_streak >= LOCK_STABILITY_BARS:
                # Stability confirmed — emit locked state
                import dataclasses
                locked_state = dataclasses.replace(self._pending_lock_state, locked=True)
                self._state = locked_state
                self._pending_lock_state = None
                logger.info(
                    f"[LOCK CONFIRMED after {LOCK_STABILITY_BARS} bars] "
                    f"{locked_state.predicted_state} conf={locked_state.confidence:.0%}"
                )
                return locked_state

        # ── Checkpoint evaluation ──────────────────────────────────────────────
        # Primary trigger: wall-clock time from bar timestamp (robust to gaps).
        # Fallback: bar count (for backtests where bars may lack timezone info).
        bar_ts = bar.get('timestamp')
        bar_time = None
        if isinstance(bar_ts, (datetime, pd.Timestamp)):
            bar_time = bar_ts.time() if hasattr(bar_ts, 'time') else None

        for cp, target_bar in CHECKPOINT_BARS.items():
            if cp in self._checkpoints_run:
                continue
            wall_time = CHECKPOINT_WALL_TIMES.get(cp)
            triggered = False
            if bar_time is not None and wall_time is not None:
                # Wall-clock trigger: bar timestamp >= checkpoint time AND minimum bars met
                triggered = bar_time >= wall_time and n_bars >= MIN_BARS_REQUIRED[cp]
            else:
                # Fallback: pure bar count (original behavior for backtests)
                triggered = n_bars >= target_bar and n_bars >= MIN_BARS_REQUIRED[cp]
            if triggered:
                new_state = self._run_checkpoint(cp)
                self._checkpoints_run.add(cp)
                if new_state is not None:
                    self._state = new_state
                    # If high-confidence: enter stability watch (do not lock yet)
                    if new_state.confidence >= self.lock_threshold:
                        self._pending_lock_state = new_state
                        self._lock_streak = 0
                        logger.info(
                            f"[{cp}] High-conf {new_state.confidence:.0%} — "
                            f"starting {LOCK_STABILITY_BARS}-bar stability watch"
                        )
                    return new_state

        return None

    def on_bn_bar(self, bar: dict) -> None:
        """
        Ingest one 1m BankNifty bar for Block H intermarket features.
        Must be called BEFORE on_bar() for the same timestamp so that BN data
        is available when the Nifty checkpoint fires.
        Bar must have keys: timestamp, open, high, low, close, volume
        """
        self._bn_bars.append(bar)

    def replay_session(self, df_1m: pd.DataFrame) -> DayTypeState:
        """
        Replay a full (or partial) 1m session DataFrame through the engine.
        Returns final state.
        """
        if df_1m.empty:
            return self._state or DayTypeState.pending(date.today())

        # Infer session date
        first_ts = df_1m['timestamp'].iloc[0]
        if isinstance(first_ts, (datetime, pd.Timestamp)):
            d = pd.Timestamp(first_ts).date()
        else:
            d = date.today()
        self.reset(d)

        for _, row in df_1m.iterrows():
            self.on_bar(row.to_dict())

        return self._state

    # ── Checkpoint logic ───────────────────────────────────────────────────────

    def _run_checkpoint(self, cp: str) -> Optional[DayTypeState]:
        if cp not in self._models:
            logger.warning(f"No model for checkpoint {cp}")
            return None

        model, scaler, meta = self._models[cp]
        feat_names = meta['feature_names']

        try:
            feats = self._compute_features(cp)
        except Exception as e:
            logger.error(f"Feature computation failed at {cp}: {e}")
            return None

        # Build feature vector in correct order
        X = np.array([feats.get(f, 0.0) for f in feat_names], dtype=float)
        X = np.nan_to_num(X, nan=0.0)
        X_scaled = scaler.transform(X.reshape(1, -1))

        proba    = model.predict_proba(X_scaled)[0]
        pred_cls = int(model.predict(X_scaled)[0])
        confidence = float(proba.max())
        conf_tier  = 'high' if confidence >= CONF_HIGH else ('med' if confidence >= CONF_MED else 'low')

        # locked=False here — locking is deferred to on_bar() stability watch.
        # A state becomes locked only after LOCK_STABILITY_BARS consecutive bars
        # of sustained high confidence (see on_bar logic).
        state = DayTypeState(
            date            = str(self._session_date),
            checkpoint      = cp,
            predicted_state = CLUSTER_NAMES.get(pred_cls, 'Unknown'),
            cluster_id      = pred_cls,
            confidence      = round(confidence, 4),
            conf_tier       = conf_tier,
            locked          = False,
            p_bear          = round(float(proba[0]), 4),
            p_bull          = round(float(proba[1]), 4),
            p_choppy        = round(float(proba[2]), 4),
            model_version   = meta.get('version', 'v1.0'),
            updated_at      = datetime.now().isoformat(timespec='seconds'),
        )
        logger.info(
            f"[{cp}] {state.predicted_state} conf={confidence:.0%} "
            f"tier={conf_tier}"
        )
        return state

    def _compute_block_h(self, target_bar: int, df_nf: pd.DataFrame) -> dict:
        """
        Block H: BankNifty/Nifty intermarket features computed from accumulated BN bars.
        Returns zero-filled dict if BN bars are insufficient (graceful degradation).
        """
        zero = {
            'bn_nf_open_5m_spread':        0.0,
            'bn_nf_open_30m_spread':       0.0,
            'bn_nf_partial_return_spread': 0.0,
            'bn_nf_correlation_5m':        0.0,
            'bn_nf_range_ratio':           1.0,
            'bn_leads_nifty':              0.0,
        }
        bn_bars = self._bn_bars[:target_bar]
        if len(bn_bars) < 5:
            return zero

        df_bn = pd.DataFrame(bn_bars)
        df_bn['timestamp'] = pd.to_datetime(df_bn['timestamp'])
        df_bn = df_bn.sort_values('timestamp').reset_index(drop=True)

        nf_open = float(df_nf['open'].iloc[0])
        bn_open = float(df_bn['open'].iloc[0])

        n = min(len(df_nf), len(df_bn))
        df_nf_s = df_nf.iloc[:n]
        df_bn_s = df_bn.iloc[:n]

        nf_5m  = (float(df_nf_s['close'].iloc[4])  - nf_open) / nf_open if n > 4  else 0.0
        bn_5m  = (float(df_bn_s['close'].iloc[4])  - bn_open) / bn_open if n > 4  else 0.0
        nf_30m = (float(df_nf_s['close'].iloc[29]) - nf_open) / nf_open if n > 29 else nf_5m
        bn_30m = (float(df_bn_s['close'].iloc[29]) - bn_open) / bn_open if n > 29 else bn_5m

        nf_partial = (float(df_nf_s['close'].iloc[-1]) - nf_open) / nf_open
        bn_partial = (float(df_bn_s['close'].iloc[-1]) - bn_open) / bn_open

        n_corr = min(n, 30)
        corr = 0.0
        if n_corr >= 5:
            nf_ret = df_nf_s['close'].iloc[:n_corr].pct_change().dropna()
            bn_ret = df_bn_s['close'].iloc[:n_corr].pct_change().dropna()
            min_len = min(len(nf_ret), len(bn_ret))
            if min_len >= 3 and nf_ret.std() > EPSILON and bn_ret.std() > EPSILON:
                corr = float(np.corrcoef(nf_ret.values[:min_len], bn_ret.values[:min_len])[0, 1])
                corr = 0.0 if np.isnan(corr) else float(np.clip(corr, -1.0, 1.0))

        nf_range = (float(df_nf_s['high'].max()) - float(df_nf_s['low'].min())) / max(nf_open, EPSILON)
        bn_range = (float(df_bn_s['high'].max()) - float(df_bn_s['low'].min())) / max(bn_open, EPSILON)

        return {
            'bn_nf_open_5m_spread':        bn_5m - nf_5m,
            'bn_nf_open_30m_spread':       bn_30m - nf_30m,
            'bn_nf_partial_return_spread': bn_partial - nf_partial,
            'bn_nf_correlation_5m':        corr,
            'bn_nf_range_ratio':           bn_range / max(nf_range, EPSILON),
            'bn_leads_nifty':              float(np.sign(bn_5m) == np.sign(nf_partial)),
        }

    def _compute_features(self, cp: str) -> dict:
        """
        Compute partial-day features from accumulated bars up to checkpoint.
        Mirrors build_intraday_features.py logic.
        """
        target_bar = CHECKPOINT_BARS[cp]
        bars_to_use = self._bars[:target_bar]

        # Build DataFrame
        df = pd.DataFrame(bars_to_use)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)

        from core.analytics.resampler import resample_ohlcv
        from core.analytics.day_features import (
            compute_session_twap, AM_END_BAR, CLV_THRESHOLD, _range_epsilon
        )

        df5m  = resample_ohlcv(df, '5m')
        df15m = resample_ohlcv(df, '15m')
        twap  = compute_session_twap(df)

        open_price    = float(df['open'].iloc[0])
        partial_high  = float(df['high'].max())
        partial_low   = float(df['low'].min())
        partial_close = float(df['close'].iloc[-1])
        partial_range = partial_high - partial_low
        range_denom   = max(partial_range, EPSILON * open_price)
        n = len(df)

        feats: dict = {}

        # Block B: opening structure
        def _close_at(bar_idx):
            return float(df['close'].iloc[bar_idx]) if n > bar_idx else float(df['close'].iloc[-1])

        feats['open_5m_ret']  = (_close_at(4)  - open_price) / open_price
        feats['open_15m_ret'] = (_close_at(14) - open_price) / open_price
        feats['open_30m_ret'] = (_close_at(29) - open_price) / open_price

        first30 = df.iloc[:30]
        open_30m_high = float(first30['high'].max())
        open_30m_low  = float(first30['low'].min())
        feats['open_30m_range'] = (open_30m_high - open_30m_low) / open_price
        feats['open_30m_range_ratio_partial'] = (open_30m_high - open_30m_low) / range_denom

        closes = df['close'].values
        hb = np.where(closes > open_30m_high)[0]
        lb = np.where(closes < open_30m_low)[0]
        feats['open_30m_high_break_min'] = int(hb[0]) if len(hb) > 0 else -1
        feats['open_30m_low_break_min']  = int(lb[0]) if len(lb) > 0 else -1

        twap_at_30 = float(twap.iloc[29]) if len(twap) > 29 else float(twap.iloc[-1])
        feats['open_30m_twap_dist'] = (twap_at_30 - open_price) / open_price * 100

        # Block C: partial trend
        feats['partial_return']     = (partial_close - open_price) / open_price
        feats['partial_range_pct']  = partial_range / open_price
        re = _range_epsilon(partial_high, partial_low, open_price)
        feats['partial_clv'] = (partial_close - partial_low - (partial_high - partial_close)) / re

        if n >= 2:
            x = np.arange(n, dtype=float)
            y = df['close'].values.astype(float)
            slope, intercept = np.polyfit(x, y, 1)
            y_pred = slope * x + intercept
            ss_res = np.sum((y - y_pred) ** 2)
            ss_tot = np.sum((y - y.mean()) ** 2)
            feats['partial_linreg_r2']    = 1.0 - ss_res / ss_tot if ss_tot > EPSILON else 0.0
            feats['partial_linreg_slope'] = slope / open_price
        else:
            feats['partial_linreg_r2']    = 0.0
            feats['partial_linreg_slope'] = 0.0

        n15 = len(df15m)
        if n15 >= 2:
            hh = int((df15m['high'].values[1:] > df15m['high'].values[:-1]).sum())
            ll = int((df15m['low'].values[1:]  < df15m['low'].values[:-1]).sum())
            feats['partial_hh_count_15m'] = hh / (n15 - 1)
            feats['partial_ll_count_15m'] = ll / (n15 - 1)
        else:
            feats['partial_hh_count_15m'] = 0.0
            feats['partial_ll_count_15m'] = 0.0

        # Block D: partial volatility
        ret1m = df['close'].pct_change().dropna()
        feats['partial_realized_vol'] = float(ret1m.std()) if len(ret1m) >= 2 else 0.0

        if len(df5m) >= 2:
            hi5 = df5m['high'].values
            lo5 = df5m['low'].values
            cl5 = df5m['close'].values
            tr1 = hi5 - lo5
            tr2 = np.abs(hi5[1:] - cl5[:-1])
            tr3 = np.abs(lo5[1:] - cl5[:-1])
            tr  = np.concatenate([[tr1[0]], np.maximum(tr1[1:], np.maximum(tr2, tr3))])
            feats['partial_atr_5m'] = float(tr.mean()) / open_price
        else:
            feats['partial_atr_5m'] = 0.0

        am_bars = df.iloc[:min(AM_END_BAR, n)]
        feats['range_pct_before_11am_partial'] = (
            (float(am_bars['high'].max()) - float(am_bars['low'].min())) / range_denom
        )

        am_ret   = df['close'].iloc[:min(AM_END_BAR, n)].pct_change().dropna()
        rest_ret = df['close'].iloc[AM_END_BAR:].pct_change().dropna()
        std_am   = float(am_ret.std())   if len(am_ret) >= 2   else EPSILON
        std_rest = float(rest_ret.std()) if len(rest_ret) >= 2 else EPSILON
        feats['partial_log_vol_expansion'] = math.log(
            max(std_am, EPSILON) / max(std_rest, EPSILON)
        ) if target_bar > AM_END_BAR else 0.0

        indices  = np.arange(n)
        abs_rets = df['close'].pct_change().abs().fillna(0).values
        sum_abs  = abs_rets.sum()
        com = float(np.dot(indices, abs_rets) / sum_abs) / max(n - 1, 1) if sum_abs > EPSILON else 0.5
        feats['partial_center_of_mass'] = float(np.clip(com, 0.0, 1.0))

        # Block E: partial TWAP microstructure
        diff = df['close'] - twap
        feats['partial_pct_above_twap']   = float((diff > 0).mean())
        feats['partial_twap_cross_count'] = int((np.diff(np.sign(diff.values)) != 0).sum())

        def longest_run(mask):
            runs, cur = 0, 0
            for v in mask:
                if v: cur += 1; runs = max(runs, cur)
                else: cur = 0
            return runs

        feats['partial_longest_above_twap'] = longest_run(diff.values > 0)
        feats['partial_longest_below_twap'] = longest_run(diff.values < 0)
        feats['partial_close_dist_twap']    = (partial_close - float(twap.iloc[-1])) / open_price * 100
        feats['partial_twap_dist_std']      = float(diff.std()) if len(diff) >= 2 else 0.0

        # Block F: partial rotation
        ret15 = df15m['close'].pct_change().dropna()
        feats['partial_flip_count_15m'] = int((np.diff(np.sign(ret15.values)) != 0).sum()) \
                                          if len(ret15) >= 2 else 0

        partial_clv_val = feats['partial_clv']
        feats['partial_dominant_direction_strength'] = abs(partial_clv_val)
        if abs(partial_clv_val) >= CLV_THRESHOLD and n15 >= 2:
            if partial_clv_val > 0:
                exc = (partial_high - df15m['low']).clip(lower=0)
            else:
                exc = (df15m['high'] - partial_low).clip(lower=0)
            feats['partial_avg_adverse_excursion'] = float(exc.mean()) / range_denom
        else:
            feats['partial_avg_adverse_excursion'] = 0.0

        # Derived composite signals
        feats['partial_trend_score'] = feats['partial_linreg_r2'] * np.sign(feats['partial_linreg_slope'])
        feats['partial_hh_minus_ll'] = feats['partial_hh_count_15m'] - feats['partial_ll_count_15m']
        feats['partial_twap_lean']   = feats['partial_pct_above_twap'] - 0.5
        feats['checkpoint_bar']      = target_bar

        # Block A: inject from historical EOD context (gap, prev-day features)
        # Skip for checkpoints whose model was trained without Block A (e.g. 13pm prod v1.1).
        _, _, _meta = self._models.get(cp, (None, None, {}))
        if not _meta.get('block_a_excluded', False):
            self._inject_block_a(feats)

        # Block H: BankNifty intermarket features (zero-safe if BN bars absent)
        feats.update(self._compute_block_h(target_bar, df))

        return feats

    def _inject_block_a(self, feats: dict) -> None:
        """Inject Block A features from historical EOD data if available."""
        if self._eod_df is None or self._session_date is None:
            for col in ['gap_pct', 'gap_dir', 'gap_size_pct60',
                        'prev_day_return', 'prev_day_range',
                        'prev_day_clv', 'prev_day_slope', 'prev_day_vol_pct']:
                feats[col] = 0.0
            return

        d = pd.Timestamp(self._session_date)
        block_a_cols = [
            'gap_pct', 'gap_dir', 'gap_size_pct60',
            'prev_day_return', 'prev_day_range',
            'prev_day_clv', 'prev_day_slope', 'prev_day_vol_pct',
        ]
        if d in self._eod_df.index:
            row = self._eod_df.loc[d]
            for col in block_a_cols:
                feats[col] = float(row[col]) if col in row and not pd.isna(row[col]) else 0.0
        else:
            # Live day: compute gap from last known EOD close
            prev_rows = self._eod_df[self._eod_df.index < d]
            if len(prev_rows) > 0 and self._bars:
                prev_row  = prev_rows.iloc[-1]
                open_price = float(self._bars[0]['open'])
                prev_close = float(prev_row.get('close', open_price))
                gap_pct = (open_price - prev_close) / prev_close * 100
                feats['gap_pct']          = gap_pct
                feats['gap_dir']          = float(np.sign(gap_pct))
                feats['gap_size_pct60']   = float(prev_row.get('gap_size_pct60', 0.0))
                feats['prev_day_return']  = float(prev_row.get('full_day_return', 0.0))
                feats['prev_day_range']   = float(prev_row.get('day_range_pct', 0.0))
                feats['prev_day_clv']     = float(prev_row.get('clv', 0.0))
                feats['prev_day_slope']   = float(prev_row.get('linreg_slope', 0.0))
                feats['prev_day_vol_pct'] = float(prev_row.get('prev_day_vol_pct', 0.0))
            else:
                for col in block_a_cols:
                    feats[col] = 0.0

        # Rolling percentiles for partial session metrics
        if self._session_date is not None:
            d_ts = pd.Timestamp(self._session_date)
            past = self._eod_df[self._eod_df.index < d_ts]
            if len(past) >= 2 and 'partial_realized_vol' in feats:
                vol_series = past['realized_vol'].dropna()
                cur_vol = feats.get('partial_realized_vol', 0.0)
                if len(vol_series) >= 1:
                    feats['partial_vol_pct20'] = float((vol_series < cur_vol).sum() / len(vol_series))
                else:
                    feats['partial_vol_pct20'] = 0.5
            else:
                feats['partial_vol_pct20'] = 0.5

            if 'partial_range_pct' in feats:
                range_series = past['day_range_pct'].dropna() if 'day_range_pct' in past.columns else pd.Series()
                cur_range = feats.get('partial_range_pct', 0.0)
                if len(range_series) >= 1:
                    feats['partial_range_pct20'] = float((range_series < cur_range).sum() / len(range_series))
                else:
                    feats['partial_range_pct20'] = 0.5
            else:
                feats['partial_range_pct20'] = 0.5

    # ── State access ───────────────────────────────────────────────────────────

    def get_state(self) -> Optional[DayTypeState]:
        """Return current day-type state. None if engine not started."""
        return self._state

    def get_state_dict(self) -> dict:
        """Return state as plain dict (for serialization / event bus)."""
        if self._state is None:
            return {}
        return self._state.to_dict()

    def is_locked(self) -> bool:
        return self._state is not None and self._state.locked

    def bars_collected(self) -> int:
        return len(self._bars)
