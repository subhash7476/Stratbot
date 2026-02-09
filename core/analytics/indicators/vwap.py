"""
Volume Weighted Average Price (VWAP)
"""
import pandas as pd
import numpy as np
from datetime import datetime
import pytz
from core.analytics.indicators.base import BaseIndicator
from core.database.utils import MarketSession


class VWAP(BaseIndicator):
    def __init__(self):
        super().__init__("VWAP")

    def calculate(self, df: pd.DataFrame, anchor: str = "Session", market: str = "NSE",
                 timestamp_col: str = "timestamp", **kwargs) -> pd.DataFrame:
        """
        Calculate VWAP with anchoring support to match TradingView Pine logic.

        Args:
            df: DataFrame with OHLCV data
            anchor: Anchor period for VWAP reset ("Session", "Week", "Month", "Quarter", "Year")
            market: Market identifier for session filtering (default: "NSE")
            timestamp_col: Name of the timestamp column in the DataFrame

        Returns:
            DataFrame: Original DataFrame with added VWAP columns (vwap, aboveVWAP, belowVWAP)
        """
        if df.empty:
            result_df = df.copy()
            result_df['vwap'] = np.nan
            result_df['aboveVWAP'] = False
            result_df['belowVWAP'] = False
            return result_df

        # Create a copy to avoid modifying the original DataFrame
        result_df = df.copy()

        # Calculate HLC3 (same as Pine Script's typical price)
        hlc3 = (result_df['high'] + result_df['low'] + result_df['close']) / 3

        # Apply session filter for anchor="Session" (NSE hours only)
        if anchor == "Session":
            # Filter to only include session bars
            session_mask = result_df[timestamp_col].apply(
                lambda x: MarketSession.for_timestamp(x).contains(x)
            )
            # Set non-session values to NaN so they don't affect VWAP calculation
            hlc3 = hlc3.where(session_mask, np.nan)
            volume = result_df['volume'].where(session_mask, np.nan)
        else:
            volume = result_df['volume']

        # Calculate anchor IDs based on the specified anchor type
        anchor_ids = self._get_anchor_ids(result_df[timestamp_col], anchor, market)

        # Calculate VWAP within each anchor group
        result_df['vwap'] = self._calculate_anchored_vwap(hlc3, volume, anchor_ids)

        # Calculate above/below VWAP signals
        result_df['aboveVWAP'] = result_df['close'] > result_df['vwap']
        result_df['belowVWAP'] = result_df['close'] < result_df['vwap']

        return result_df

    def _get_anchor_ids(self, timestamps, anchor: str, market: str):
        """
        Generate anchor IDs for grouping VWAP calculations.
        Vectorized for performance.
        """
        # Convert to pandas series if not already
        if not isinstance(timestamps, pd.Series):
            timestamps = pd.Series(timestamps)
            
        # Ensure IST
        ist_timestamps = timestamps.dt.tz_localize('UTC').dt.tz_convert(MarketSession.IST) if timestamps.dt.tz is None else timestamps.dt.tz_convert(MarketSession.IST)
        
        if anchor == "Session":
            # Session date - this is tricky because it depends on market hours
            # For NSE, we can approximate with date if we know it's always intraday or handle the 9:15-3:30 range
            # A safe way is to use MarketSession.get_session_date vectorization if possible
            # For now, let's use a faster loop or vectorized date if appropriate
            return ist_timestamps.dt.date
        elif anchor == "Week":
            return ist_timestamps.dt.isocalendar().year.astype(str) + "-W" + ist_timestamps.dt.isocalendar().week.astype(str).str.zfill(2)
        elif anchor == "Month":
            return ist_timestamps.dt.year.astype(str) + "-" + ist_timestamps.dt.month.astype(str).str.zfill(2)
        elif anchor == "Quarter":
            quarters = ((ist_timestamps.dt.month - 1) // 3 + 1).astype(str)
            return ist_timestamps.dt.year.astype(str) + "-Q" + quarters
        elif anchor == "Year":
            return ist_timestamps.dt.year.astype(str)
        else:
            raise ValueError(f"Unsupported anchor type: {anchor}")

    def _calculate_anchored_vwap(self, hlc3: pd.Series, volume: pd.Series, anchor_ids: pd.Series):
        """
        Calculate VWAP with anchoring - VWAP resets at each anchor boundary.
        """
        # Ensure input types are Series
        hlc3_s = pd.Series(hlc3) if not isinstance(hlc3, pd.Series) else hlc3
        vol_s = pd.Series(volume) if not isinstance(volume, pd.Series) else volume
        
        # Create a combined series for calculation
        df_calc = pd.DataFrame({
            'hlc3': hlc3_s,
            'volume': vol_s,
            'anchor_id': anchor_ids
        })

        # Initialize the result series
        vwap_result = pd.Series(index=hlc3.index, dtype=float)

        # Group by anchor_id and calculate VWAP within each group
        for anchor_id, group in df_calc.groupby('anchor_id'):
            # Get indices for this anchor group
            group_indices = group.index

            # Extract data for this group
            group_hlc3 = group['hlc3']
            group_volume = group['volume']

            # Calculate cumulative sums within the group
            pv_cumsum = (group_hlc3 * group_volume).cumsum()
            vol_cumsum = group_volume.cumsum()

            # Calculate VWAP for this group
            group_vwap = pv_cumsum / vol_cumsum

            # Assign back to the result series
            vwap_result.loc[group_indices] = group_vwap

        return vwap_result
