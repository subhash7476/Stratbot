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


def _classify_all(df_m5: pd.DataFrame) -> pd.DataFrame:
    """Classify all bars in one vectorised pass. Returns df with session + session_date columns."""
    df = df_m5.copy()
    t = df["timestamp"].dt.time
    pre_mask = (t >= PRE_NY_START) & (t < PRE_NY_END)
    ny_mask = (t >= NY_START) & (t < NY_END)
    df["session"] = "OUTSIDE"
    df.loc[pre_mask, "session"] = "PRE_NY"
    df.loc[ny_mask, "session"] = "NY_SESSION"
    df["session_date"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    return df


def compute_pre_ny_ranges(df_m5: pd.DataFrame) -> dict[str, PreNYRange]:
    """Compute pre-session high/low for each trading date (vectorised)."""
    df = _classify_all(df_m5)
    pre_ny = df[df["session"] == "PRE_NY"]
    ranges = {}
    for date, group in pre_ny.groupby("session_date"):
        if len(group) < 2:
            continue
        ranges[date] = PreNYRange(high=group["high"].max(), low=group["low"].min(), date=date)
    return ranges


def get_all_ny_bars(df_m5: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Pre-group all NY session bars by date in one pass — avoids per-date full-scan."""
    df = _classify_all(df_m5)
    ny = df[df["session"] == "NY_SESSION"]
    return {date: group.reset_index(drop=True) for date, group in ny.groupby("session_date")}


def get_ny_session_bars(df_m5: pd.DataFrame, session_date: str) -> pd.DataFrame:
    """Extract NY session bars for a given date (single-date lookup)."""
    df = _classify_all(df_m5)
    mask = (df["session_date"] == session_date) & (df["session"] == "NY_SESSION")
    return df[mask].reset_index(drop=True)


def get_trading_dates(df_m5: pd.DataFrame) -> list[str]:
    """Return sorted dates that have both pre-session and NY session bars (vectorised)."""
    df = _classify_all(df_m5)
    pre_ny_dates = set(df[df["session"] == "PRE_NY"]["session_date"].unique())
    ny_dates = set(df[df["session"] == "NY_SESSION"]["session_date"].unique())
    return sorted(pre_ny_dates & ny_dates)
