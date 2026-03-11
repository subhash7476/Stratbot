"""ATR calculation and M5 → M15 resampling."""

import pandas as pd
import numpy as np
from ftmo.config import M5_ATR_PERIOD, M15_ATR_PERIOD


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR: true range smoothed with EWM."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    return tr.ewm(span=period, adjust=False).mean()


def resample_m5_to_m15(df_m5: pd.DataFrame) -> pd.DataFrame:
    """Resample M5 OHLCV to M15. Timestamp column, not index."""
    df = df_m5.copy()
    df = df.set_index("timestamp")

    resampled = df.resample("15min").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])

    resampled = resampled.reset_index()
    return resampled


def enrich_with_indicators(df_m5: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add ATR columns to M5 and M15 DataFrames.

    Returns (df_m5_enriched, df_m15) where:
    - df_m5 has 'atr' (M5 ATR) and 'm15_atr' columns
    - df_m15 has 'atr' (M15 ATR) column
    """
    df_m5 = df_m5.copy()

    # M5 ATR
    df_m5["atr"] = compute_atr(df_m5, M5_ATR_PERIOD)

    # M15 resampling and ATR
    df_m15 = resample_m5_to_m15(df_m5)
    df_m15["atr"] = compute_atr(df_m15, M15_ATR_PERIOD)

    # Map M15 ATR back to M5 bars via floor to nearest 15min
    df_m5["m15_ts"] = df_m5["timestamp"].dt.floor("15min")
    m15_lookup = df_m15[["timestamp", "atr"]].rename(
        columns={"timestamp": "m15_ts", "atr": "m15_atr"}
    )
    df_m5 = df_m5.merge(m15_lookup, on="m15_ts", how="left")
    df_m5.drop(columns=["m15_ts"], inplace=True)

    # Forward-fill M15 ATR for the first few bars where M15 ATR is NaN
    df_m5["m15_atr"] = df_m5["m15_atr"].ffill()

    return df_m5, df_m15
