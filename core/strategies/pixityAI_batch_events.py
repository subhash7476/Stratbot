import pandas as pd
import numpy as np
import logging
from typing import Optional
from core.events import SignalEvent, SignalType
from core.analytics.indicators.ema import EMA
from core.analytics.indicators.atr import ATR
from core.analytics.indicators.adx import ADX

logger = logging.getLogger(__name__)

def compute_session_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Compute intraday VWAP (or TWAP fallback for index data with no volume).
    Resets each trading day.
    """
    if isinstance(df.index, pd.DatetimeIndex):
        timestamps = df.index
    elif 'timestamp' in df.columns:
        timestamps = pd.to_datetime(df['timestamp'])
    else:
        raise ValueError("DataFrame must have a DatetimeIndex or 'timestamp' column")

    hlc3 = (df['high'] + df['low'] + df['close']) / 3
    if isinstance(timestamps, pd.DatetimeIndex):
        session_date = timestamps.date
    else:
        session_date = timestamps.dt.date
    
    has_volume = df['volume'].sum() > 0

    if has_volume:
        pv = hlc3 * df['volume']
        cum_pv = pv.groupby(session_date).cumsum()
        cum_vol = df['volume'].groupby(session_date).cumsum()
        return cum_pv / cum_vol
    else:
        # TWAP fallback: expanding mean of HLC3 within each session
        return hlc3.groupby(session_date).expanding().mean().droplevel(0)

def find_swing_highs(highs: pd.Series, period: int = 5) -> pd.Series:
    """Detect swing highs and forward-fill the last known value."""
    result = pd.Series(np.nan, index=highs.index)
    for i in range(period, len(highs) - period):
        window = highs.iloc[i - period: i + period + 1]
        if highs.iloc[i] == window.max():
            result.iloc[i] = highs.iloc[i]
    return result.ffill()

def find_swing_lows(lows: pd.Series, period: int = 5) -> pd.Series:
    """Detect swing lows and forward-fill the last known value."""
    result = pd.Series(np.nan, index=lows.index)
    for i in range(period, len(lows) - period):
        window = lows.iloc[i - period: i + period + 1]
        if lows.iloc[i] == window.min():
            result.iloc[i] = lows.iloc[i]
    return result.ffill()

def batch_generate_events(
    df: pd.DataFrame,
    swing_period: int = 5,
    reversion_k: float = 2.0,
    time_stop_bars: int = 12,
    bar_minutes: int = 1,
) -> list:
    """
    Vectorized event generation — same logic as PixityAIEventGenerator.process_bar
    but computed once on the full DataFrame.
    """
    logger.debug("  Computing indicators...")
    df = df.copy()
    df['ema20'] = EMA(20).calculate(df)
    df['ema50'] = EMA(50).calculate(df)
    df['atr'] = ATR(14).calculate(df)
    df['adx'] = ADX(14).calculate(df)
    df['vwap'] = compute_session_vwap(df)

    # Volume z-score (rolling 100-bar); 0 for index data with no volume
    if df['volume'].sum() > 0:
        vol_mean = df['volume'].rolling(100, min_periods=20).mean()
        vol_std = df['volume'].rolling(100, min_periods=20).std()
        df['vol_z'] = ((df['volume'] - vol_mean) / vol_std).fillna(0)
    else:
        df['vol_z'] = 0.0

    # Previous bar values
    df['prev_close'] = df['close'].shift(1)
    df['prev_ema20'] = df['ema20'].shift(1)

    # Swing highs and lows
    logger.debug("  Computing swing points...")
    df['swing_high'] = find_swing_highs(df['high'], swing_period)
    df['swing_low'] = find_swing_lows(df['low'], swing_period)

    # Need at least 50 bars for EMA50 warmup
    df = df.iloc[50:].copy()

    # ── Trend LONG: close > vwap AND ema20 > ema50 AND prev_close <= swing_high < close
    trend_long = (
        (df['close'] > df['vwap']) &
        (df['ema20'] > df['ema50']) &
        (df['prev_close'] <= df['swing_high']) &
        (df['swing_high'] < df['close']) &
        df['swing_high'].notna()
    )

    # ── Trend SHORT: close < vwap AND ema20 < ema50 AND prev_close >= swing_low > close
    trend_short = (
        (df['close'] < df['vwap']) &
        (df['ema20'] < df['ema50']) &
        (df['prev_close'] >= df['swing_low']) &
        (df['swing_low'] > df['close']) &
        (df['swing_low'].notna())
    )

    # ── Reversion: adx < 25, price crossing VWAP bands
    lower_band = df['vwap'] - (reversion_k * df['atr'])
    upper_band = df['vwap'] + (reversion_k * df['atr'])

    rev_long = (
        (df['adx'] < 25) &
        (df['prev_close'] < lower_band) &
        (df['close'] >= lower_band)
    )

    rev_short = (
        (df['adx'] < 25) &
        (df['prev_close'] > upper_band) &
        (df['close'] <= upper_band)
    )

    # Build SignalEvent objects
    events = []

    def make_event(timestamp, row, signal_type, event_type):
        metadata = {
            "vwap_dist": (row['close'] - row['vwap']) / row['vwap'] if row['vwap'] else 0.0,
            "ema_slope": (row['ema20'] - row['prev_ema20']) / row['prev_ema20'] if row['prev_ema20'] else 0.0,
            "atr_pct": row['atr'] / row['close'] if row['atr'] else 0.0,
            "adx": row['adx'] if row['adx'] else 0.0,
            "hour": float(timestamp.hour),
            "minute": float(timestamp.minute),
            "vol_z": row['vol_z'],
            "event_type": event_type,
            "side": signal_type.value,
            "entry_price_basis": "next_open",
            "entry_price_at_event": row['close'],
            "atr_at_event": row['atr'],
            "h_bars": time_stop_bars,
            "bar_minutes": bar_minutes,
        }
        return SignalEvent(
            strategy_id="pixityAI_generator",
            symbol=row['symbol'] if 'symbol' in row else "UNKNOWN",
            timestamp=timestamp,
            signal_type=signal_type,
            confidence=0.5,
            metadata=metadata,
        )

    logger.debug("  Scanning for events...")
    for idx, row in df[trend_long].iterrows():
        ts = row['timestamp'] if 'timestamp' in row else idx
        events.append(make_event(ts, row, SignalType.BUY, "TREND"))
    for idx, row in df[trend_short].iterrows():
        ts = row['timestamp'] if 'timestamp' in row else idx
        events.append(make_event(ts, row, SignalType.SELL, "TREND"))
    for idx, row in df[rev_long].iterrows():
        ts = row['timestamp'] if 'timestamp' in row else idx
        events.append(make_event(ts, row, SignalType.BUY, "REVERSION"))
    for idx, row in df[rev_short].iterrows():
        ts = row['timestamp'] if 'timestamp' in row else idx
        events.append(make_event(ts, row, SignalType.SELL, "REVERSION"))

    # Sort by timestamp
    events.sort(key=lambda e: e.timestamp)

    logger.debug(f"  Trend LONG: {trend_long.sum()}, Trend SHORT: {trend_short.sum()}")
    logger.debug(f"  Reversion LONG: {rev_long.sum()}, Reversion SHORT: {rev_short.sum()}")

    return events


def batch_generate_events_with_quality_filter(
    df: pd.DataFrame,
    config_path: str = "core/models/signal_quality_config.json",
    swing_period: int = 5,
    reversion_k: float = 2.0,
    time_stop_bars: int = 12,
    bar_minutes: int = 1,
) -> tuple[list, dict]:
    """
    Generate events with signal quality filtering.

    Wrapper around batch_generate_events() that applies the modular filter pipeline.

    Args:
        df: OHLCV DataFrame
        config_path: Path to signal quality config
        swing_period: Swing detection period
        reversion_k: ATR multiplier for reversion bands
        time_stop_bars: Time-based exit bars
        bar_minutes: Bar timeframe in minutes

    Returns:
        Tuple of (filtered_events, filter_stats)
    """
    from core.filters.pipeline import SignalQualityPipeline
    from core.filters.models import FilterContext
    import core.filters.kalman_filter  # Register Kalman filter

    logger.info("Generating raw events...")
    raw_events = batch_generate_events(
        df=df,
        swing_period=swing_period,
        reversion_k=reversion_k,
        time_stop_bars=time_stop_bars,
        bar_minutes=bar_minutes
    )

    logger.info(f"Generated {len(raw_events)} raw events")

    # Load filter pipeline
    try:
        pipeline = SignalQualityPipeline.from_config(config_path)
    except FileNotFoundError:
        logger.warning(f"Config not found: {config_path}, using passthrough pipeline")
        pipeline = SignalQualityPipeline(filters=[], enabled=False)

    if not pipeline.enabled or len(pipeline.filters) == 0:
        logger.info("Filter pipeline disabled, returning all raw events")
        return raw_events, {"mode": "passthrough", "raw_count": len(raw_events)}

    # Initialize filters with historical data
    logger.info(f"Initializing {len(pipeline.filters)} filters...")
    pipeline.initialize(df)

    # Filter each event
    logger.info(f"Filtering {len(raw_events)} events through pipeline...")
    filtered_events = []
    rejection_reasons = {}

    for event in raw_events:
        # Get lookback window for this event
        event_time = event.timestamp

        # Find the index position for this event timestamp
        if isinstance(df.index, pd.DatetimeIndex):
            event_idx = df.index.get_indexer([event_time], method='nearest')[0]
        else:
            # DataFrame has integer index with 'timestamp' column
            timestamps = pd.to_datetime(df['timestamp'])
            time_diffs = (timestamps - pd.Timestamp(event_time)).abs()
            event_idx = time_diffs.argmin()

        # Get recent bars (100 bars lookback for filter context)
        lookback_bars = 100
        start_idx = max(0, event_idx - lookback_bars + 1)
        recent_bars = df.iloc[start_idx:event_idx + 1].copy()

        if len(recent_bars) < 20:  # Minimum data requirement
            logger.debug(f"Skipping event at {event_time}: insufficient lookback data")
            rejection_reasons["insufficient_data"] = rejection_reasons.get("insufficient_data", 0) + 1
            continue

        # Build filter context
        context = FilterContext(
            signal=event,
            symbol=event.symbol,
            current_price=event.metadata.get('entry_price_at_event', recent_bars['close'].iloc[-1]),
            recent_bars=recent_bars,
            timestamp=pd.Timestamp(event_time),
            market_state=None
        )

        # Evaluate through pipeline
        result = pipeline.evaluate(context)

        if result.passed:
            # Add quality metadata to event
            filtered_event = SignalEvent(
                strategy_id=event.strategy_id,
                symbol=event.symbol,
                timestamp=event.timestamp,
                signal_type=event.signal_type,
                confidence=result.confidence,  # Update confidence from filter
                metadata={
                    **event.metadata,
                    "filter_passed": True,
                    "filter_confidence": result.confidence,
                    "filter_reason": result.reason,
                    "filter_metadata": result.metadata
                }
            )
            filtered_events.append(filtered_event)
            logger.debug(f"✓ ACCEPTED: {event.symbol} {event.signal_type.value} @ {event_time} | {result.reason}")
        else:
            logger.debug(f"✗ REJECTED: {event.symbol} {event.signal_type.value} @ {event_time} | {result.reason}")
            rejection_reasons[result.reason] = rejection_reasons.get(result.reason, 0) + 1

    # Get pipeline stats
    stats = pipeline.get_stats()
    stats['raw_event_count'] = len(raw_events)
    stats['filtered_event_count'] = len(filtered_events)
    stats['rejection_reasons'] = rejection_reasons

    logger.info(
        f"Filtering complete: {len(filtered_events)}/{len(raw_events)} events passed "
        f"({stats['acceptance_rate_pct']:.1f}% acceptance rate)"
    )

    return filtered_events, stats
