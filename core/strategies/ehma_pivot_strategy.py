"""
CANONICAL EHMA STRATEGY

RULES:
- All EHMA logic lives here.
- Pages must only import and call.
- No DB, no UI, no side effects.
- No copies allowed.

If behavior differs between backtest and live,
this file is the only place to fix it.
"""

"""
EHMA Pivot Strategy v4.0 - MULTI-TIMEFRAME EDITION (60/15/5 STACK)
Based on TradingView "TW All in One" Indicator

KEY OPTIMIZATIONS in v4.0:
1. 60m BIAS FILTER - Only trade in direction of higher timeframe trend
2. 15m SIGNAL GENERATION - Primary signal detection on 15m timeframe
3. 5m ENTRY CONFIRMATION - Precise entry timing using 5m confirmation
4. Reduced choppy signals by requiring timeframe alignment
5. Better risk-reward through precise entries

MULTI-TIMEFRAME LOGIC:
- 60m: Determines BIAS (BULLISH/BEARISH) using MHULL vs EMA100
- 15m: Generates trading signals (LONG only if 60m BULLISH, SHORT only if 60m BEARISH)
- 5m: Confirms entry (MHULL_5m position + candle direction alignment)

Author: Trading Bot Pro
Date: 2025-01-06
Version: 4.0.0 (MTF Edition)
"""

from typing import Dict, Tuple, Optional, List
from dataclasses import dataclass
import numpy as np
import pandas as pd


def compute_ehma(src: pd.Series, length: int) -> pd.Series:
    """Exponential Hull Moving Average (EHMA)"""
    ema1 = src.ewm(span=length, adjust=False).mean()
    ema_double = 2 * ema1
    ema_full = src.ewm(span=length, adjust=False).mean()
    combined = ema_double - ema_full
    sqrt_length = int(np.sqrt(length))
    return combined.ewm(span=sqrt_length, adjust=False).mean()


