"""Download US100-equivalent M5 data from free sources.

Three methods (in order of preference):

  Method 1: Alpha Vantage — 2 full years of M5 data, free key, 25 calls/day
    python ftmo/download_data.py alphavantage --api-key YOUR_KEY

  Method 2: yfinance — 60 days of M5 data, NO key needed, instant
    python ftmo/download_data.py yfinance

  Method 3: Twelve Data — 2+ years, free key (only works for QQQ, not NDX)
    python ftmo/download_data.py twelvedata --api-key YOUR_KEY

All methods use QQQ (Nasdaq 100 ETF) which mirrors US100 CFD moves exactly.
Output is scaled to approximate US100 index levels (~QQQ × 38.5).

Sign up links:
  Alpha Vantage: https://www.alphavantage.co/support/#api-key  (free, instant)
  Twelve Data:   https://twelvedata.com                        (free, instant)
"""

import argparse
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent

# QQQ ≈ US100 / 38.5 (approximate ratio, varies slightly over time)
# We scale QQQ prices to US100 levels so ATR/risk values are realistic
QQQ_TO_US100_RATIO = 38.5


def scale_to_us100(df: pd.DataFrame) -> pd.DataFrame:
    """Scale QQQ prices to approximate US100 index levels."""
    df = df.copy()
    for col in ["open", "high", "low", "close"]:
        df[col] = (df[col] * QQQ_TO_US100_RATIO).round(2)
    return df


# ═══════════════════════════════════════════════════════════════════
# Method 1: Alpha Vantage (BEST — 2 years of M5, free)
# ═══════════════════════════════════════════════════════════════════

def download_alphavantage(api_key: str, months: int = 24) -> pd.DataFrame:
    """Download M5 data using Alpha Vantage TIME_SERIES_INTRADAY with month param.

    Free tier: 25 API calls/day. Each call = 1 month of 5min data.
    24 calls = 2 full years. All in one session.
    """
    import requests

    all_chunks = []
    now = datetime.utcnow()

    logger.info(f"Downloading QQQ M5 via Alpha Vantage ({months} months)...")

    for i in range(months):
        target = now - timedelta(days=i * 30)  # Approximate month
        month_str = target.strftime("%Y-%m")

        logger.info(f"  Fetching {month_str} ({i+1}/{months})...")

        params = {
            "function": "TIME_SERIES_INTRADAY",
            "symbol": "QQQ",
            "interval": "5min",
            "month": month_str,
            "outputsize": "full",
            "apikey": api_key,
            "datatype": "json",
        }

        try:
            resp = requests.get(
                "https://www.alphavantage.co/query",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"  Request failed: {e}")
            time.sleep(12)
            continue

        # Check for rate limit or error messages
        if "Note" in data or "Information" in data:
            msg = data.get("Note") or data.get("Information")
            logger.warning(f"  Rate limited: {msg}")
            logger.info("  Waiting 60s...")
            time.sleep(65)
            # Retry this month
            try:
                resp = requests.get(
                    "https://www.alphavantage.co/query",
                    params=params,
                    timeout=30,
                )
                data = resp.json()
            except Exception:
                continue

        ts_key = "Time Series (5min)"
        if ts_key not in data:
            logger.warning(f"  No data for {month_str}, keys: {list(data.keys())}")
            time.sleep(12)
            continue

        rows = []
        for ts_str, ohlcv in data[ts_key].items():
            rows.append({
                "time": pd.Timestamp(ts_str),
                "open": float(ohlcv["1. open"]),
                "high": float(ohlcv["2. high"]),
                "low": float(ohlcv["3. low"]),
                "close": float(ohlcv["4. close"]),
                "volume": float(ohlcv["5. volume"]),
            })

        if rows:
            chunk = pd.DataFrame(rows)
            all_chunks.append(chunk)
            logger.info(f"  Got {len(rows)} bars for {month_str}")
        else:
            logger.info(f"  Empty month: {month_str}")

        # Rate limit: 25/day free tier ≈ stay safe with 12s between calls
        time.sleep(13)

    if not all_chunks:
        raise RuntimeError("No data downloaded. Check your API key at https://www.alphavantage.co/support/#api-key")

    combined = pd.concat(all_chunks, ignore_index=True)
    combined = combined.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)

    logger.info(f"Alpha Vantage total: {len(combined)} bars")
    logger.info(f"Range: {combined['time'].iloc[0]} → {combined['time'].iloc[-1]}")

    return scale_to_us100(combined)


# ═══════════════════════════════════════════════════════════════════
# Method 2: yfinance (INSTANT — 60 days M5, no key needed)
# ═══════════════════════════════════════════════════════════════════

