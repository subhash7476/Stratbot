# core/api/instruments.py
"""
Upstox Instruments Downloader (Restored Original Logic)
-------------------------------------------------------
This module follows the EXACT behavior of your previously working version:
- Downloads complete.json.gz from Upstox CDN
- Decompresses using gzip
- Normalizes JSON structure
- Renames standard columns
- Keeps only the required fields
- Saves `instruments/all_instruments.parquet`
- Splits into `instruments/segment_wise/<segment>.parquet`
- Implements daily caching logic
"""

import os
import gzip
import json
import requests
import pandas as pd
from datetime import datetime
from pathlib import Path

# --- Paths ---
INSTRUMENT_DIR = Path("instruments")
SEGMENT_DIR = INSTRUMENT_DIR / "segment_wise"
SEGMENT_DIR.mkdir(parents=True, exist_ok=True)

LAST_UPDATED_FILE = INSTRUMENT_DIR / "last_updated.txt"

JSON_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"

# ---------------------------------------------------
# Helper: Detect if instruments were already updated
# ---------------------------------------------------
def is_today_updated() -> bool:
    if not LAST_UPDATED_FILE.exists():
        return False
    try:
        last_date = LAST_UPDATED_FILE.read_text().strip()
        return last_date == datetime.now().strftime("%Y-%m-%d")
    except:
        return False

# ---------------------------------------------------
# Main function: Download + Normalize + Split
# ---------------------------------------------------
def download_and_split_instruments() -> bool:

    if is_today_updated():
        print("Instruments already updated today.")
        return True

    try:
        print("Downloading complete.json.gz ...")
        r = requests.get(JSON_URL, timeout=30)
        r.raise_for_status()

        # Decompress gzip content
        content = gzip.decompress(r.content)
        data = json.loads(content.decode("utf-8"))

        # JSON normalization
        if "data" in data:
            df = pd.json_normalize(data["data"])
        else:
            df = pd.json_normalize(data)

        # Standardize columns (IDENTICAL to your old code)
        df = df.rename(columns={
            'tradingsymbol': 'trading_symbol',
            'instrumenttype': 'instrument_type',
            'exchange': 'exchange',
            'segment': 'segment',
            'lotsize': 'lot_size',
            'ticksize': 'tick_size',
            'expiry': 'expiry',
            'strike': 'strike_price',
        })

        # Keep only essential columns (IDENTICAL to your old code)
        keep_cols = [
            'instrument_key', 'trading_symbol', 'name', 'instrument_type',
            'exchange', 'segment', 'lot_size', 'tick_size', 'expiry', 'strike_price'
        ]

        df = df[[c for c in keep_cols if c in df.columns]]

        # Save full instruments
        INSTRUMENT_DIR.mkdir(exist_ok=True)
        df.to_parquet(INSTRUMENT_DIR / "all_instruments.parquet", index=False)

        # Split by segment
        for segment in df['segment'].dropna().unique():
            segment_df = df[df['segment'] == segment].copy()
            safe_name = str(segment).replace(":", "_")
            segment_df.to_parquet(SEGMENT_DIR / f"{safe_name}.parquet", index=False)

        # Update timestamp
        LAST_UPDATED_FILE.write_text(datetime.now().strftime("%Y-%m-%d"))

        print(f"Instruments updated & split: {len(df)} symbols.")
        return True

    except Exception as e:
        print("Instrument update failed:", e)
        return False

# ---------------------------------------------------
# Helper functions for UI
# ---------------------------------------------------
def load_segment_instruments(segment: str) -> pd.DataFrame:
    file = SEGMENT_DIR / f"{segment.replace(':', '_')}.parquet"
    if not file.exists():
        return pd.DataFrame()
    return pd.read_parquet(file)

def list_segments():
    return [p.stem for p in SEGMENT_DIR.glob("*.parquet")]
