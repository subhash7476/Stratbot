"""
Layer 1: OBSERVER — Daily intermarket feature engineering.

Computes 7 features from Nifty 50, Bank Nifty, and India VIX daily data.
Features 1-6 are HMM inputs; feature 7 (gap_pct) is an execution filter only.
USDINR slope is omitted (unavailable via Upstox) — HMM uses 6 features.
"""
import numpy as np
import pandas as pd
from typing import Optional


class RegimeObserver:
    """Computes daily intermarket features for regime classification."""

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.vix_pctl_window = cfg.get('vix_pctl_window', 90)
        self.vix_roc_period = cfg.get('vix_roc_period', 5)
        self.nifty_ema_period = cfg.get('nifty_ema_period', 20)
        self.nifty_slope_delta = cfg.get('nifty_slope_delta', 5)
        self.realized_vol_window = cfg.get('realized_vol_window', 10)

    def compute_features(
        self,
        nifty_daily: pd.DataFrame,
        banknifty_daily: pd.DataFrame,
        vix_daily: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Compute aligned daily feature DataFrame.

        Each input must have columns: [timestamp, open, high, low, close, volume]
        or be indexed by datetime with those columns.

        Returns DataFrame indexed by date with columns:
            vix_level, vix_pctl_90d, vix_roc_5d,
            banknifty_nifty_ratio, nifty_20dma_slope, realized_vol_10d,
            gap_pct
        """
        # Normalize inputs to date-indexed close series
        nifty = self._to_daily_series(nifty_daily, 'nifty')
        banknifty = self._to_daily_series(banknifty_daily, 'banknifty')
        vix = self._to_daily_series(vix_daily, 'vix')

        # Align all series on common dates
        aligned = pd.DataFrame({
            'nifty_close': nifty['close'],
            'nifty_open': nifty['open'],
            'banknifty_close': banknifty['close'],
            'vix_close': vix['close'],
        }).dropna()

        features = pd.DataFrame(index=aligned.index)

        # 1. VIX level
        features['vix_level'] = aligned['vix_close']

        # 2. VIX 90-day rolling percentile
        features['vix_pctl_90d'] = aligned['vix_close'].rolling(
            self.vix_pctl_window, min_periods=30
        ).apply(lambda x: (x.iloc[-1] >= x).sum() / len(x), raw=False)

        # 3. VIX 5-day rate of change
        vix_shifted = aligned['vix_close'].shift(self.vix_roc_period)
        features['vix_roc_5d'] = (aligned['vix_close'] - vix_shifted) / vix_shifted

        # 4. Bank Nifty / Nifty ratio (financial sector leadership)
        features['banknifty_nifty_ratio'] = aligned['banknifty_close'] / aligned['nifty_close']

        # 5. Nifty 20DMA slope (5-day delta of EMA-20)
        ema20 = aligned['nifty_close'].ewm(span=self.nifty_ema_period, adjust=False).mean()
        features['nifty_20dma_slope'] = ema20.diff(self.nifty_slope_delta)

        # 6. Realized volatility (10-day rolling stdev of log returns)
        log_returns = np.log(aligned['nifty_close'] / aligned['nifty_close'].shift(1))
        features['realized_vol_10d'] = log_returns.rolling(
            self.realized_vol_window, min_periods=5
        ).std()

        # 7. Gap % (execution filter, NOT HMM input)
        prev_close = aligned['nifty_close'].shift(1)
        features['gap_pct'] = (aligned['nifty_open'] - prev_close) / prev_close

        return features.dropna()

    def get_hmm_features(self, features: pd.DataFrame) -> pd.DataFrame:
        """Return only the 6 features used as HMM inputs (excludes gap_pct)."""
        hmm_cols = [
            'vix_level', 'vix_pctl_90d', 'vix_roc_5d',
            'banknifty_nifty_ratio', 'nifty_20dma_slope', 'realized_vol_10d'
        ]
        return features[hmm_cols]

    def _to_daily_series(self, df: pd.DataFrame, name: str) -> pd.DataFrame:
        """Normalize input to date-indexed DataFrame with open/close columns."""
        result = df.copy()

        # If index is not datetime, try to use 'timestamp' column
        if not isinstance(result.index, pd.DatetimeIndex):
            if 'timestamp' in result.columns:
                result.index = pd.to_datetime(result['timestamp'])
            else:
                raise ValueError(f"{name}: need DatetimeIndex or 'timestamp' column")

        # Convert to date index (take last value per day for daily data)
        result.index = result.index.date
        result = result[~result.index.duplicated(keep='last')]

        for col in ['open', 'close']:
            if col not in result.columns:
                raise ValueError(f"{name}: missing '{col}' column")

        return result[['open', 'close']]
