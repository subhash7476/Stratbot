"""
Day-Type State Monitor
=======================
3 hardening components for production deployment:

  1. PredictionDriftMonitor
     Tracks rolling 30-day accuracy. Alerts if accuracy drops
     more than 2 std-dev below historical mean.

  2. ClusterFrequencyMonitor
     Tracks rolling 30-day predicted cluster distribution.
     Alerts if any cluster deviates more than 2 std-dev from
     historical base rate.

  3. ConfidenceDriftMonitor
     Tracks rolling 30-day mean confidence.
     Flags if confidence trends up while accuracy trends down
     (overconfidence drift — the silent killer).

All monitors work from a persistent log file:
  data/features/day_type/daytype_live_log.csv

Schema:
  date, checkpoint, predicted_cluster, actual_cluster, confidence,
  conf_tier, correct, locked, p_bear, p_bull, p_choppy

Usage:
  from core.state.daytype_monitor import DayTypeMonitor
  monitor = DayTypeMonitor()
  monitor.log_prediction(date, checkpoint, state, actual_cluster)
  alerts = monitor.check_alerts()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_PATH = ROOT / "data" / "features" / "day_type" / "daytype_live_log.csv"

logger = logging.getLogger(__name__)

# Tuning parameters
ROLLING_WINDOW   = 30    # days for rolling statistics
ALERT_SIGMA      = 2.0   # std-dev threshold for alert
MIN_SAMPLES      = 15    # minimum samples before monitoring is active
CONF_DRIFT_SLOPE = 0.002 # minimum rising confidence slope to flag (per day)

# Historical baselines from Phase 3 validation (2025 val set, 13pm intraday-only)
HISTORICAL_ACCURACY   = 0.721   # 72.1% no-Block-A validation accuracy
HISTORICAL_ACCURACY_STD = 0.08  # conservative std estimate for 30-day window
HISTORICAL_CLUSTER_DIST = {0: 0.282, 1: 0.328, 2: 0.390}  # cluster base rates
HISTORICAL_CONFIDENCE   = 0.70  # typical mean confidence at 13pm


@dataclass
class MonitorAlert:
    alert_type: str    # 'accuracy_drift' | 'cluster_freq' | 'confidence_drift'
    severity:   str    # 'warning' | 'critical'
    message:    str
    value:      float
    threshold:  float
    window_days: int

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.alert_type}: {self.message} (value={self.value:.3f}, threshold={self.threshold:.3f})"


class DayTypeMonitor:
    """
    Persistent prediction logger and drift alerting system.
    Thread-safe for single-process use.
    """

    def __init__(self, log_path: Path = LOG_PATH):
        self.log_path = log_path
        self._log_df: Optional[pd.DataFrame] = None
        self._load_log()

    # ── Logging ────────────────────────────────────────────────────────────────

    def _load_log(self) -> None:
        if self.log_path.exists():
            try:
                self._log_df = pd.read_csv(
                    self.log_path, index_col='date', parse_dates=True
                )
            except Exception as e:
                logger.warning(f"Could not load prediction log: {e}")
                self._log_df = self._empty_log()
        else:
            self._log_df = self._empty_log()

    @staticmethod
    def _empty_log() -> pd.DataFrame:
        return pd.DataFrame(columns=[
            'checkpoint', 'predicted_cluster', 'actual_cluster',
            'confidence', 'conf_tier', 'correct', 'locked',
            'p_bear', 'p_bull', 'p_choppy'
        ])

    def log_prediction(
        self,
        session_date: date,
        checkpoint: str,
        predicted_cluster: int,
        confidence: float,
        conf_tier: str,
        locked: bool,
        p_bear: float,
        p_bull: float,
        p_choppy: float,
        actual_cluster: Optional[int] = None,
    ) -> None:
        """
        Log a prediction. Call at each checkpoint.
        actual_cluster can be filled in retroactively via update_actual().
        """
        row = pd.DataFrame([{
            'checkpoint':        checkpoint,
            'predicted_cluster': predicted_cluster,
            'actual_cluster':    actual_cluster,
            'confidence':        confidence,
            'conf_tier':         conf_tier,
            'correct':           int(predicted_cluster == actual_cluster) if actual_cluster is not None else np.nan,
            'locked':            locked,
            'p_bear':            p_bear,
            'p_bull':            p_bull,
            'p_choppy':          p_choppy,
        }], index=pd.DatetimeIndex([pd.Timestamp(session_date)], name='date'))

        # Upsert: replace existing row for (date, checkpoint) if present
        key = (pd.Timestamp(session_date), checkpoint)
        existing = self._log_df[
            (self._log_df.index == key[0]) &
            (self._log_df['checkpoint'] == key[1])
        ]
        if len(existing) > 0:
            self._log_df = self._log_df.drop(index=existing.index)

        self._log_df = pd.concat([self._log_df, row]).sort_index()
        self._save_log()

    def update_actual(self, session_date: date, actual_cluster: int) -> None:
        """
        Fill in actual_cluster after EOD cluster is known.
        Updates all rows for that date. Call once per day after market close.
        """
        d_ts = pd.Timestamp(session_date)
        mask = self._log_df.index == d_ts
        if mask.sum() == 0:
            return
        self._log_df.loc[mask, 'actual_cluster'] = actual_cluster
        self._log_df.loc[mask, 'correct'] = (
            self._log_df.loc[mask, 'predicted_cluster'] == actual_cluster
        ).astype(float)
        self._save_log()

    def _save_log(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_df.to_csv(self.log_path)

    # ── Alert checks ───────────────────────────────────────────────────────────

    def check_alerts(self, checkpoint: str = '13pm') -> list[MonitorAlert]:
        """
        Run all 3 drift checks for the given checkpoint.
        Returns list of MonitorAlert objects (empty = all clear).
        """
        alerts = []
        df = self._log_df[self._log_df['checkpoint'] == checkpoint].copy()

        if len(df) < MIN_SAMPLES:
            logger.info(f"Monitor: only {len(df)} samples for {checkpoint}, need {MIN_SAMPLES}")
            return alerts

        # Only rows where we know the actual outcome
        df_known = df[df['actual_cluster'].notna()].copy()
        if len(df_known) < MIN_SAMPLES:
            return alerts

        alerts.extend(self._check_accuracy_drift(df_known))
        alerts.extend(self._check_cluster_frequency(df))
        alerts.extend(self._check_confidence_drift(df_known))

        for a in alerts:
            logger.warning(str(a))
        return alerts

    def _check_accuracy_drift(self, df: pd.DataFrame) -> list[MonitorAlert]:
        """Rolling 30-day accuracy vs historical baseline."""
        alerts = []
        recent = df.tail(ROLLING_WINDOW)
        if len(recent) < MIN_SAMPLES:
            return alerts

        rolling_acc = recent['correct'].mean()
        # Alert threshold: historical_mean - 2*std
        lower_bound = HISTORICAL_ACCURACY - ALERT_SIGMA * HISTORICAL_ACCURACY_STD
        critical_bound = HISTORICAL_ACCURACY - 3.0 * HISTORICAL_ACCURACY_STD

        if rolling_acc < critical_bound:
            alerts.append(MonitorAlert(
                alert_type  = 'accuracy_drift',
                severity    = 'critical',
                message     = f"30d accuracy {rolling_acc:.1%} is >3 std-dev below baseline {HISTORICAL_ACCURACY:.1%}",
                value       = rolling_acc,
                threshold   = critical_bound,
                window_days = len(recent),
            ))
        elif rolling_acc < lower_bound:
            alerts.append(MonitorAlert(
                alert_type  = 'accuracy_drift',
                severity    = 'warning',
                message     = f"30d accuracy {rolling_acc:.1%} is >2 std-dev below baseline {HISTORICAL_ACCURACY:.1%}",
                value       = rolling_acc,
                threshold   = lower_bound,
                window_days = len(recent),
            ))
        return alerts

    def _check_cluster_frequency(self, df: pd.DataFrame) -> list[MonitorAlert]:
        """
        Rolling 30-day predicted cluster distribution vs historical base rate.
        Flags if any cluster deviates > 2 std-dev.
        Std-dev for proportion: sqrt(p*(1-p)/n).
        """
        alerts = []
        recent = df.tail(ROLLING_WINDOW)
        n = len(recent)
        if n < MIN_SAMPLES:
            return alerts

        counts = recent['predicted_cluster'].value_counts(normalize=True)
        for cluster_id, base_rate in HISTORICAL_CLUSTER_DIST.items():
            observed = counts.get(cluster_id, 0.0)
            std = np.sqrt(base_rate * (1 - base_rate) / n)
            deviation = abs(observed - base_rate) / (std + 1e-10)

            if deviation > 3.0 * ALERT_SIGMA:
                alerts.append(MonitorAlert(
                    alert_type  = 'cluster_freq',
                    severity    = 'critical',
                    message     = f"Cluster {cluster_id} frequency {observed:.1%} vs baseline {base_rate:.1%} ({deviation:.1f} std-dev)",
                    value       = observed,
                    threshold   = base_rate,
                    window_days = n,
                ))
            elif deviation > ALERT_SIGMA:
                alerts.append(MonitorAlert(
                    alert_type  = 'cluster_freq',
                    severity    = 'warning',
                    message     = f"Cluster {cluster_id} frequency {observed:.1%} vs baseline {base_rate:.1%} ({deviation:.1f} std-dev)",
                    value       = observed,
                    threshold   = base_rate,
                    window_days = n,
                ))
        return alerts

    def _check_confidence_drift(self, df: pd.DataFrame) -> list[MonitorAlert]:
        """
        Detect overconfidence drift: confidence rising while accuracy falling.
        Uses simple linear slope on rolling window.
        """
        alerts = []
        recent = df.tail(ROLLING_WINDOW).copy()
        if len(recent) < MIN_SAMPLES:
            return alerts

        recent = recent.reset_index()
        recent['t'] = np.arange(len(recent))

        # Confidence slope
        if recent['confidence'].std() > 0:
            conf_slope = np.polyfit(recent['t'], recent['confidence'], 1)[0]
        else:
            conf_slope = 0.0

        # Accuracy slope
        if recent['correct'].std() > 0:
            acc_slope = np.polyfit(recent['t'], recent['correct'].astype(float), 1)[0]
        else:
            acc_slope = 0.0

        # Overconfidence pattern: conf rising, accuracy falling
        if conf_slope > CONF_DRIFT_SLOPE and acc_slope < -CONF_DRIFT_SLOPE:
            alerts.append(MonitorAlert(
                alert_type  = 'confidence_drift',
                severity    = 'warning',
                message     = (f"Confidence rising ({conf_slope:+.4f}/day) while "
                               f"accuracy falling ({acc_slope:+.4f}/day) — possible overconfidence drift"),
                value       = conf_slope,
                threshold   = CONF_DRIFT_SLOPE,
                window_days = len(recent),
            ))

        # Absolute mean confidence rising beyond expected
        mean_conf = recent['confidence'].mean()
        if mean_conf > HISTORICAL_CONFIDENCE + 0.10:
            alerts.append(MonitorAlert(
                alert_type  = 'confidence_drift',
                severity    = 'warning',
                message     = f"Mean confidence {mean_conf:.2f} is >0.10 above historical {HISTORICAL_CONFIDENCE:.2f}",
                value       = mean_conf,
                threshold   = HISTORICAL_CONFIDENCE + 0.10,
                window_days = len(recent),
            ))

        return alerts

    # ── Summary report ─────────────────────────────────────────────────────────

    def summary(self, checkpoint: str = '13pm', last_n: int = 30) -> str:
        """
        Return a human-readable summary of recent prediction performance.
        """
        df = self._log_df[self._log_df['checkpoint'] == checkpoint]
        df_known = df[df['actual_cluster'].notna()]
        recent   = df_known.tail(last_n)

        if len(recent) == 0:
            return f"No data for checkpoint {checkpoint}"

        lines = [
            f"DayTypeMonitor — {checkpoint} — last {len(recent)} days",
            f"  Accuracy:    {recent['correct'].mean():.1%}",
            f"  Mean conf:   {recent['confidence'].mean():.3f}",
            f"  High-conf %: {(recent['confidence'] >= 0.70).mean():.1%}",
        ]

        counts = recent['predicted_cluster'].value_counts(normalize=True)
        lines.append("  Cluster freq (predicted):")
        for c, name in {0:'BearTrend',1:'BullTrend',2:'Choppy'}.items():
            obs  = counts.get(c, 0.0)
            base = HISTORICAL_CLUSTER_DIST[c]
            lines.append(f"    {name}: {obs:.1%}  (baseline {base:.1%})")

        alerts = self.check_alerts(checkpoint)
        if alerts:
            lines.append(f"  ALERTS ({len(alerts)}):")
            for a in alerts:
                lines.append(f"    {a}")
        else:
            lines.append("  ALERTS: none")

        return '\n'.join(lines)

    def get_log(self, checkpoint: Optional[str] = None) -> pd.DataFrame:
        """Return raw prediction log, optionally filtered by checkpoint."""
        if checkpoint:
            return self._log_df[self._log_df['checkpoint'] == checkpoint].copy()
        return self._log_df.copy()
