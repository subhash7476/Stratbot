import pandas as pd


def load_from_live_cache(db, instrument_key: str, minutes: int = 120) -> pd.DataFrame:
    df = db.con.execute("""
        SELECT timestamp, open, high, low, close, volume
        FROM live_ohlcv_cache
        WHERE instrument_key = ?
          AND timestamp >= NOW() - INTERVAL ? MINUTE
        ORDER BY timestamp
    """, [instrument_key, minutes]).df()

    if df.empty:
        return None

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    return df


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if df is None or df.empty:
        return None

    rule_map = {
        "5minute": "5T",
        "15minute": "15T",
        "60minute": "60T",
    }

    rule = rule_map.get(timeframe)
    if not rule:
        return None

    return (
        df.resample(rule)
        .agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        })
        .dropna()
    )