def download_yfinance(days: int = 60) -> pd.DataFrame:
    """Download M5 data using yfinance. No API key needed.

    Limitation: Yahoo only serves 60 days of 5m data.
    Good enough for pipeline testing and initial validation.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("Install yfinance: pip install yfinance")

    logger.info(f"Downloading QQQ M5 via yfinance (last {days} days, inc. pre/post market)...")

    ticker = yf.Ticker("QQQ")
    df = ticker.history(period=f"{days}d", interval="5m", prepost=True)

    if df.empty:
        raise RuntimeError("yfinance returned no data for QQQ")

    df = df.reset_index()

    # Standardize columns
    col_map = {}
    for c in df.columns:
        cl = c.lower() if isinstance(c, str) else str(c).lower()
        if "date" in cl or "time" in cl:
            col_map[c] = "time"
        elif cl == "open":
            col_map[c] = "open"
        elif cl == "high":
            col_map[c] = "high"
        elif cl == "low":
            col_map[c] = "low"
        elif cl == "close":
            col_map[c] = "close"
        elif cl == "volume":
            col_map[c] = "volume"

    df = df.rename(columns=col_map)
    df = df[["time", "open", "high", "low", "close", "volume"]]

    # Remove timezone info for CSV compatibility (will be re-added on import)
    if df["time"].dt.tz is not None:
        df["time"] = df["time"].dt.tz_localize(None)

    logger.info(f"yfinance total: {len(df)} bars")
    logger.info(f"Range: {df['time'].iloc[0]} → {df['time'].iloc[-1]}")

    return scale_to_us100(df)


# ═══════════════════════════════════════════════════════════════════
# Method 3: Twelve Data (2+ years, free key — QQQ works on free tier)
# ═══════════════════════════════════════════════════════════════════

def download_twelvedata(api_key: str, years: int = 2) -> pd.DataFrame:
    """Download M5 data using Twelve Data. QQQ is free (NDX is not)."""
    import requests

    end = datetime.utcnow()
    start = end - timedelta(days=years * 365)

    all_chunks = []
    current_end = end
    request_count = 0

    logger.info(f"Downloading QQQ M5 via Twelve Data: {start.date()} → {end.date()}")

    while current_end > start:
        chunk_start = current_end - timedelta(days=25)
        if chunk_start < start:
            chunk_start = start

        logger.info(f"  Fetching {chunk_start.date()} → {current_end.date()}...")

        params = {
            "symbol": "QQQ",
            "interval": "5min",
            "start_date": chunk_start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": current_end.strftime("%Y-%m-%d %H:%M:%S"),
            "outputsize": 5000,
            "apikey": api_key,
            "format": "JSON",
            "timezone": "UTC",
        }

        try:
            resp = requests.get(
                "https://api.twelvedata.com/time_series",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"  Error: {e}")
            time.sleep(10)
            continue

        if "code" in data and data["code"] != 200:
            logger.error(f"  API error: {data.get('message', 'unknown')}")
            time.sleep(10)
            current_end = chunk_start
            continue

        if "values" not in data or not data["values"]:
            current_end = chunk_start
            time.sleep(1)
            continue

        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)
        df = df.rename(columns={"datetime": "time"})
        df = df[["time", "open", "high", "low", "close", "volume"]]
        df = df.sort_values("time").reset_index(drop=True)

        all_chunks.append(df)
        request_count += 1

        earliest = df["time"].iloc[0]
        logger.info(f"  Got {len(df)} bars ({earliest} → {df['time'].iloc[-1]})")
        current_end = earliest - timedelta(minutes=5)

        # Rate limiting: free = 8 req/min
        if request_count % 7 == 0:
            logger.info("  Rate limit pause...")
            time.sleep(62)
        else:
            time.sleep(8)

    if not all_chunks:
        raise RuntimeError("No data downloaded from Twelve Data")

    combined = pd.concat(all_chunks, ignore_index=True)
    combined = combined.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)

    logger.info(f"Twelve Data total: {len(combined)} bars, {request_count} requests")
    return scale_to_us100(combined)


# ═══════════════════════════════════════════════════════════════════

def save_csv(df: pd.DataFrame, output: Path):
    """Save as TradingView-compatible CSV for our ingest.py."""
    out = df.copy()
    out.columns = ["time", "open", "high", "low", "close", "Volume"]
    out.to_csv(str(output), index=False)
    logger.info(f"Saved {len(out)} bars to {output}")
    logger.info(f"Next step: python -m ftmo.cli import {output}")


def main():
    parser = argparse.ArgumentParser(
        description="Download US100-equivalent M5 data from free sources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python ftmo/download_data.py yfinance                          # 60 days, instant, no key
  python ftmo/download_data.py alphavantage --api-key ABCDEF     # 2 years, free key
  python ftmo/download_data.py twelvedata --api-key ABCDEF       # 2 years, free key
        """,
    )

    sub = parser.add_subparsers(dest="source", required=True)

    # yfinance
    p_yf = sub.add_parser("yfinance", help="60 days M5, no API key needed (instant)")
    p_yf.add_argument("--output", default=None)

    # Alpha Vantage
    p_av = sub.add_parser("alphavantage", help="2 years M5, free API key")
    p_av.add_argument("--api-key", required=True, help="Free key from alphavantage.co")
    p_av.add_argument("--months", type=int, default=24, help="Months of history (default: 24)")
    p_av.add_argument("--output", default=None)

    # Twelve Data
    p_td = sub.add_parser("twelvedata", help="2 years M5, free API key (QQQ)")
    p_td.add_argument("--api-key", required=True, help="Free key from twelvedata.com")
    p_td.add_argument("--years", type=int, default=2, help="Years of history (default: 2)")
    p_td.add_argument("--output", default=None)

    args = parser.parse_args()
    output = Path(args.output) if args.output else OUTPUT_DIR / "US100_M5.csv"

    if args.source == "yfinance":
        df = download_yfinance()
    elif args.source == "alphavantage":
        df = download_alphavantage(args.api_key, months=args.months)
    elif args.source == "twelvedata":
        df = download_twelvedata(args.api_key, years=args.years)

    save_csv(df, output)


if __name__ == "__main__":
    main()