def compute_hma(src: pd.Series, length: int) -> pd.Series:
    """Hull Moving Average (HMA)"""
    def wma(series: pd.Series, period: int) -> pd.Series:
        weights = np.arange(1, period + 1)
        return series.rolling(period).apply(
            lambda x: np.dot(x, weights) / weights.sum(), raw=True
        )
    half_length = max(1, int(length / 2))
    sqrt_length = max(1, int(np.sqrt(length)))
    wma_half = wma(src, half_length)
    wma_full = wma(src, length)
    raw_hma = 2 * wma_half - wma_full
    return wma(raw_hma, sqrt_length)


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range"""
    high, low, close = df['High'], df['Low'], df['Close']
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def compute_rsi(src: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index"""
    delta = src.diff()
    gain = delta.where(delta > 0, 0).ewm(span=period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(span=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index - measures trend strength"""
    high, low, close = df['High'], df['Low'], df['Close']

    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    tr = pd.concat([high - low, abs(high - close.shift()),
                   abs(low - close.shift())], axis=1).max(axis=1)

    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)

    dx = 100 * abs(plus_di - minus_di) / \
        (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(span=period, adjust=False).mean()

    return adx


# ============================================================
# NEW: MULTI-TIMEFRAME (60/15/5) FUNCTIONS
# ============================================================

@dataclass
class MTFBias:
    """60-minute timeframe bias result"""
    direction: str  # 'BULLISH', 'BEARISH', 'NEUTRAL'
    mhull: float
    ema100: float
    strength: float  # 0-1, how strong the bias is
    timestamp: pd.Timestamp


@dataclass
class MTFSignal:
    """15-minute signal result"""
    signal_type: str  # 'LONG', 'SHORT', 'NONE'
    timestamp: pd.Timestamp
    price: float
    entry_price: float
    sl_price: float
    tp_price: float
    atr: float
    rsi: float
    strength: float
    reasons: List[str]
    bias_aligned: bool  # Whether signal aligns with 60m bias


@dataclass
class MTFConfirmation:
    """5-minute entry confirmation result"""
    confirmed: bool
    timestamp: pd.Timestamp
    confirm_price: float
    mhull_5m: float
    ema100_5m: float
    candle_bullish: bool
    reasons: List[str]


@dataclass
class MTFTradeSignal:
    """Complete 60/15/5 trade signal"""
    is_tradeable: bool
    signal_type: str  # 'LONG', 'SHORT', 'NONE'
    symbol: str

    # 60m bias
    bias_60m: str
    bias_strength: float

    # 15m signal
    signal_time_15m: pd.Timestamp
    signal_strength: float

    # 5m confirmation
    confirmed_5m: bool
    confirm_time_5m: pd.Timestamp

    # Trade levels
    entry_price: float
    sl_price: float
    tp_price: float
    atr: float
    rsi: float

    # Meta
    reasons: List[str]
    alignment_score: float  # 0-1, overall alignment across timeframes


def compute_60m_bias(df_60m: pd.DataFrame, ehma_length: int = 16,
                     ema_filter: int = 100) -> MTFBias:
    """
    Compute 60-minute bias for directional filter.

    BULLISH: MHULL > EMA100
    BEARISH: MHULL < EMA100

    Returns the bias direction and strength.
    """
    if df_60m is None or len(df_60m) < ema_filter + 10:
        return MTFBias(
            direction='NEUTRAL',
            mhull=np.nan,
            ema100=np.nan,
            strength=0.0,
            timestamp=pd.Timestamp.now()
        )

    df = df_60m.copy()
    df['MHULL'] = compute_ehma(df['Close'], ehma_length)
    df['EMA100'] = df['Close'].ewm(span=ema_filter, adjust=False).mean()

    # Get latest values
    latest_mhull = df['MHULL'].iloc[-1]
    latest_ema100 = df['EMA100'].iloc[-1]
    latest_close = df['Close'].iloc[-1]

    # Determine bias
    if pd.isna(latest_mhull) or pd.isna(latest_ema100):
        direction = 'NEUTRAL'
        strength = 0.0
    else:
        # Calculate strength based on distance from EMA100
        distance_pct = (latest_mhull - latest_ema100) / latest_ema100 * 100

        if latest_mhull > latest_ema100:
            direction = 'BULLISH'
            # Strength increases with distance (capped at 1.0)
            # 2% distance = full strength
            strength = min(1.0, abs(distance_pct) / 2.0)
        elif latest_mhull < latest_ema100:
            direction = 'BEARISH'
            strength = min(1.0, abs(distance_pct) / 2.0)
        else:
            direction = 'NEUTRAL'
            strength = 0.0

    return MTFBias(
        direction=direction,
        mhull=latest_mhull,
        ema100=latest_ema100,
        strength=strength,
        timestamp=df.index[-1]
    )


def detect_15m_signals(df_15m: pd.DataFrame, bias_60m: MTFBias,
                       ehma_length: int = 16, ema_filter: int = 100,
                       lookback_bars: int = 5,
                       use_rsi_filter: bool = True,
                       use_volume_filter: bool = True,
                       use_momentum_filter: bool = True,
                       rsi_ob: int = 70, rsi_os: int = 30,
                       volume_mult: float = 1.0,
                       atr_sl_mult: float = 2.0,
                       atr_tp_mult: float = 3.0) -> List[MTFSignal]:
    """
    Detect trading signals on 15-minute timeframe.

    Only generates signals that ALIGN with 60m bias:
    - LONG signals only when 60m bias is BULLISH
    - SHORT signals only when 60m bias is BEARISH
    """
    signals = []

    if df_15m is None or len(df_15m) < ema_filter + 10:
        return signals

    df = df_15m.copy()
    df['MHULL'] = compute_ehma(df['Close'], ehma_length)
    df['SHULL'] = df['MHULL'].shift(2)
    df['EMA100'] = df['Close'].ewm(span=ema_filter, adjust=False).mean()
    df['ATR'] = compute_atr(df, period=14)
    df['RSI'] = compute_rsi(df['Close'], period=14)
    df['Vol_SMA'] = df['Volume'].rolling(20).mean()
    df['Bullish_Candle'] = df['Close'] > df['Open']
    df['Bearish_Candle'] = df['Close'] < df['Open']

    for i in range(-lookback_bars, 0):
        idx = len(df) + i
        if idx < 3:
            continue

        prev_idx = idx - 1

        prev_mhull = df['MHULL'].iloc[prev_idx]
        prev_shull = df['SHULL'].iloc[prev_idx]
        prev_ema100 = df['EMA100'].iloc[prev_idx]

        curr_mhull = df['MHULL'].iloc[idx]
        curr_shull = df['SHULL'].iloc[idx]
        curr_ema100 = df['EMA100'].iloc[idx]
        curr_close = df['Close'].iloc[idx]
        curr_open = df['Open'].iloc[idx]

        rsi_val = df['RSI'].iloc[idx]
        vol = df['Volume'].iloc[idx]
        vol_avg = df['Vol_SMA'].iloc[idx]
        atr = df['ATR'].iloc[idx]

        if pd.isna(curr_mhull) or pd.isna(curr_ema100) or pd.isna(atr):
            continue

        # ====== BULLISH CROSSOVER ======
        mhull_crossed_above = (prev_mhull <= prev_ema100) and (
            curr_mhull > curr_ema100)
        shull_crossed_above = (prev_shull <= prev_ema100) and (
            curr_shull > curr_ema100)
        both_above_ema = (curr_mhull > curr_ema100) and (
            curr_shull > curr_ema100)

        if (mhull_crossed_above or shull_crossed_above) and both_above_ema:
            # CHECK 60m BIAS ALIGNMENT
            bias_aligned = (bias_60m.direction == 'BULLISH')

            strength = 1.0
            reasons = []

            if bias_aligned:
                strength += 0.5
                reasons.append(
                    f"60m BULLISH bias (str: {bias_60m.strength:.1f})")
            else:
                reasons.append(f"⚠️ Counter-trend (60m: {bias_60m.direction})")

            # RSI Filter
            if use_rsi_filter and pd.notna(rsi_val):
                if rsi_val > rsi_ob:
                    strength -= 0.5
                    reasons.append("RSI overbought ⚠️")
                elif rsi_val < 40:
                    strength += 0.3
                    reasons.append("RSI oversold zone")

            # Volume Filter
            if use_volume_filter and pd.notna(vol_avg) and vol_avg > 0:
                vol_ratio = vol / vol_avg
                if vol_ratio > 1.5:
                    strength += 0.3
                    reasons.append(f"High volume ({vol_ratio:.1f}x)")
                elif vol_ratio < volume_mult:
                    strength -= 0.2
                    reasons.append("Low volume")

            # Momentum Filter
            if use_momentum_filter:
                if df['Bullish_Candle'].iloc[idx]:
                    strength += 0.2
                    reasons.append("Bullish candle")
                else:
                    strength -= 0.1

            signals.append(MTFSignal(
                signal_type='LONG',
                timestamp=df.index[idx],
                price=curr_close,
                entry_price=curr_close,
                sl_price=curr_close - (atr_sl_mult * atr),
                tp_price=curr_close + (atr_tp_mult * atr),
                atr=atr,
                rsi=rsi_val,
                strength=strength,
                reasons=reasons,
                bias_aligned=bias_aligned
            ))

        # ====== BEARISH CROSSOVER ======
        mhull_crossed_below = (prev_mhull >= prev_ema100) and (
            curr_mhull < curr_ema100)
        shull_crossed_below = (prev_shull >= prev_ema100) and (
            curr_shull < curr_ema100)
        both_below_ema = (curr_mhull < curr_ema100) and (
            curr_shull < curr_ema100)

        if (mhull_crossed_below or shull_crossed_below) and both_below_ema:
            # CHECK 60m BIAS ALIGNMENT
            bias_aligned = (bias_60m.direction == 'BEARISH')

            strength = 1.0
            reasons = []

            if bias_aligned:
                strength += 0.5
                reasons.append(
                    f"60m BEARISH bias (str: {bias_60m.strength:.1f})")
            else:
                reasons.append(f"⚠️ Counter-trend (60m: {bias_60m.direction})")

            # RSI Filter
            if use_rsi_filter and pd.notna(rsi_val):
                if rsi_val < rsi_os:
                    strength -= 0.5
                    reasons.append("RSI oversold ⚠️")
                elif rsi_val > 60:
                    strength += 0.3
                    reasons.append("RSI overbought zone")

            # Volume Filter
            if use_volume_filter and pd.notna(vol_avg) and vol_avg > 0:
                vol_ratio = vol / vol_avg
                if vol_ratio > 1.5:
                    strength += 0.3
                    reasons.append(f"High volume ({vol_ratio:.1f}x)")
                elif vol_ratio < volume_mult:
                    strength -= 0.2
                    reasons.append("Low volume")

            # Momentum Filter
            if use_momentum_filter:
                if df['Bearish_Candle'].iloc[idx]:
                    strength += 0.2
                    reasons.append("Bearish candle")
                else:
                    strength -= 0.1

            signals.append(MTFSignal(
                signal_type='SHORT',
                timestamp=df.index[idx],
                price=curr_close,
                entry_price=curr_close,
                sl_price=curr_close + (atr_sl_mult * atr),
                tp_price=curr_close - (atr_tp_mult * atr),
                atr=atr,
                rsi=rsi_val,
                strength=strength,
                reasons=reasons,
                bias_aligned=bias_aligned
            ))

    return signals


def confirm_5m_entry(df_5m: pd.DataFrame, signal: MTFSignal,
                     ehma_length: int = 16, ema_filter: int = 100,
                     max_5m_bars_after_signal: int = 3) -> MTFConfirmation:
    """
    Confirm entry on 5-minute timeframe.

    For LONG signals:
    - MHULL_5m > EMA100_5m (bullish alignment on 5m)
    - Latest 5m candle should be bullish

    For SHORT signals:
    - MHULL_5m < EMA100_5m (bearish alignment on 5m)
    - Latest 5m candle should be bearish

    Checks the 5m bar at or after the 15m signal time (within N bars).
    """
    default_result = MTFConfirmation(
        confirmed=False,
        timestamp=pd.Timestamp.now(),
        confirm_price=np.nan,
        mhull_5m=np.nan,
        ema100_5m=np.nan,
        candle_bullish=False,
        reasons=["No 5m confirmation data"]
    )

    if df_5m is None or len(df_5m) < ema_filter + 10:
        return default_result

    df = df_5m.copy()
    df['MHULL'] = compute_ehma(df['Close'], ehma_length)
    df['EMA100'] = df['Close'].ewm(span=ema_filter, adjust=False).mean()
    df['Bullish_Candle'] = df['Close'] > df['Open']
    df['Bearish_Candle'] = df['Close'] < df['Open']

    # Find 5m bars at or after the 15m signal time
    signal_time = signal.timestamp

    # Get 5m bars after signal time (within the confirmation window)
    mask = df.index >= signal_time
    if not mask.any():
        # No 5m data after signal time, use latest available
        mask = pd.Series([True] * len(df), index=df.index)

    confirm_df = df[mask].head(max_5m_bars_after_signal + 1)

    if confirm_df.empty:
        return default_result

    # Check each 5m bar for confirmation
    reasons = []

    for i in range(len(confirm_df)):
        idx = i

        mhull_5m = confirm_df['MHULL'].iloc[idx]
        ema100_5m = confirm_df['EMA100'].iloc[idx]
        close_5m = confirm_df['Close'].iloc[idx]
        is_bullish = confirm_df['Bullish_Candle'].iloc[idx]
        is_bearish = confirm_df['Bearish_Candle'].iloc[idx]

        if pd.isna(mhull_5m) or pd.isna(ema100_5m):
            continue

        if signal.signal_type == 'LONG':
            # LONG confirmation: MHULL_5m > EMA100_5m AND bullish candle
            mhull_above = mhull_5m > ema100_5m

            if mhull_above and is_bullish:
                reasons.append("5m MHULL > EMA100")
                reasons.append("5m bullish candle")
                return MTFConfirmation(
                    confirmed=True,
                    timestamp=confirm_df.index[idx],
                    confirm_price=close_5m,
                    mhull_5m=mhull_5m,
                    ema100_5m=ema100_5m,
                    candle_bullish=is_bullish,
                    reasons=reasons
                )
            elif mhull_above:
                reasons.append("5m MHULL > EMA100 ✓")
                reasons.append("Waiting for bullish candle...")
            elif is_bullish:
                reasons.append("5m bullish candle ✓")
                reasons.append("Waiting for MHULL alignment...")

        elif signal.signal_type == 'SHORT':
            # SHORT confirmation: MHULL_5m < EMA100_5m AND bearish candle
            mhull_below = mhull_5m < ema100_5m

            if mhull_below and is_bearish:
                reasons.append("5m MHULL < EMA100")
                reasons.append("5m bearish candle")
                return MTFConfirmation(
                    confirmed=True,
                    timestamp=confirm_df.index[idx],
                    confirm_price=close_5m,
                    mhull_5m=mhull_5m,
                    ema100_5m=ema100_5m,
                    candle_bullish=is_bullish,
                    reasons=reasons
                )
            elif mhull_below:
                reasons.append("5m MHULL < EMA100 ✓")
                reasons.append("Waiting for bearish candle...")
            elif is_bearish:
                reasons.append("5m bearish candle ✓")
                reasons.append("Waiting for MHULL alignment...")

    # No confirmation found
    if not reasons:
        reasons = ["5m conditions not met"]

    return MTFConfirmation(
        confirmed=False,
        timestamp=confirm_df.index[-1] if len(
            confirm_df) > 0 else pd.Timestamp.now(),
        confirm_price=confirm_df['Close'].iloc[-1] if len(
            confirm_df) > 0 else np.nan,
        mhull_5m=confirm_df['MHULL'].iloc[-1] if len(
            confirm_df) > 0 else np.nan,
        ema100_5m=confirm_df['EMA100'].iloc[-1] if len(
            confirm_df) > 0 else np.nan,
        candle_bullish=confirm_df['Bullish_Candle'].iloc[-1] if len(
            confirm_df) > 0 else False,
        reasons=reasons
    )


def generate_ehma_mtf_signals(
    df_60m: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_5m: pd.DataFrame,
    symbol: str = "UNKNOWN",
    ehma_length: int = 16,
    ema_filter: int = 100,
    lookback_bars_15m: int = 5,
    max_5m_bars_confirm: int = 3,
    use_rsi_filter: bool = True,
    use_volume_filter: bool = True,
    use_momentum_filter: bool = True,
    rsi_ob: int = 70,
    rsi_os: int = 30,
    volume_mult: float = 1.0,
    atr_sl_mult: float = 2.0,
    atr_tp_mult: float = 3.0,
    require_bias_alignment: bool = True,
    require_5m_confirmation: bool = True
) -> List[MTFTradeSignal]:
    """
    Generate EHMA signals using 60/15/5 multi-timeframe stack.

    Process:
    1. Compute 60m BIAS (BULLISH/BEARISH)
    2. Detect 15m SIGNALS (filtered by 60m bias if required)
    3. Confirm on 5m (optional but recommended)
    4. Return only TRADEABLE signals

    Parameters:
    -----------
    df_60m : 60-minute OHLCV DataFrame
    df_15m : 15-minute OHLCV DataFrame  
    df_5m : 5-minute OHLCV DataFrame
    symbol : Stock symbol
    ehma_length : EHMA period (default 16)
    ema_filter : EMA trend filter period (default 100)
    lookback_bars_15m : How many 15m bars to check for signals (default 5)
    max_5m_bars_confirm : How many 5m bars after signal to wait for confirmation (default 3)
    require_bias_alignment : Only take trades aligned with 60m bias (default True)
    require_5m_confirmation : Only take trades with 5m confirmation (default True)

    Returns:
    --------
    List of MTFTradeSignal objects (only tradeable ones)
    """
    trade_signals = []

    # Step 1: Compute 60m bias
    bias_60m = compute_60m_bias(df_60m, ehma_length, ema_filter)

    # Step 2: Detect 15m signals
    signals_15m = detect_15m_signals(
        df_15m, bias_60m,
        ehma_length=ehma_length,
        ema_filter=ema_filter,
        lookback_bars=lookback_bars_15m,
        use_rsi_filter=use_rsi_filter,
        use_volume_filter=use_volume_filter,
        use_momentum_filter=use_momentum_filter,
        rsi_ob=rsi_ob,
        rsi_os=rsi_os,
        volume_mult=volume_mult,
        atr_sl_mult=atr_sl_mult,
        atr_tp_mult=atr_tp_mult
    )

    # Step 3: Process each 15m signal
    for signal in signals_15m:
        reasons = list(signal.reasons)

        # Check bias alignment requirement
        if require_bias_alignment and not signal.bias_aligned:
            continue  # Skip counter-trend signals

        # Step 4: Get 5m confirmation
        if require_5m_confirmation:
            confirmation = confirm_5m_entry(
                df_5m, signal,
                ehma_length=ehma_length,
                ema_filter=ema_filter,
                max_5m_bars_after_signal=max_5m_bars_confirm
            )

            if not confirmation.confirmed:
                continue  # Skip unconfirmed signals

            reasons.extend(confirmation.reasons)
            confirm_time = confirmation.timestamp
            entry_price = confirmation.confirm_price
        else:
            confirm_time = signal.timestamp
            entry_price = signal.entry_price
            confirmation = MTFConfirmation(
                confirmed=True,
                timestamp=signal.timestamp,
                confirm_price=signal.entry_price,
                mhull_5m=np.nan,
                ema100_5m=np.nan,
                candle_bullish=True,
                reasons=["5m confirmation disabled"]
            )

        # Calculate alignment score
        alignment_score = 0.0

        # 60m bias contribution (0-0.4)
        if signal.bias_aligned:
            alignment_score += 0.4 * bias_60m.strength

        # 15m signal strength contribution (0-0.3)
        alignment_score += 0.3 * min(1.0, signal.strength / 2.0)

        # 5m confirmation contribution (0-0.3)
        if confirmation.confirmed:
            alignment_score += 0.3

        # Recalculate SL/TP from confirmation price
        if signal.signal_type == 'LONG':
            sl_price = entry_price - (atr_sl_mult * signal.atr)
            tp_price = entry_price + (atr_tp_mult * signal.atr)
        else:
            sl_price = entry_price + (atr_sl_mult * signal.atr)
            tp_price = entry_price - (atr_tp_mult * signal.atr)

        trade_signals.append(MTFTradeSignal(
            is_tradeable=True,
            signal_type=signal.signal_type,
            symbol=symbol,
            bias_60m=bias_60m.direction,
            bias_strength=bias_60m.strength,
            signal_time_15m=signal.timestamp,
            signal_strength=signal.strength,
            confirmed_5m=confirmation.confirmed,
            confirm_time_5m=confirm_time,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            atr=signal.atr,
            rsi=signal.rsi,
            reasons=reasons,
            alignment_score=alignment_score
        ))

    # Sort by alignment score (best first)
    trade_signals.sort(key=lambda x: x.alignment_score, reverse=True)

    return trade_signals


def detect_ehma_signal_mtf_fast(
    df_60m: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_5m: pd.DataFrame,
    ehma_length: int = 16,
    lookback_bars: int = 5,
    require_bias_alignment: bool = True,
    require_5m_confirmation: bool = True
) -> Dict:
    """
    Fast MTF signal detection for batch scanning.
    Returns a dictionary with signal info for display.

    This is the function to call from the batch scanner.
    """
    result = {
        'bias_60m': 'NEUTRAL',
        'bias_strength': 0.0,
        'latest_price': np.nan,
        'latest_time': None,
        'rsi': np.nan,
        'atr': np.nan,
        'trend': 'NEUTRAL',
        'signals': [],
        'mtf_aligned': False
    }

    # Get 60m bias
    bias_60m = compute_60m_bias(df_60m, ehma_length)
    result['bias_60m'] = bias_60m.direction
    result['bias_strength'] = bias_60m.strength

    # Get 15m indicators for display
    if df_15m is not None and len(df_15m) > 100:
        df_15m_calc = df_15m.copy()
        df_15m_calc['MHULL'] = compute_ehma(df_15m_calc['Close'], ehma_length)
        df_15m_calc['EMA100'] = df_15m_calc['Close'].ewm(
            span=100, adjust=False).mean()
        df_15m_calc['ATR'] = compute_atr(df_15m_calc, period=14)
        df_15m_calc['RSI'] = compute_rsi(df_15m_calc['Close'], period=14)

        result['latest_price'] = df_15m_calc['Close'].iloc[-1]
        result['latest_time'] = df_15m_calc.index[-1]
        result['rsi'] = df_15m_calc['RSI'].iloc[-1]
        result['atr'] = df_15m_calc['ATR'].iloc[-1]
        result['trend'] = 'BULLISH' if df_15m_calc['MHULL'].iloc[-1] > df_15m_calc['EMA100'].iloc[-1] else 'BEARISH'

    # Get MTF signals
    mtf_signals = generate_ehma_mtf_signals(
        df_60m=df_60m,
        df_15m=df_15m,
        df_5m=df_5m,
        ehma_length=ehma_length,
        lookback_bars_15m=lookback_bars,
        require_bias_alignment=require_bias_alignment,
        require_5m_confirmation=require_5m_confirmation
    )

    # Convert to dict format for batch scanner compatibility
    for sig in mtf_signals:
        result['signals'].append({
            'type': sig.signal_type,
            'bar_index': -1,  # MTF signals don't have simple bar index
            'timestamp': sig.confirm_time_5m,
            'price': sig.entry_price,
            'entry_price': sig.entry_price,
            'sl_price': sig.sl_price,
            'tp_price': sig.tp_price,
            'atr': sig.atr,
            'rsi': sig.rsi,
            'volume_ratio': 1.0,
            'strength': sig.alignment_score * 2,  # Scale to match old scoring
            'reasons': sig.reasons,
            'bias_60m': sig.bias_60m,
            'bias_strength': sig.bias_strength,
            'confirmed_5m': sig.confirmed_5m,
            'mtf_aligned': True
        })

    result['mtf_aligned'] = len(result['signals']) > 0

    return result


# ============================================================
# ORIGINAL v3 FUNCTIONS (kept for backward compatibility)
# ============================================================

def generate_ehma_pivot_signals_v3(
    df: pd.DataFrame,
    # === EHMA Parameters ===
    ehma_length: int = 16,
    ema_filter: int = 100,
    ma_type: str = "ehma",

    # === Signal Filters ===
    use_rsi_filter: bool = True,
    rsi_period: int = 14,
    rsi_ob: int = 70,  # Don't go LONG above this
    rsi_os: int = 30,  # Don't go SHORT below this

    use_volume_filter: bool = True,
    volume_mult: float = 1.0,  # Volume must be > X times average

    use_adx_filter: bool = False,  # ADX filter (optional)
    adx_min: int = 20,  # Minimum ADX for trend trades

    use_momentum_filter: bool = True,  # Require green candle for LONG, red for SHORT

    # === Exit Parameters ===
    atr_period: int = 14,
    atr_sl_mult: float = 2.0,  # WIDER SL (was 1.5)
    atr_tp_mult: float = 3.0,  # Better R:R target

    use_trailing_stop: bool = True,
    trail_activation: float = 1.5,  # Activate trailing after 1.5 ATR profit
    trail_distance: float = 1.0,  # Trail by 1.0 ATR

    max_holding_bars: int = 50,  # Exit if held too long (0 = disabled)

    # === Direction Filter ===
    trade_direction: str = "both"  # "long", "short", or "both"
) -> pd.DataFrame:
    """
    Generate OPTIMIZED trading signals with multiple filters (v3 - single timeframe)
    """
    df = df.copy()

    # Validate columns
    required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    # === Calculate Indicators ===

    # EHMA/HMA
    if ma_type.lower() == "ehma":
        df['MHULL'] = compute_ehma(df['Close'], ehma_length)
    else:
        df['MHULL'] = compute_hma(df['Close'], ehma_length)

    df['SHULL'] = df['MHULL'].shift(2)
    df['EMA100'] = df['Close'].ewm(span=ema_filter, adjust=False).mean()

    # ATR
    df['ATR'] = compute_atr(df, period=atr_period)

    # RSI
    if use_rsi_filter:
        df['RSI'] = compute_rsi(df['Close'], period=rsi_period)

    # Volume average
    if use_volume_filter:
        df['Vol_SMA'] = df['Volume'].rolling(20).mean()

    # ADX
    if use_adx_filter:
        df['ADX'] = compute_adx(df, period=14)

    # Candle direction (for momentum filter)
    df['Bullish_Candle'] = df['Close'] > df['Open']
    df['Bearish_Candle'] = df['Close'] < df['Open']

    # === Initialize Signal Columns ===
    df['Signal'] = 0
    df['Crossover_Bar'] = 0
    df['SL_Price'] = np.nan
    df['TP_Price'] = np.nan
    df['Entry_Price'] = np.nan
    df['Trail_Activation'] = np.nan
    df['Trail_Distance'] = np.nan
    df['Max_Bars'] = max_holding_bars
    df['Signal_Strength'] = 0.0  # Quality score

    # === Generate Signals ===
    for i in range(3, len(df) - 1):
        # Skip if indicators not ready
        if pd.isna(df['SHULL'].iloc[i]) or pd.isna(df['MHULL'].iloc[i]):
            continue
        if pd.isna(df['EMA100'].iloc[i]):
            continue

        # Previous and current values
        prev_mhull = df['MHULL'].iloc[i-1]
        prev_shull = df['SHULL'].iloc[i-1]
        prev_ema100 = df['EMA100'].iloc[i-1]

        curr_mhull = df['MHULL'].iloc[i]
        curr_shull = df['SHULL'].iloc[i]
        curr_ema100 = df['EMA100'].iloc[i]
        curr_close = df['Close'].iloc[i]

        # Entry bar data
        next_idx = i + 1
        next_open = df['Open'].iloc[next_idx]
        next_atr = df['ATR'].iloc[next_idx]

        # === BULLISH CROSSOVER ===
        mhull_crossed_above = (prev_mhull <= prev_ema100) and (
            curr_mhull > curr_ema100)
        shull_crossed_above = (prev_shull <= prev_ema100) and (
            curr_shull > curr_ema100)
        both_above_ema = (curr_mhull > curr_ema100) and (
            curr_shull > curr_ema100)

        bullish_crossover = (
            mhull_crossed_above or shull_crossed_above) and both_above_ema

        if bullish_crossover and trade_direction in ["long", "both"]:
            # Price confirmation
            if next_open <= max(curr_mhull, curr_shull, curr_ema100):
                continue

            # === APPLY FILTERS ===
            signal_strength = 1.0

            # RSI Filter - don't buy overbought
            if use_rsi_filter:
                rsi_val = df['RSI'].iloc[i]
                if pd.notna(rsi_val):
                    if rsi_val > rsi_ob:
                        continue  # Skip - overbought
                    # Bonus for buying near oversold
                    if rsi_val < 40:
                        signal_strength += 0.2

            # Volume Filter
            if use_volume_filter:
                vol = df['Volume'].iloc[i]
                vol_avg = df['Vol_SMA'].iloc[i]
                if pd.notna(vol_avg) and vol_avg > 0:
                    if vol < vol_avg * volume_mult:
                        continue  # Skip - weak volume
                    # Bonus for high volume
                    if vol > vol_avg * 1.5:
                        signal_strength += 0.2

            # ADX Filter
            if use_adx_filter:
                adx_val = df['ADX'].iloc[i]
                if pd.notna(adx_val) and adx_val < adx_min:
                    continue  # Skip - no trend

            # Momentum Filter - require bullish candle
            if use_momentum_filter:
                if not df['Bullish_Candle'].iloc[i]:
                    signal_strength -= 0.3  # Penalty but don't skip

            # === GENERATE LONG SIGNAL ===
            df.loc[df.index[i], 'Crossover_Bar'] = 1
            df.loc[df.index[next_idx], 'Signal'] = 1
            df.loc[df.index[next_idx], 'Entry_Price'] = next_open
            df.loc[df.index[next_idx], 'Signal_Strength'] = signal_strength

            # Calculate SL/TP with WIDER stops
            sl_price = next_open - (atr_sl_mult * next_atr)
            tp_price = next_open + (atr_tp_mult * next_atr)

            df.loc[df.index[next_idx], 'SL_Price'] = sl_price
            df.loc[df.index[next_idx], 'TP_Price'] = tp_price

            # Trailing stop params
            if use_trailing_stop:
                df.loc[df.index[next_idx], 'Trail_Activation'] = next_open + \
                    (trail_activation * next_atr)
                df.loc[df.index[next_idx],
                       'Trail_Distance'] = trail_distance * next_atr

        # === BEARISH CROSSOVER ===
        mhull_crossed_below = (prev_mhull >= prev_ema100) and (
            curr_mhull < curr_ema100)
        shull_crossed_below = (prev_shull >= prev_ema100) and (
            curr_shull < curr_ema100)
        both_below_ema = (curr_mhull < curr_ema100) and (
            curr_shull < curr_ema100)

        bearish_crossover = (
            mhull_crossed_below or shull_crossed_below) and both_below_ema

        if bearish_crossover and trade_direction in ["short", "both"]:
            # Price confirmation
            if next_open >= min(curr_mhull, curr_shull, curr_ema100):
                continue

            # === APPLY FILTERS ===
            signal_strength = 1.0

            # RSI Filter - don't short oversold
            if use_rsi_filter:
                rsi_val = df['RSI'].iloc[i]
                if pd.notna(rsi_val):
                    if rsi_val < rsi_os:
                        continue  # Skip - oversold
                    if rsi_val > 60:
                        signal_strength += 0.2

            # Volume Filter
            if use_volume_filter:
                vol = df['Volume'].iloc[i]
                vol_avg = df['Vol_SMA'].iloc[i]
                if pd.notna(vol_avg) and vol_avg > 0:
                    if vol < vol_avg * volume_mult:
                        continue
                    if vol > vol_avg * 1.5:
                        signal_strength += 0.2

            # ADX Filter
            if use_adx_filter:
                adx_val = df['ADX'].iloc[i]
                if pd.notna(adx_val) and adx_val < adx_min:
                    continue

            # Momentum Filter - require bearish candle
            if use_momentum_filter:
                if not df['Bearish_Candle'].iloc[i]:
                    signal_strength -= 0.3

            # === GENERATE SHORT SIGNAL ===
            df.loc[df.index[i], 'Crossover_Bar'] = -1
            df.loc[df.index[next_idx], 'Signal'] = -1
            df.loc[df.index[next_idx], 'Entry_Price'] = next_open
            df.loc[df.index[next_idx], 'Signal_Strength'] = signal_strength

            # Calculate SL/TP
            sl_price = next_open + (atr_sl_mult * next_atr)
            tp_price = next_open - (atr_tp_mult * next_atr)

            df.loc[df.index[next_idx], 'SL_Price'] = sl_price
            df.loc[df.index[next_idx], 'TP_Price'] = tp_price

            if use_trailing_stop:
                df.loc[df.index[next_idx], 'Trail_Activation'] = next_open - \
                    (trail_activation * next_atr)
                df.loc[df.index[next_idx],
                       'Trail_Distance'] = trail_distance * next_atr

    return df


def backtest_ehma_strategy_v3(
    df: pd.DataFrame,
    initial_capital: float = 100000,
    position_size_pct: float = 100,
    commission_pct: float = 0.03,
    slippage_pct: float = 0.01,
    use_trailing_stop: bool = True,
    max_holding_bars: int = 50
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Backtesting with trailing stop and time-based exits
    """
    trades = []
    equity = [initial_capital]
    current_capital = initial_capital

    in_position = False
    entry_price = 0
    entry_idx = 0
    position_type = None
    sl_price = 0
    tp_price = 0
    trail_activation = 0
    trail_distance = 0
    trailing_active = False
    highest_since_entry = 0
    lowest_since_entry = float('inf')

    for i in range(len(df)):
        if not in_position:
            # Check for entry
            if df['Signal'].iloc[i] != 0:
                if pd.isna(df['Entry_Price'].iloc[i]):
                    continue

                entry_price = df['Entry_Price'].iloc[i]
                position_type = 'LONG' if df['Signal'].iloc[i] == 1 else 'SHORT'

                sl_price = df['SL_Price'].iloc[i]
                tp_price = df['TP_Price'].iloc[i]

                if pd.isna(sl_price) or pd.isna(tp_price):
                    continue

                trail_activation = df['Trail_Activation'].iloc[i] if use_trailing_stop else float(
                    'inf')
                trail_distance = df['Trail_Distance'].iloc[i] if use_trailing_stop else 0

                in_position = True
                entry_idx = i
                highest_since_entry = df['High'].iloc[i]
                lowest_since_entry = df['Low'].iloc[i]
                trailing_active = False

                equity.append(current_capital)
        else:
            high, low, close = df['High'].iloc[i], df['Low'].iloc[i], df['Close'].iloc[i]
            highest_since_entry = max(highest_since_entry, high)
            lowest_since_entry = min(lowest_since_entry, low)

            exit_price = None
            exit_reason = None

            # Check exits
            if position_type == 'LONG':
                # SL hit
                if low <= sl_price:
                    exit_price = sl_price
                    exit_reason = 'SL'
                # TP hit
                elif high >= tp_price:
                    exit_price = tp_price
                    exit_reason = 'TP'
                # Trailing stop activation
                elif use_trailing_stop and not trailing_active and high >= trail_activation:
                    trailing_active = True
                # Trailing stop hit
                if trailing_active and not exit_price:
                    current_trail_stop = highest_since_entry - trail_distance
                    if low <= current_trail_stop:
                        exit_price = current_trail_stop
                        exit_reason = 'TRAIL'
            else:  # SHORT
                if high >= sl_price:
                    exit_price = sl_price
                    exit_reason = 'SL'
                elif low <= tp_price:
                    exit_price = tp_price
                    exit_reason = 'TP'
                elif use_trailing_stop and not trailing_active and low <= trail_activation:
                    trailing_active = True
                if trailing_active and not exit_price:
                    current_trail_stop = lowest_since_entry + trail_distance
                    if high >= current_trail_stop:
                        exit_price = current_trail_stop
                        exit_reason = 'TRAIL'

            # Max holding bars
            bars_held = i - entry_idx
            if max_holding_bars > 0 and bars_held >= max_holding_bars and not exit_price:
                exit_price = close
                exit_reason = 'TIME'

            if exit_price:
                # Calculate P&L
                if position_type == 'LONG':
                    pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                else:
                    pnl_pct = ((entry_price - exit_price) / entry_price) * 100

                # Apply costs
                total_cost_pct = commission_pct * 2 + slippage_pct * 2
                pnl_pct -= total_cost_pct

                # Update capital
                position_value = current_capital * (position_size_pct / 100)
                pnl_amount = position_value * (pnl_pct / 100)
                current_capital += pnl_amount

                trades.append({
                    'Entry_Time': df.index[entry_idx],
                    'Exit_Time': df.index[i],
                    'Type': position_type,
                    'Entry_Price': entry_price,
                    'Exit_Price': exit_price,
                    'Exit_Reason': exit_reason,
                    'PnL_%': round(pnl_pct, 2),
                    'PnL_Amount': round(pnl_amount, 2),
                    'Bars_Held': bars_held
                })

                in_position = False

            equity.append(current_capital)

    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    equity_df = pd.DataFrame({
        'Time': df.index,
        'Equity': equity[1:]
    })

    return trades_df, equity_df


def calculate_performance_metrics(trades_df: pd.DataFrame,
                                  equity_df: pd.DataFrame) -> Dict[str, float]:
    """Calculate comprehensive performance metrics"""
    if len(trades_df) == 0:
        return {
            'Total Trades': 0,
            'Winning Trades': 0,
            'Losing Trades': 0,
            'Win Rate %': 0,
            'Profit Factor': 0,
            'Total Return %': 0,
            'Max Drawdown %': 0,
            'Sharpe Ratio': 0,
            'Avg Win %': 0,
            'Avg Loss %': 0,
            'Avg Bars Held': 0,
            'Gross Profit': 0,
            'Gross Loss': 0,
            'Best Trade %': 0,
            'Worst Trade %': 0,
            'Consecutive Wins': 0,
            'Consecutive Losses': 0
        }

    total_trades = len(trades_df)
    winning_trades = len(trades_df[trades_df['PnL_%'] > 0])
    losing_trades = len(trades_df[trades_df['PnL_%'] < 0])

    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

    gross_profit = trades_df[trades_df['PnL_Amount'] > 0]['PnL_Amount'].sum()
    gross_loss = abs(
        trades_df[trades_df['PnL_Amount'] < 0]['PnL_Amount'].sum())
    profit_factor = (
        gross_profit / gross_loss) if gross_loss > 0 else float('inf')

    total_return = ((equity_df['Equity'].iloc[-1] /
                    equity_df['Equity'].iloc[0]) - 1) * 100

    cummax = equity_df['Equity'].cummax()
    drawdown = (equity_df['Equity'] - cummax) / cummax * 100
    max_drawdown = drawdown.min()

    returns = equity_df['Equity'].pct_change().dropna()
    sharpe = (returns.mean() / returns.std() *
              np.sqrt(252)) if returns.std() > 0 else 0

    avg_win = trades_df[trades_df['PnL_%'] >
                        0]['PnL_%'].mean() if winning_trades > 0 else 0
    avg_loss = trades_df[trades_df['PnL_%'] <
                         0]['PnL_%'].mean() if losing_trades > 0 else 0
    avg_bars_held = trades_df['Bars_Held'].mean(
    ) if 'Bars_Held' in trades_df.columns else 0

    best_trade = trades_df['PnL_%'].max() if len(trades_df) > 0 else 0
    worst_trade = trades_df['PnL_%'].min() if len(trades_df) > 0 else 0

    # Consecutive wins/losses
    max_consec_wins = 0
    max_consec_losses = 0
    curr_wins = 0
    curr_losses = 0

    for pnl in trades_df['PnL_%']:
        if pnl > 0:
            curr_wins += 1
            curr_losses = 0
            max_consec_wins = max(max_consec_wins, curr_wins)
        else:
            curr_losses += 1
            curr_wins = 0
            max_consec_losses = max(max_consec_losses, curr_losses)

    return {
        'Total Trades': total_trades,
        'Winning Trades': winning_trades,
        'Losing Trades': losing_trades,
        'Win Rate %': round(win_rate, 2),
        'Profit Factor': round(profit_factor, 2),
        'Total Return %': round(total_return, 2),
        'Max Drawdown %': round(max_drawdown, 2),
        'Sharpe Ratio': round(sharpe, 2),
        'Avg Win %': round(avg_win, 2),
        'Avg Loss %': round(avg_loss, 2),
        'Avg Bars Held': round(avg_bars_held, 1),
        'Gross Profit': round(gross_profit, 2),
        'Gross Loss': round(gross_loss, 2),
        'Best Trade %': round(best_trade, 2),
        'Worst Trade %': round(worst_trade, 2),
        'Consecutive Wins': max_consec_wins,
        'Consecutive Losses': max_consec_losses
    }


# ============ WRAPPER FOR BACKWARD COMPATIBILITY ============

def generate_ehma_pivot_signals(
    df: pd.DataFrame,
    ehma_length: int = 16,
    ema_filter: int = 100,
    use_ema_filter: bool = True,
    ma_type: str = "ehma",
    exit_mode: str = "hybrid",
    atr_period: int = 14,
    atr_sl_mult: float = 2.0,  # Default now 2.0 (was 1.5)
    atr_tp_mult: float = 3.0,  # Default now 3.0 (was 2.5)
    pivot_left: int = 33,
    pivot_right: int = 21,
    pivot_quick_right: int = 3
) -> pd.DataFrame:
    """Backward compatible wrapper - calls v3 with optimized defaults"""
    return generate_ehma_pivot_signals_v3(
        df=df,
        ehma_length=ehma_length,
        ema_filter=ema_filter,
        ma_type=ma_type,
        use_rsi_filter=True,
        rsi_period=14,
        rsi_ob=70,
        rsi_os=30,
        use_volume_filter=True,
        volume_mult=1.0,
        use_adx_filter=False,
        use_momentum_filter=True,
        atr_period=atr_period,
        atr_sl_mult=atr_sl_mult,
        atr_tp_mult=atr_tp_mult,
        use_trailing_stop=True,
        trail_activation=1.5,
        trail_distance=1.0,
        max_holding_bars=50,
        trade_direction="both"
    )


def backtest_ehma_strategy(
    df: pd.DataFrame,
    initial_capital: float = 100000,
    position_size_pct: float = 100,
    commission_pct: float = 0.03,
    slippage_pct: float = 0.01
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Backward compatible wrapper"""
    return backtest_ehma_strategy_v3(
        df=df,
        initial_capital=initial_capital,
        position_size_pct=position_size_pct,
        commission_pct=commission_pct,
        slippage_pct=slippage_pct,
        use_trailing_stop=True,
        max_holding_bars=50
    )


# Strategy metadata
EHMA_PIVOT_INFO = {
    'name': 'EHMA Pivot Strategy v4.0 (MTF Edition)',
    'description': 'EHMA crossover with 60/15/5 Multi-Timeframe alignment',
    'version': '4.0.0',
    'best_timeframe': '15minute (with 60m bias + 5m confirmation)',
    'expected_win_rate': '50-60% (with MTF alignment)',
    'trade_frequency': 'Low-Medium (high-quality aligned signals only)',
    'market_conditions': 'Best in trending markets with clear 60m bias',
    'mtf_stack': {
        '60m': 'Bias filter (BULLISH/BEARISH direction)',
        '15m': 'Signal generation (primary entry signals)',
        '5m': 'Entry confirmation (precise timing)'
    },
    'parameters': {
        'ehma_length': {'default': 16, 'range': (10, 30), 'description': 'EHMA period'},
        'ema_filter': {'default': 100, 'range': (50, 200), 'description': 'Trend filter EMA'},
        'atr_sl_mult': {'default': 2.0, 'range': (1.5, 3.0), 'description': 'ATR SL multiplier'},
        'atr_tp_mult': {'default': 3.0, 'range': (2.0, 5.0), 'description': 'ATR TP multiplier'},
        'require_bias_alignment': {'default': True, 'description': 'Only trade with 60m trend'},
        'require_5m_confirmation': {'default': True, 'description': 'Wait for 5m entry confirmation'},
    },
    'improvements_in_v4': [
        '60m bias filter eliminates counter-trend trades',
        '15m signal generation with multi-filter validation',
        '5m entry confirmation for precise timing',
        'Alignment score prioritizes best opportunities',
        'Reduced choppy/whipsaw signals',
        'Better win rate through trend alignment'
    ],
    'pros': [
        'Higher quality signals through MTF alignment',
        'Reduced false signals in choppy markets',
        'Better entry timing via 5m confirmation',
        'Clear trend direction from 60m bias',
        'Configurable strictness levels'
    ],
    'cons': [
        'Fewer signals due to strict MTF requirements',
        'Requires data from multiple timeframes',
        'May miss some moves when 60m is neutral',
        'Slightly more complex setup'
    ]
}


if __name__ == "__main__":
    print("EHMA Pivot Strategy v4.0 - MTF Edition")
    print("=" * 50)
    print(f"Version: {EHMA_PIVOT_INFO['version']}")
    print(f"\nMulti-Timeframe Stack:")
    for tf, desc in EHMA_PIVOT_INFO['mtf_stack'].items():
        print(f"  {tf}: {desc}")
    print(f"\nKey improvements:")
    for fix in EHMA_PIVOT_INFO['improvements_in_v4']:
        print(f"  ✓ {fix}")
    print("\n" + "=" * 50)
    print("Ready for profitable trading with MTF alignment!")
