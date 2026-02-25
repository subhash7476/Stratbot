"""
TLP V1 Diagnostic Engine
------------------------
Computes post-trade diagnostics: MAE, MFE, and Exit Efficiency.
Loads high-resolution 1m bars from DuckDB for precise excursion analysis.
"""
import logging
from datetime import datetime
from typing import Optional, Dict, List
from pathlib import Path
import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

class DiagnosticsEngine:
    def __init__(self, candle_dir: Path):
        self.candle_dir = candle_dir

    def compute_mae_mfe(self, 
                        symbol: str, 
                        direction: str, 
                        entry_price: float, 
                        sl_distance: float,
                        entry_ts: datetime, 
                        exit_ts: datetime) -> Dict[str, float]:
        """
        Loads 1m bars between entry and exit and computes directional MAE/MFE.
        """
        null_result = {
            "mae_points": 0.0, "mfe_points": 0.0, 
            "mae_r": 0.0, "mfe_r": 0.0, 
            "theoretical_max_pnl": 0.0, "exit_efficiency": 0.0
        }
        
        if sl_distance <= 0:
            return null_result

        try:
            # 1. Determine DB file based on entry date
            date_str = entry_ts.date().isoformat()
            db_path = self.candle_dir / f"{date_str}.duckdb"
            
            if not db_path.exists():
                logger.warning(f"DuckDB file not found for diagnostic: {db_path}")
                return null_result

            # 2. Query 1m bars in inclusive window
            conn = duckdb.connect(str(db_path), read_only=True)
            query = """
                SELECT high, low, close 
                FROM candles 
                WHERE symbol = ? AND timeframe = '1m'
                  AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
            """
            df = conn.execute(query, [symbol, entry_ts, exit_ts]).df()
            conn.close()

            if df.empty:
                return null_result

            # 3. Calculate absolute excursions
            max_high = df['high'].max()
            min_low = df['low'].min()

            if direction.upper() in ("BUY", "LONG"):
                mae_points = max(0.0, entry_price - min_low)
                mfe_points = max(0.0, max_high - entry_price)
            else:  # SELL / SHORT
                mae_points = max(0.0, max_high - entry_price)
                mfe_points = max(0.0, entry_price - min_low)

            mae_r = round(mae_points / sl_distance, 4)
            mfe_r = round(mfe_points / sl_distance, 4)

            return {
                "mae_points": round(mae_points, 4),
                "mfe_points": round(mfe_points, 4),
                "mae_r": mae_r,
                "mfe_r": mfe_r
            }

        except Exception as e:
            logger.error(f"Failed to compute MAE/MFE for {symbol}: {e}")
            return null_result
