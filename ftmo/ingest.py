"""CSV import: auto-detect TradingView / MetaTrader format → standardized DataFrame."""

import pandas as pd
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

IST = "Asia/Kolkata"


def detect_csv_format(filepath: Path) -> str:
    with open(filepath, "r", encoding="utf-8-sig") as f:
        header = f.readline().strip().lower()

    if "tick volume" in header or "<tickvol>" in header or "\t" in header:
        return "metatrader"
    if "time" in header and ("volume" in header or "vol" in header):
        return "tradingview"

    raise ValueError(
        f"Unrecognized CSV format. Header: {header}\n"
        "Expected TradingView (time,open,high,low,close,Volume) or "
        "MetaTrader (Date,Time,Open,High,Low,Close,Tick Volume)"
    )


def load_tradingview_csv(filepath: Path, source_tz: str = "UTC") -> pd.DataFrame:
    df = pd.read_csv(filepath)
    df.columns = [c.strip().lower() for c in df.columns]

    time_col = "time" if "time" in df.columns else "datetime"
    if time_col not in df.columns:
        raise ValueError(f"No time/datetime column found. Columns: {list(df.columns)}")

    df["timestamp"] = pd.to_datetime(df[time_col])

    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(source_tz)
    df["timestamp"] = df["timestamp"].dt.tz_convert(IST)

    vol_col = next((c for c in df.columns if "vol" in c and c != "timestamp"), None)

    result = pd.DataFrame({
        "timestamp": df["timestamp"],
        "open": df["open"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "close": df["close"].astype(float),
        "volume": df[vol_col].astype(float) if vol_col else 0,
    })
    return result.sort_values("timestamp").reset_index(drop=True)


def load_metatrader_csv(filepath: Path, source_tz: str = "UTC") -> pd.DataFrame:
    # MT5 can use tab or comma delimiter, and various header formats
    with open(filepath, "r", encoding="utf-8-sig") as f:
        first_line = f.readline()
    sep = "\t" if "\t" in first_line else ","

    df = pd.read_csv(filepath, sep=sep)
    df.columns = [c.strip().lower().replace("<", "").replace(">", "") for c in df.columns]

    # MT5 format: <DATE>, <TIME>, <OPEN>, ...  or  Date, Time, Open, ...
    if "date" in df.columns and "time" in df.columns:
        df["timestamp"] = pd.to_datetime(df["date"] + " " + df["time"])
    elif "date" in df.columns:
        df["timestamp"] = pd.to_datetime(df["date"])
    elif "datetime" in df.columns:
        df["timestamp"] = pd.to_datetime(df["datetime"])
    else:
        raise ValueError(f"No date column found. Columns: {list(df.columns)}")

    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(source_tz)
    df["timestamp"] = df["timestamp"].dt.tz_convert(IST)

    vol_col = next(
        (c for c in df.columns if c in ("tickvol", "tick volume", "vol", "volume")),
        None,
    )

    result = pd.DataFrame({
        "timestamp": df["timestamp"],
        "open": df["open"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "close": df["close"].astype(float),
        "volume": df[vol_col].astype(float) if vol_col else 0,
    })
    return result.sort_values("timestamp").reset_index(drop=True)


def import_csv(filepath: str | Path, source_tz: str = "UTC") -> pd.DataFrame:
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"CSV file not found: {filepath}")

    fmt = detect_csv_format(filepath)
    logger.info(f"Detected CSV format: {fmt}")

    if fmt == "tradingview":
        df = load_tradingview_csv(filepath, source_tz)
    else:
        df = load_metatrader_csv(filepath, source_tz)

    # Validate
    if len(df) < 100:
        raise ValueError(f"Only {len(df)} bars loaded — need at least 100 for ATR warmup")

    date_range = f"{df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}"
    logger.info(f"Loaded {len(df)} M5 bars: {date_range}")

    return df
