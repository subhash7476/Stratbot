import pandas as pd
import duckdb
import numpy as np
import os
import sys
import glob
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.events import SignalEvent, SignalType
from core.analytics.indicators.ema import EMA
from core.analytics.indicators.atr import ATR
from core.analytics.indicators.adx import ADX
from core.analytics.pixityAI_labeler import PixityAILabeler
from core.analytics.resampler import resample_ohlcv
from core.strategies.pixityAI_batch_events import (
    compute_session_vwap, find_swing_highs, find_swing_lows, batch_generate_events,
)
from scripts.pixityAI_trainer import train_pixityAI_meta_model
import json


def load_candles_from_duckdb(
    data_dir: str,
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """Load candle data for a symbol from daily DuckDB files."""
    pattern = os.path.join(data_dir, "*.duckdb")
    files = sorted(glob.glob(pattern))

    if start_date:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        files = [f for f in files if datetime.strptime(
            os.path.basename(f).replace(".duckdb", ""), "%Y-%m-%d") >= start_dt]
    if end_date:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        files = [f for f in files if datetime.strptime(
            os.path.basename(f).replace(".duckdb", ""), "%Y-%m-%d") <= end_dt]

    print(f"Loading {len(files)} daily files for '{symbol}'...")

    frames = []
    for f in files:
        try:
            conn = duckdb.connect(f, read_only=True)
            df = conn.sql(
                f"SELECT symbol, timestamp, open, high, low, close, volume "
                f"FROM candles WHERE symbol = '{symbol}' ORDER BY timestamp"
            ).fetchdf()
            conn.close()
            if not df.empty:
                frames.append(df)
        except Exception as e:
            print(f"  Warning: skipping {os.path.basename(f)}: {e}")

    if not frames:
        print("No data found!")
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    result['timestamp'] = pd.to_datetime(result['timestamp'])
    result = result.sort_values('timestamp').reset_index(drop=True)
    print(f"Loaded {len(result):,} bars across {len(frames)} days")
    return result


def run_full_pixityAI_pipeline(
    symbol: str = "NSE_INDEX|Nifty 50",
    data_dir: str = "data/market_data/nse/candles/1m",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    timeframe: str = "1m",
    model_out: Optional[str] = None,
):
    # Derive paths
    slug = symbol.split("|")[-1].replace(" ", "").lower()
    if not model_out:
        model_out = f"core/models/pixityAI_{slug}_{timeframe}.joblib"
    
    config_path = model_out.replace(".joblib", "_config.json")
    labeled_csv = f"data/pixityAI_labeled_events_{slug}_{timeframe}.csv"

    # ── Phase 0: Load data from DuckDB ──
    print("=" * 60)
    print("Phase 0: Loading candle data from DuckDB")
    print("=" * 60)
    df_1m = load_candles_from_duckdb(data_dir, symbol, start_date, end_date)
    if df_1m.empty:
        return

    # ── Phase 0.5: Resample to target timeframe ──
    if timeframe != "1m":
        print(f"\nResampling 1m -> {timeframe}...")
        df = resample_ohlcv(df_1m, timeframe)
        print(f"Resampled to {len(df):,} bars.")
    else:
        df = df_1m

    # ── Phase 1: Batch event generation (vectorized) ──
    print("\n" + "=" * 60)
    print("Phase 1: Collecting Events (vectorized)")
    print("=" * 60)

    bar_minutes = 1
    if timeframe.endswith('m'): bar_minutes = int(timeframe[:-1])
    elif timeframe.endswith('h'): bar_minutes = int(timeframe[:-1]) * 60
    elif timeframe.endswith('d'): bar_minutes = 1440

    all_events = batch_generate_events(df, bar_minutes=bar_minutes)

    print(f"\nTotal: {len(all_events)} trade candidates.")
    if not all_events:
        print("No events found. Check your data or strategy triggers.")
        return

    # ── Phase 2: Triple-barrier labeling ──
    print("\n" + "=" * 60)
    print("Phase 2: Labeling Events (Triple-Barrier)")
    print("=" * 60)

    labeler = PixityAILabeler(sl_mult=1.0, tp_mult=2.0, time_stop_bars=12)
    labeled_df = labeler.label_events(all_events, df)

    if labeled_df.empty:
        print("Labeling failed to produce any results.")
        return

    os.makedirs("data", exist_ok=True)
    labeled_df.to_csv(labeled_csv, index=False)

    print(f"Labeled data saved to {labeled_csv}")
    print(f"Total labeled events: {len(labeled_df)}")
    print(f"\nLabel distribution:")
    dist = labeled_df['label'].value_counts().sort_index()
    for label, count in dist.items():
        pct = count / len(labeled_df) * 100
        tag = {1: "TP hit", -1: "SL hit", 0: "Time stop"}
        label_int = int(float(str(label)))
        print(f"  {label_int:+d} ({tag.get(label_int, '?')}): {count} ({pct:.1f}%)")
    if 'realized_R' in labeled_df.columns:
        print(f"\nMean realized R: {labeled_df['realized_R'].mean():.3f}")
        print(f"Median realized R: {labeled_df['realized_R'].median():.3f}")

    if 'event_type' in labeled_df.columns:
        print(f"\nBy event type:")
        for etype, grp in labeled_df.groupby('event_type'):
            mean_r = grp['realized_R'].mean() if 'realized_R' in grp.columns else 0
            tp_rate = (grp['label'] == 1).mean() * 100
            print(f"  {etype}: {len(grp)} events, TP rate={tp_rate:.1f}%, mean R={mean_r:.3f}")

    # ── Phase 3: Train meta-model ──
    print("\n" + "=" * 60)
    print("Phase 3: Training Meta-Model")
    print("=" * 60)

    train_pixityAI_meta_model(labeled_csv, model_out)

    # Save config
    config = {
        "symbol": symbol,
        "timeframe": timeframe,
        "bar_minutes": bar_minutes,
        "model_path": model_out,
        "sl_mult": 1.0,
        "tp_mult": 2.0,
        "time_stop_bars": 12,
        "cooldown_bars": 3
    }
    with open(config_path, "w") as f:
        json.dump(config, f, indent=4)

    print("\n" + "=" * 60)
    print("Pipeline Complete!")
    print(f"Model saved at: {model_out}")
    print(f"Config saved at: {config_path}")
    print(f"Labeled events: {labeled_csv}")
    print("=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description='PixityAI Full Workflow: Load DuckDB -> Events -> Labels -> Train')
    parser.add_argument(
        '--symbol', default='NSE_INDEX|Nifty 50',
        help='Symbol to train on (default: NSE_INDEX|Nifty 50)')
    parser.add_argument(
        '--data-dir', default='data/market_data/nse/candles/1m',
        help='Directory containing daily DuckDB candle files')
    parser.add_argument(
        '--start', default=None,
        help='Start date YYYY-MM-DD (default: all available)')
    parser.add_argument(
        '--end', default=None,
        help='End date YYYY-MM-DD (default: all available)')
    parser.add_argument(
        '--timeframe', default='1m',
        help='Timeframe to train on (e.g. 5m, 15m, 1h, 1d)')
    parser.add_argument(
        '--model-out', default=None,
        help='Output path for trained model (auto-generated if not provided)')
    args = parser.parse_args()

    run_full_pixityAI_pipeline(
        symbol=args.symbol,
        data_dir=args.data_dir,
        start_date=args.start,
        end_date=args.end,
        timeframe=args.timeframe,
        model_out=args.model_out,
    )
