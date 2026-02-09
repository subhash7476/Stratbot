import pandas as pd


def resample_ohlcv(df_1m: pd.DataFrame, target_tf: str) -> pd.DataFrame:
    """Resample 1m OHLCV to any target timeframe, preserving trading integrity.

    - Groups by session date first (no overnight bars)
    - Aggregation: open=first, high=max, low=min, close=last, volume=sum
    - Supported: 5m, 15m, 30m, 1h, 4h, 1d
    - Returns 1m data unchanged if target_tf == '1m'
    - Accepts timestamp as either a column or DatetimeIndex
    - Always returns timestamp as a column (not index)
    """
    if target_tf == '1m' or not target_tf:
        return df_1m

    df = df_1m.copy()

    # Promote 'timestamp' column to DatetimeIndex if needed
    ts_was_column = False
    if not isinstance(df.index, pd.DatetimeIndex):
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.set_index('timestamp')
            ts_was_column = True
        else:
            raise ValueError("DataFrame must have a 'timestamp' column or a DatetimeIndex")

    # Map common timeframes to pandas frequency strings
    tf_map = {
        '5m': '5min',
        '15m': '15min',
        '30m': '30min',
        '1h': '60min',
        '4h': '240min',
        '1d': '1D',
    }
    freq = tf_map.get(target_tf, target_tf)

    # Aggregation rules
    agg_dict = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    }
    if 'symbol' in df.columns:
        agg_dict['symbol'] = 'first'

    def resample_session(group):
        # offset='15min' aligns bars to NSE market open (9:15, 10:15, ...)
        resampled = group.resample(freq, closed='left', label='left', offset='15min').agg(agg_dict)
        return resampled.dropna(subset=['open'])

    # Group by date to ensure bars never span overnight
    df_resampled = df.groupby(df.index.date, group_keys=False).apply(resample_session)

    # Always return timestamp as a column for downstream compatibility
    df_resampled = df_resampled.reset_index()
    if 'index' in df_resampled.columns:
        df_resampled = df_resampled.rename(columns={'index': 'timestamp'})
    # After groupby+apply, the index name may vary; ensure 'timestamp' exists
    if 'timestamp' not in df_resampled.columns:
        # The DatetimeIndex became unnamed after reset_index
        first_col = df_resampled.columns[0]
        if pd.api.types.is_datetime64_any_dtype(df_resampled[first_col]):
            df_resampled = df_resampled.rename(columns={first_col: 'timestamp'})

    return df_resampled.reset_index(drop=True)
