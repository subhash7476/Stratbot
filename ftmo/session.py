"""Session classifier: pre-NY range detection and trading window logic."""

from dataclasses import dataclass
from datetime import datetime, time
import pandas as pd

from ftmo.config import PRE_NY_START, PRE_NY_END, NY_START, NY_END


@dataclass(frozen=True)
class PreNYRange:
    high: float
    low: float
    date: str  # YYYY-MM-DD


def classify_bar(ts: datetime) -> str:
    """Classify an IST timestamp into session zone."""
    t = ts.time() if hasattr(ts, "time") else ts
    if PRE_NY_START <= t < PRE_NY_END:
        return "PRE_NY"
    if NY_START <= t < NY_END:
        return "NY_SESSION"
    return "OUTSIDE"


def get_session_date(ts: datetime) -> str:
    """Return the session date string for a timestamp.
    The session date is the IST calendar date on which 6:00 PM falls."""
    return ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]


def compute_pre_ny_ranges(df_m5: pd.DataFrame) -> dict[str, PreNYRange]:
    """Compute pre-NY high/low for each trading date."""
    df = df_m5.copy()
    df["session"] = df["timestamp"].apply(classify_bar)
    df["session_date"] = df["timestamp"].apply(get_session_date)

    pre_ny = df[df["session"] == "PRE_NY"]
    ranges = {}

    for date, group in pre_ny.groupby("session_date"):
        if len(group) < 2:
            continue  # Need at least 2 bars for a valid range
        ranges[date] = PreNYRange(
            high=group["high"].max(),
            low=group["low"].min(),
            date=date,
        )

    return ranges


def get_ny_session_bars(df_m5: pd.DataFrame, session_date: str) -> pd.DataFrame:
    """Extract NY session bars (6:00-8:00 PM IST) for a given date."""
    df = df_m5.copy()
    df["session_date"] = df["timestamp"].apply(get_session_date)
    df["session"] = df["timestamp"].apply(classify_bar)

    mask = (df["session_date"] == session_date) & (df["session"] == "NY_SESSION")
    return df[mask].reset_index(drop=True)


def get_trading_dates(df_m5: pd.DataFrame) -> list[str]:
    """Return sorted list of dates that have both pre-NY and NY session bars."""
    df = df_m5.copy()
    df["session"] = df["timestamp"].apply(classify_bar)
    df["session_date"] = df["timestamp"].apply(get_session_date)

    pre_ny_dates = set(df[df["session"] == "PRE_NY"]["session_date"].unique())
    ny_dates = set(df[df["session"] == "NY_SESSION"]["session_date"].unique())

    valid_dates = sorted(pre_ny_dates & ny_dates)
    return valid_dates
