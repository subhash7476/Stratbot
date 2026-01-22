# core/strategies/indian_market_squeeze.py
"""
Indian Market Squeeze Strategy - 15m Timeframe
===============================================
Version: 4.0 - Profit Ratio Optimization

Features:
- Bollinger Bands + Keltner Channel squeeze detection
- Dual SuperTrend (fast/slow) for trend confirmation
- WaveTrend oscillator for momentum
- Williams %R for exhaustion detection
- Trade outcome tracking (SL/TP hit detection)
- P&L calculation for backtesting

V4.0 Optimizations (from Gemini analysis):
- 60m trend filter to avoid counter-trend trades
- Increased SL multiplier (1.6x ATR) to prevent stop hunting
- Higher RR target (2.5) to offset failed squeezes
- Breakeven at 1.5R (was 1.0R) with 0.5R profit lock
- Max hold time 60 bars (was 30) for full squeeze potential
- Volume ratio 1.2x for first hour entries
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Literal, Optional, Tuple, Dict
from datetime import datetime, date, time, timedelta
SignalSide = Literal["LONG", "SHORT"]
TradeStatus = Literal["ACTIVE", "SL_HIT", "TP_HIT", "EXPIRED", "OPEN"]

# Ensure date is available at module level for type hints
__all__ = ['SqueezeSignal', 'BacktestResult', 'check_trade_outcome',
           'build_15m_signals_with_backtest', 'run_batch_scan_squeeze_15m_v2',
           'get_60m_trend_map']  # V4: 60m trend filter export


@dataclass
class SqueezeSignal:
    """Represents a single squeeze signal with trade tracking."""
    signal_type: Literal["LONG", "SHORT"]
    timestamp: pd.Timestamp
    entry_price: float
    sl_price: float
    tp_price: float
    reasons: List[str] = field(default_factory=list)
    score: float = 0.0

    # Trade outcome fields
    status: str = "ACTIVE"  # ACTIVE, SL_HIT, TP_HIT, EXPIRED, BREAKEVEN
    exit_price: float = 0.0
    exit_time: Optional[pd.Timestamp] = None
    exit_reason: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0
    bars_held: int = 0

    # New fields for session tracking
    trade_date: Optional[date] = None
    breakeven_triggered: bool = False

    # V4-ML: Enhanced failure analysis fields
    snapshot: Dict = field(default_factory=dict)  # Technical state at entry
    ml_features: Dict = field(default_factory=dict)  # ML-ready features
    failure_category: str = ""  # Why SL hit: "wick_trap", "trend_reversal", etc.
    market_regime: str = ""  # "high_vol", "low_vol", "trending", "choppy"


@dataclass
class BacktestResult:
    """Container for backtest results."""
    active_signals: List[SqueezeSignal] = field(default_factory=list)
    completed_trades: List[SqueezeSignal] = field(default_factory=list)
    skipped_signals: List[SqueezeSignal] = field(
        default_factory=list)  # New: track skipped

    # Statistics
    win_count: int = 0
    loss_count: int = 0
    breakeven_count: int = 0  # New
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0

    # Session stats
    signals_skipped_session_limit: int = 0
    signals_skipped_cooldown: int = 0
    signals_skipped_low_quality: int = 0
    signals_skipped_trend_filter: int = 0  # NEW: Counter-trend filter


def enrich_signal_with_context(
    signal: SqueezeSignal,
    df_15m: pd.DataFrame,
    df_60m: Optional[pd.DataFrame] = None,
    symbol: str = "",
) -> SqueezeSignal:
    """
    Capture rich technical context for ML analysis at signal generation.

    Args:
        signal: The squeeze signal to enrich
        df_15m: 15m OHLCV data
        df_60m: 60m data for trend context
        symbol: Stock symbol

    Returns:
        Enriched SqueezeSignal with snapshot and ml_features
    """
    try:
        signal_idx = df_15m.index.get_loc(signal.timestamp)
    except KeyError:
        return signal  # Can't enrich if timestamp not found

    # ===========================================
    # 1. CAPTURE SNAPSHOT (Raw technical values)
    # ===========================================
    snapshot = {}

    # Price and volume basics
    row = df_15m.iloc[signal_idx]
    snapshot['open'] = float(row['Open'])
    snapshot['high'] = float(row['High'])
    snapshot['low'] = float(row['Low'])
    snapshot['close'] = float(row['Close'])
    snapshot['volume'] = float(row.get('Volume', 0))

    # Volatility measures
    if signal_idx >= 20:
        recent_highs = df_15m['High'].iloc[signal_idx-20:signal_idx].max()
        recent_lows = df_15m['Low'].iloc[signal_idx-20:signal_idx].min()
        snapshot['range_20bar'] = float(recent_highs - recent_lows)
        atr_values = (df_15m['High'] - df_15m['Low']).iloc[signal_idx-14:signal_idx]
        snapshot['atr_14'] = float(atr_values.mean()) if len(atr_values) > 0 else 0

    # Calculate wick vs body ratio (stop hunt detection)
    candle_range = snapshot['high'] - snapshot['low']
    body_size = abs(snapshot['close'] - snapshot['open'])
    if candle_range > 0:
        snapshot['wick_ratio'] = float((candle_range - body_size) / candle_range)
    else:
        snapshot['wick_ratio'] = 0.0

    # Position in daily range
    if signal_idx >= 5:
        day_high = df_15m['High'].iloc[signal_idx-5:signal_idx+1].max()
        day_low = df_15m['Low'].iloc[signal_idx-5:signal_idx+1].min()
        day_range = day_high - day_low
        if day_range > 0:
            snapshot['position_in_day_range'] = float((snapshot['close'] - day_low) / day_range)
        else:
            snapshot['position_in_day_range'] = 0.5

    # ===========================================
    # 2. CAPTURE ML FEATURES (Engineered features)
    # ===========================================
    ml_features = {}

    # A. Time-based features
    ml_features['hour_of_day'] = signal.timestamp.hour
    ml_features['minute_of_hour'] = signal.timestamp.minute
    ml_features['day_of_week'] = signal.timestamp.weekday()
    ml_features['minutes_since_open'] = (signal.timestamp.hour - 9) * 60 + (signal.timestamp.minute - 15)

    # B. Multi-timeframe momentum
    if signal_idx >= 20:
        ml_features['returns_1bar'] = float(df_15m['Close'].iloc[signal_idx] / df_15m['Close'].iloc[signal_idx-1] - 1)
        ml_features['returns_5bar'] = float(df_15m['Close'].iloc[signal_idx] / df_15m['Close'].iloc[signal_idx-5] - 1)
        ml_features['returns_20bar'] = float(df_15m['Close'].iloc[signal_idx] / df_15m['Close'].iloc[signal_idx-20] - 1)

        # Volume momentum
        if 'Volume' in df_15m.columns:
            avg_volume_20 = df_15m['Volume'].iloc[signal_idx-20:signal_idx].mean()
            if avg_volume_20 > 0:
                ml_features['volume_ratio'] = float(snapshot['volume'] / avg_volume_20)

    # C. 60m trend alignment
    if df_60m is not None and not df_60m.empty:
        try:
            signal_60m_idx = df_60m.index.get_indexer([signal.timestamp], method='ffill')[0]
            if signal_60m_idx >= 50:
                ema20_60m = df_60m["Close"].iloc[:signal_60m_idx+1].ewm(span=20, adjust=False).mean().iloc[-1]
                ema50_60m = df_60m["Close"].iloc[:signal_60m_idx+1].ewm(span=50, adjust=False).mean().iloc[-1]
                price_60m = df_60m["Close"].iloc[signal_60m_idx]

                ml_features['price_vs_ema20_60m'] = float((price_60m - ema20_60m) / ema20_60m * 100)
                ml_features['ema20_vs_ema50_60m'] = float((ema20_60m - ema50_60m) / ema50_60m * 100)
                ml_features['trend_aligned'] = 1.0 if (
                    (signal.signal_type == "LONG" and price_60m > ema20_60m and ema20_60m > ema50_60m) or
                    (signal.signal_type == "SHORT" and price_60m < ema20_60m and ema20_60m < ema50_60m)
                ) else 0.0
        except:
            ml_features['trend_aligned'] = 0.0

    # D. Support/Resistance context
    if signal_idx >= 50:
        lookback = min(50, signal_idx)
        highs = df_15m['High'].iloc[signal_idx-lookback:signal_idx]
        lows = df_15m['Low'].iloc[signal_idx-lookback:signal_idx]

        recent_swing_high = highs.max()
        recent_swing_low = lows.min()

        ml_features['distance_to_swing_high'] = float((recent_swing_high - snapshot['close']) / snapshot['close'] * 100)
        ml_features['distance_to_swing_low'] = float((snapshot['close'] - recent_swing_low) / snapshot['close'] * 100)

    # E. Risk/Reward features
    risk = abs(signal.entry_price - signal.sl_price)
    reward = abs(signal.tp_price - signal.entry_price)
    if risk > 0:
        ml_features['reward_risk_ratio'] = float(reward / risk)
        ml_features['risk_as_pct'] = float(risk / signal.entry_price * 100)

    # F. Volatility regime
    if signal_idx >= 20:
        recent_vol = (df_15m['High'] - df_15m['Low']).iloc[signal_idx-20:signal_idx].std()
        long_vol = (df_15m['High'] - df_15m['Low']).iloc[max(0, signal_idx-100):signal_idx].std() if signal_idx >= 100 else recent_vol
        if long_vol > 0:
            ml_features['volatility_ratio'] = float(recent_vol / long_vol)
            ml_features['volatility_regime'] = 'high_vol' if ml_features['volatility_ratio'] > 1.5 else 'low_vol' if ml_features['volatility_ratio'] < 0.7 else 'normal'

    # Store the enriched data
    signal.snapshot = snapshot
    signal.ml_features = ml_features

    # Determine market regime
    if 'volatility_regime' in ml_features:
        signal.market_regime = ml_features['volatility_regime']

    return signal


def categorize_failure(
    signal: SqueezeSignal,
    exit_price: float,
    exit_time: pd.Timestamp,
    price_path: List[Dict],
    max_favorable: float,
    max_adverse: float,
    df_15m: pd.DataFrame,
    df_60m: Optional[pd.DataFrame] = None,
) -> str:
    """
    Categorize why a trade hit SL based on market conditions.

    Categories:
    1. "wick_trap": Stop hunt - price wick touched SL but recovered
    2. "trend_reversal": 60m trend reversed against position
    3. "volatility_spike": High volatility caused stop-out
    4. "momentum_exhaustion": No follow-through after signal
    5. "opening_bell_noise": Early morning volatility
    6. "range_bound": Price stuck in range, breakout failed
    """
    try:
        exit_idx = df_15m.index.get_loc(exit_time)
        signal_idx = df_15m.index.get_loc(signal.timestamp)
    except:
        return "unknown_failure"

    exit_bar = df_15m.iloc[exit_idx]
    initial_risk = abs(signal.entry_price - signal.sl_price)

    # ===========================================
    # 1. CHECK FOR WICK TRAP (Stop Hunt)
    # ===========================================
    if signal.signal_type == "LONG":
        if exit_bar['Low'] <= exit_price < exit_bar['Close']:
            candle_body = abs(exit_bar['Close'] - exit_bar['Open'])
            candle_range = exit_bar['High'] - exit_bar['Low']
            if candle_range > 0:
                wick_ratio = (candle_range - candle_body) / candle_range
                if wick_ratio > 0.5:
                    signal.failure_category = "wick_trap"
                    return f"Wick Trap - {wick_ratio:.1%} wick"
    elif signal.signal_type == "SHORT":
        if exit_bar['High'] >= exit_price > exit_bar['Close']:
            candle_body = abs(exit_bar['Close'] - exit_bar['Open'])
            candle_range = exit_bar['High'] - exit_bar['Low']
            if candle_range > 0:
                wick_ratio = (candle_range - candle_body) / candle_range
                if wick_ratio > 0.5:
                    signal.failure_category = "wick_trap"
                    return f"Wick Trap - {wick_ratio:.1%} wick"

    # ===========================================
    # 2. CHECK TREND REVERSAL (60m)
    # ===========================================
    if df_60m is not None and not df_60m.empty:
        try:
            entry_60m_idx = df_60m.index.get_indexer([signal.timestamp], method='ffill')[0]
            exit_60m_idx = df_60m.index.get_indexer([exit_time], method='ffill')[0]

            if entry_60m_idx >= 20 and exit_60m_idx >= 20:
                ema20_entry = df_60m['Close'].iloc[:entry_60m_idx+1].ewm(span=20, adjust=False).mean().iloc[-1]
                ema20_exit = df_60m['Close'].iloc[:exit_60m_idx+1].ewm(span=20, adjust=False).mean().iloc[-1]

                if signal.signal_type == "LONG" and ema20_exit < ema20_entry * 0.995:
                    signal.failure_category = "trend_reversal"
                    return f"Trend Reversal - 60m EMA20 dropped"
                elif signal.signal_type == "SHORT" and ema20_exit > ema20_entry * 1.005:
                    signal.failure_category = "trend_reversal"
                    return f"Trend Reversal - 60m EMA20 rose"
        except:
            pass

    # ===========================================
    # 3. CHECK MOMENTUM EXHAUSTION
    # ===========================================
    if initial_risk > 0 and max_favorable > initial_risk * 0.8:
        if max_favorable > abs(max_adverse):
            signal.failure_category = "momentum_exhaustion"
            return f"Momentum Exhaustion - reached {max_favorable:.2f} then reversed"

    # ===========================================
    # 4. CHECK OPENING BELL NOISE
    # ===========================================
    entry_hour = signal.timestamp.hour
    entry_minute = signal.timestamp.minute
    if entry_hour == 9 and entry_minute <= 45:
        signal.failure_category = "opening_bell_noise"
        return f"Opening Bell Noise - entered at {entry_hour}:{entry_minute:02d}"

    # ===========================================
    # 5. CHECK VOLATILITY SPIKE
    # ===========================================
    if len(price_path) >= 5:
        recent_moves = [abs(p['move']) for p in price_path[-5:]]
        avg_move = sum(recent_moves) / len(recent_moves)
        if initial_risk > 0 and avg_move > initial_risk * 0.5:
            signal.failure_category = "volatility_spike"
            return f"Volatility Spike - avg move {avg_move:.2f}"

    # ===========================================
    # 6. CHECK RANGE-BOUND MARKET
    # ===========================================
    if signal_idx >= 50:
        prev_bars = df_15m.iloc[signal_idx-50:signal_idx]
        range_50 = prev_bars['High'].max() - prev_bars['Low'].min()
        atr_50 = (prev_bars['High'] - prev_bars['Low']).mean()
        if atr_50 > 0 and range_50 < atr_50 * 5:
            signal.failure_category = "range_bound"
            return f"Range Bound Market"

    signal.failure_category = "unknown"
    return "Stop Loss Hit"


def get_60m_trend_map(df_60m) -> Optional[pd.Series]:
    """
    Computes trend on 60m candles using dual EMA confirmation + EMA SLOPE.
    Returns a Series with 1 (Bull), -1 (Bear), or 0 (Neutral/Choppy).

    V4-Robust Enhanced: Uses EMA20 AND EMA50 alignment PLUS EMA20 slope.
    - Bullish: Price > EMA20 AND EMA20 > EMA50 AND EMA20 is rising
    - Bearish: Price < EMA20 AND EMA20 < EMA50 AND EMA20 is falling
    - Neutral: EMAs not aligned OR EMA20 not sloping in trend direction

    Active Trend Requirement: Only take signals when EMA20 is sloping
    in the direction of the trade (rising for LONG, falling for SHORT).
    """
    # Type safety: ensure df_60m is a DataFrame
    if df_60m is None:
        return None
    if not isinstance(df_60m, pd.DataFrame):
        return None
    if df_60m.empty:
        return None

    # Dual EMA for trend confirmation
    ema20 = df_60m["Close"].ewm(span=20, adjust=False).mean()
    ema50 = df_60m["Close"].ewm(span=50, adjust=False).mean()

    # V4-ROBUST: EMA20 slope requirement
    # Rising = current EMA20 > previous bar's EMA20
    ema20_rising = ema20 > ema20.shift(1)
    ema20_falling = ema20 < ema20.shift(1)

    # Neutral by default (choppy market)
    trend = pd.Series(0, index=df_60m.index)

    # Bullish: Price > EMA20 AND EMA20 > EMA50 AND EMA20 is RISING (active uptrend)
    bullish = (df_60m["Close"] > ema20) & (ema20 > ema50) & ema20_rising
    trend[bullish] = 1

    # Bearish: Price < EMA20 AND EMA20 < EMA50 AND EMA20 is FALLING (active downtrend)
    bearish = (df_60m["Close"] < ema20) & (ema20 < ema50) & ema20_falling
    trend[bearish] = -1

    return trend


def compute_squeeze_stack(df_15m: pd.DataFrame,
                          bb_length: int = 20,
                          bb_mult: float = 1.6,
                          kc_length: int = 20,
                          kc_mult: float = 1.25,
                          use_true_range: bool = True,
                          wt_n1: int = 9,
                          wt_n2: int = 21,
                          wt_ob_level: int = 53,
                          wt_os_level: int = -53,
                          wr_fast_len: int = 8,
                          wr_slow_len: int = 34,
                          wr_ob_level: float = -15.0,
                          wr_os_level: float = -85.0,
                          require_all_conf: bool = True,
                          session_mask: Optional[pd.Series] = None,
                          # V4-ROBUST: ADX filter parameters
                          adx_period: int = 14,
                          min_adx: float = 20.0,
                          ) -> pd.DataFrame:
    """
    Re-implements Indian Market Squeeze logic on 15m candles.

    V4-Robust additions:
    - ADX filter: Only take signals when ADX > min_adx (strong trend)
    """
    df = df_15m.copy()

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    # --- ADX Calculation (Trend Strength Filter) ---
    # Squeezes release better when ADX > 20 (Strong Trend)
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    tr = np.maximum(high - low, np.maximum((high - close.shift()).abs(), (low - close.shift()).abs()))
    atr_adx = tr.rolling(adx_period).mean()

    plus_di = 100 * (plus_dm.rolling(adx_period).mean() / atr_adx)
    minus_di = 100 * (minus_dm.rolling(adx_period).mean() / atr_adx)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.rolling(adx_period).mean()

    # ADX filter: True when trend is strong enough
    adx_strong = adx >= min_adx

    # --- Bollinger Bands ---
    bb_basis = close.rolling(bb_length).mean()
    bb_dev = close.rolling(bb_length).std() * bb_mult
    bb_upper = bb_basis + bb_dev
    bb_lower = bb_basis - bb_dev

    # --- Keltner Channels ---
    if use_true_range:
        tr = np.maximum(high - low,
                        np.maximum((high - close.shift()).abs(),
                                   (low - close.shift()).abs()))
    else:
        tr = high - low
    kc_ma = close.rolling(kc_length).mean()
    kc_range_ma = tr.rolling(kc_length).mean()
    kc_upper = kc_ma + kc_range_ma * kc_mult
    kc_lower = kc_ma - kc_range_ma * kc_mult

    sqz_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)
    sqz_off = (bb_lower < kc_lower) & (bb_upper > kc_upper)
    no_sqz = ~sqz_on & ~sqz_off

    highest_high = high.rolling(kc_length).max()
    lowest_low = low.rolling(kc_length).min()
    mid = (highest_high + lowest_low) / 2.0
    sqz_mom = (close - (mid + kc_ma) / 2.0).rolling(kc_length).apply(
        lambda x: np.polyfit(range(len(x)), x, 1)[0], raw=True
    )

    mom_up = sqz_mom > 0
    mom_dn = sqz_mom < 0
    mom_rising = sqz_mom > sqz_mom.shift()
    mom_falling = sqz_mom < sqz_mom.shift()

    # --- SuperTrend ---
    def supertrend(high_s, low_s, close_s, length, mult):
        tr_local = np.maximum(high_s - low_s,
                              np.maximum((high_s - close_s.shift()).abs(),
                                         (low_s - close_s.shift()).abs()))
        atr = tr_local.rolling(length).mean()
        hl2 = (high_s + low_s) / 2.0
        basic_upper = hl2 + mult * atr
        basic_lower = hl2 - mult * atr

        final_upper = basic_upper.copy()
        final_lower = basic_lower.copy()
        trend = pd.Series(True, index=close_s.index)

        prev_fu, prev_fl, prev_t = np.nan, np.nan, True
        for i in range(len(close_s)):
            if i == 0:
                final_upper.iat[i] = basic_upper.iat[i]
                final_lower.iat[i] = basic_lower.iat[i]
                prev_fu, prev_fl = final_upper.iat[i], final_lower.iat[i]
                continue

            bu, bl = basic_upper.iat[i], basic_lower.iat[i]
            c_prev = close_s.iat[i - 1]

            fu = bu if pd.isna(
                prev_fu) or bu < prev_fu or c_prev > prev_fu else prev_fu
            fl = bl if pd.isna(
                prev_fl) or bl > prev_fl or c_prev < prev_fl else prev_fl

            final_upper.iat[i], final_lower.iat[i] = fu, fl

            c = close_s.iat[i]
            t = True if c > prev_fu else (False if c < prev_fl else prev_t)
            trend.iat[i] = t
            prev_fu, prev_fl, prev_t = fu, fl, t

        return trend, ~trend

    st_fast_bull, st_fast_bear = supertrend(high, low, close, 7, 1.5)
    st_slow_bull, st_slow_bear = supertrend(high, low, close, 11, 2.5)
    st_fast_flip_bear = st_fast_bear & st_fast_bull.shift()
    st_fast_flip_bull = st_fast_bull & st_fast_bear.shift()
    st_both_bull = st_fast_bull & st_slow_bull
    st_both_bear = st_fast_bear & st_slow_bear

    # --- WaveTrend ---
    hlc3 = (high + low + close) / 3.0
    esa = hlc3.ewm(span=wt_n1, adjust=False).mean()
    d = (hlc3 - esa).abs().ewm(span=wt_n1, adjust=False).mean()
    ci = (hlc3 - esa) / (0.015 * d).replace(0, np.nan)
    ci = ci.fillna(0)
    wt1 = ci.ewm(span=wt_n2, adjust=False).mean()
    wt2 = wt1.rolling(4).mean()
    wt_cross_up = (wt1.shift() < wt2.shift()) & (wt1 > wt2)
    wt_cross_down = (wt1.shift() > wt2.shift()) & (wt1 < wt2)
    wt_overbought = (wt1 >= wt_ob_level).fillna(False)
    wt_oversold = (wt1 <= wt_os_level).fillna(False)

    # --- Williams %R ---
    def williams_r(h, l, c, length):
        hh, ll = h.rolling(length).max(), l.rolling(length).min()
        return ((hh - c) / (hh - ll).replace(0, np.nan) * -100.0)

    wr_fast = williams_r(high, low, close, wr_fast_len)
    wr_slow = williams_r(high, low, close, wr_slow_len)
    wr_bull_exhaustion = (wr_fast >= wr_ob_level) & (
        wr_slow >= wr_ob_level) & (wr_fast < wr_fast.shift())
    wr_bear_exhaustion = (wr_fast <= wr_os_level) & (
        wr_slow <= wr_os_level) & (wr_fast > wr_fast.shift())

    # --- Session filter ---
    in_session = session_mask.reindex(df.index).fillna(False).astype(
        bool) if session_mask is not None else pd.Series(True, index=df.index)

    recent_sqz = sqz_on.shift(1) | sqz_on.shift(2) | sqz_on.shift(3)

    # --- V4-ROBUST: Candle Body Confirmation ---
    # Prevents entering on wick fakeouts - close must break through BB
    # LONG: Close must be above previous upper BB (real breakout, not just wick)
    # SHORT: Close must be below previous lower BB (real breakdown, not just wick)
    long_body_confirm = close > bb_upper.shift(1)
    short_body_confirm = close < bb_lower.shift(1)

    # Long conditions (V4-ROBUST: ADX filter + Candle Body Confirmation added)
    long_squeeze = recent_sqz & mom_up & mom_rising
    long_st = st_both_bull
    long_wt = wt_cross_up | ((wt1 > wt2) & (wt1 > wt1.shift()))
    long_entry = long_squeeze & long_st & (
        long_wt if require_all_conf else True) & ~wt_overbought & in_session & adx_strong & long_body_confirm
    long_entry = long_entry.fillna(False).astype(bool)
    long_signal = long_entry & ~long_entry.shift().fillna(False).astype(bool)

    # Short conditions (V4-ROBUST: ADX filter + Candle Body Confirmation added)
    short_squeeze = recent_sqz & mom_dn & mom_falling
    short_st = st_both_bear
    short_wt = wt_cross_down | ((wt1 < wt2) & (wt1 < wt1.shift()))
    short_entry = short_squeeze & short_st & (
        short_wt if require_all_conf else True) & ~wt_oversold & in_session & adx_strong & short_body_confirm
    short_entry = short_entry.fillna(False).astype(bool)
    short_signal = short_entry & ~short_entry.shift().fillna(False).astype(bool)

    # Exits
    long_exit = wr_bull_exhaustion | st_fast_flip_bear
    short_exit = wr_bear_exhaustion | st_fast_flip_bull

    out = pd.DataFrame(index=df.index)
    out["long_signal"] = long_signal
    out["short_signal"] = short_signal
    out["long_exit"] = long_exit
    out["short_exit"] = short_exit
    out["st_both_bull"] = st_both_bull
    out["st_both_bear"] = st_both_bear
    out["wt_cross_up"] = wt_cross_up
    out["wt_cross_down"] = wt_cross_down
    out["recent_sqz"] = recent_sqz
    out["mom_rising"] = mom_rising
    out["mom_falling"] = mom_falling
    out["wr_bull_exhaustion"] = wr_bull_exhaustion
    out["wr_bear_exhaustion"] = wr_bear_exhaustion
    # V4-ROBUST: ADX for debugging/display
    out["adx"] = adx
    out["adx_strong"] = adx_strong

    return out


def check_trade_outcome(
    df_15m: pd.DataFrame,
    signal: SqueezeSignal,
    max_bars: int = 60,
    use_breakeven: bool = True,
    breakeven_at_r: float = 1.5,
    profit_lock_r: float = 0.5,
    use_trailing: bool = False,
    trailing_atr_mult: float = 1.5,
    df_60m: Optional[pd.DataFrame] = None,  # NEW: For failure analysis
) -> SqueezeSignal:
    """
    Enhanced trade outcome checker with breakeven stop, profit locking, and ML failure analysis.
    """
    try:
        signal_idx = df_15m.index.get_loc(signal.timestamp)
    except KeyError:
        signal.status = "ACTIVE"
        return signal

    end_idx = min(signal_idx + max_bars + 1, len(df_15m))
    future_bars = df_15m.iloc[signal_idx + 1:end_idx]

    if future_bars.empty:
        signal.status = "ACTIVE"
        return signal

    # Calculate initial risk for breakeven logic
    initial_risk = abs(signal.entry_price - signal.sl_price)
    current_sl = signal.sl_price
    breakeven_triggered = False

    # ML: Track price path for failure analysis
    price_path = []
    max_favorable = 0
    max_adverse = 0

    for bar_num, (idx, bar) in enumerate(future_bars.iterrows(), 1):
        high_price = bar["High"]
        low_price = bar["Low"]
        close_price = bar["Close"]

        # ML: Track price movement
        if signal.signal_type == "LONG":
            current_move = close_price - signal.entry_price
        else:
            current_move = signal.entry_price - close_price

        price_path.append({
            'timestamp': idx,
            'move': current_move,
            'high': high_price,
            'low': low_price,
            'close': close_price
        })
        max_favorable = max(max_favorable, current_move)
        max_adverse = min(max_adverse, current_move)

        if signal.signal_type == "LONG":
            current_profit = close_price - signal.entry_price

            if use_breakeven and not breakeven_triggered:
                if current_profit >= (initial_risk * breakeven_at_r):
                    current_sl = signal.entry_price + (initial_risk * profit_lock_r)
                    breakeven_triggered = True
                    signal.breakeven_triggered = True

            if low_price <= current_sl:
                signal.status = "SL_HIT" if not breakeven_triggered else "BREAKEVEN"
                signal.exit_price = current_sl
                signal.exit_time = idx
                # ML: Categorize the failure
                if not breakeven_triggered:
                    signal.exit_reason = categorize_failure(
                        signal, current_sl, idx, price_path,
                        max_favorable, max_adverse, df_15m, df_60m
                    )
                else:
                    signal.exit_reason = "Breakeven Stop"
                signal.pnl = current_sl - signal.entry_price
                signal.pnl_pct = (signal.pnl / signal.entry_price) * 100
                signal.bars_held = bar_num
                return signal

            if high_price >= signal.tp_price:
                signal.status = "TP_HIT"
                signal.exit_price = signal.tp_price
                signal.exit_time = idx
                signal.exit_reason = "Take Profit"
                signal.pnl = signal.tp_price - signal.entry_price
                signal.pnl_pct = (signal.pnl / signal.entry_price) * 100
                signal.bars_held = bar_num
                return signal

        else:  # SHORT
            current_profit = signal.entry_price - close_price

            if use_breakeven and not breakeven_triggered:
                if current_profit >= (initial_risk * breakeven_at_r):
                    current_sl = signal.entry_price - (initial_risk * profit_lock_r)
                    breakeven_triggered = True
                    signal.breakeven_triggered = True

            if high_price >= current_sl:
                signal.status = "SL_HIT" if not breakeven_triggered else "BREAKEVEN"
                signal.exit_price = current_sl
                signal.exit_time = idx
                # ML: Categorize the failure
                if not breakeven_triggered:
                    signal.exit_reason = categorize_failure(
                        signal, current_sl, idx, price_path,
                        max_favorable, max_adverse, df_15m, df_60m
                    )
                else:
                    signal.exit_reason = "Breakeven Stop"
                signal.pnl = signal.entry_price - current_sl
                signal.pnl_pct = (signal.pnl / signal.entry_price) * 100
                signal.bars_held = bar_num
                return signal

            if low_price <= signal.tp_price:
                signal.status = "TP_HIT"
                signal.exit_price = signal.tp_price
                signal.exit_time = idx
                signal.exit_reason = "Take Profit"
                signal.pnl = signal.entry_price - signal.tp_price
                signal.pnl_pct = (signal.pnl / signal.entry_price) * 100
                signal.bars_held = bar_num
                return signal

    # Neither SL nor TP hit within max_bars - EXPIRED
    if len(future_bars) >= max_bars:
        signal.status = "EXPIRED"
        signal.exit_price = future_bars["Close"].iloc[-1]
        signal.exit_time = future_bars.index[-1]
        signal.exit_reason = "Max bars reached"
        if signal.signal_type == "LONG":
            signal.pnl = signal.exit_price - signal.entry_price
        else:
            signal.pnl = signal.entry_price - signal.exit_price
        signal.pnl_pct = (signal.pnl / signal.entry_price) * 100
        signal.bars_held = max_bars
    else:
        signal.status = "ACTIVE"

    return signal


def build_15m_signals_with_backtest(
    df_15m: pd.DataFrame,
    df_60m: Optional[pd.DataFrame] = None,  # 60m data for trend filter
    sl_mode: Literal["ATR", "PCT"] = "ATR",
    # V4-ROBUST: was 1.6, now 2.2 (avoid wick stop-outs)
    sl_atr_mult: float = 2.2,
    # V4-ROBUST: was 2.5, now 2.0 (compensate for wider SL)
    tp_rr: float = 2.0,
    sl_pct: float = 0.01,
    tp_pct: float = 0.02,
    # V4-ROBUST: was 60, now 40 (prevent expired trades)
    max_trade_bars: int = 40,
    capital_per_trade: float = 10000.0,

    # Session limit options
    max_trades_per_session: int = 1,  # KEY FIX: Limit trades per day
    cooldown_bars: int = 4,           # Bars to wait after trade closes

    # Breakeven options
    use_breakeven: bool = True,
    breakeven_at_r: float = 1.5,     # Trigger at 1.5R

    # Quality filters
    use_quality_filter: bool = True,
    min_volume_ratio: float = 0.7,
    min_score: float = 5.0,           # Only take score 5 signals

    # 60m trend filter
    use_trend_filter: bool = True,    # NEW: Filter counter-trend signals

    # Compute squeeze function (passed in)
    compute_squeeze_fn=None,
) -> BacktestResult:
    """
    Build signals WITH session limits, breakeven stops, and 60m trend filter.

    Key improvements (V4.0):
    1. Max 1 trade per stock per SESSION (trading day)
    2. Breakeven stop at 1.5R profit with 0.5R lock
    3. Quality filters for high-probability entries
    4. Cooldown between trades
    5. 60m trend filter - only trade WITH the higher timeframe trend

    Args:
        df_15m: OHLCV DataFrame with DatetimeIndex
        df_60m: Optional 60m OHLCV for trend filtering (NEW)
        sl_mode: "ATR" or "PCT"
        sl_atr_mult: ATR multiplier for SL (default 1.6)
        tp_rr: Risk:Reward ratio (default 2.5)
        sl_pct: Fixed SL percentage (if PCT mode)
        tp_pct: Fixed TP percentage (if PCT mode)
        max_trade_bars: Max bars to hold trade (default 60)
        max_trades_per_session: Max trades per day (SESSION LIMIT)
        cooldown_bars: Bars to wait after trade closes
        use_breakeven: Enable breakeven stops
        breakeven_at_r: R-multiple for breakeven trigger (default 1.5)
        use_quality_filter: Enable entry quality checks
        min_volume_ratio: Min volume vs average
        min_score: Minimum alignment score required
        use_trend_filter: Enable 60m trend filter (NEW)
        compute_squeeze_fn: Function to compute squeeze signals

    Returns:
        BacktestResult with all trade details and statistics
    """

    # # Import compute_squeeze_stack if not provided
    # if compute_squeeze_fn is None:
    #     try:
    #         from core.strategies.squeeze_momentum import compute_squeeze_stack
    #         compute_squeeze_fn = compute_squeeze_stack
    #     except ImportError:
    #         raise ImportError(
    #             "compute_squeeze_stack not found. Please provide compute_squeeze_fn.")

    sig_df = compute_squeeze_stack(df_15m)
    atr = (df_15m["High"] - df_15m["Low"]).rolling(14).mean()

    # CRITICAL FIX: Merge signal columns with price data
    # sig_df only has signal columns, we need to join with df_15m for prices
    merged_df = df_15m.copy()
    for col in sig_df.columns:
        if col not in merged_df.columns:
            merged_df[col] = sig_df[col]

    # NEW: Compute 60m trend map for trend filtering
    trend_60m = get_60m_trend_map(df_60m) if df_60m is not None else None
    trend_15m_aligned = None
    if trend_60m is not None:
        # Reindex 60m trend to match 15m timestamps (forward fill)
        trend_15m_aligned = trend_60m.reindex(df_15m.index, method='ffill')

    all_signals: List[SqueezeSignal] = []
    skipped_signals: List[SqueezeSignal] = []

    # SESSION TRACKING
    trades_per_session: Dict[date, int] = {}  # date -> count
    last_trade_exit_idx: Optional[int] = None  # For cooldown
    active_trade: Optional[SqueezeSignal] = None  # Track if we're in a trade

    # Statistics for skipped signals
    skipped_session_limit = 0
    skipped_cooldown = 0
    skipped_low_quality = 0
    skipped_low_score = 0
    skipped_trend_filter = 0  # NEW: Counter-trend signals skipped

    for ts, row in merged_df.iterrows():
        close = float(row["Close"])  # Now row has Close since we merged
        current_idx = merged_df.index.get_loc(ts)
        current_date = ts.date()

        # Get signal type - handle both boolean and numeric values
        signal_type = None
        long_sig = row.get("long_signal", False)
        short_sig = row.get("short_signal", False)

        # Convert to boolean safely
        is_long = bool(long_sig) if pd.notna(long_sig) else False
        is_short = bool(short_sig) if pd.notna(short_sig) else False

        if is_long:
            signal_type = "LONG"
        elif is_short:
            signal_type = "SHORT"
        else:
            continue  # No signal

        # Compute alignment score (FIXED: Exhaustion disqualifies, doesn't reduce score)
        score = 0.0

        # First check for exhaustion - if present, skip this signal entirely
        if signal_type == "LONG" and row.get("wr_bull_exhaustion"):
            continue  # Skip long signals with bullish exhaustion
        if signal_type == "SHORT" and row.get("wr_bear_exhaustion"):
            continue  # Skip short signals with bearish exhaustion

        # Build positive score (max 5.0)
        # FIX: Use signal_type instead of row.get("long_signal") which is only True on signal bar
        if row.get("st_both_bull") and signal_type == "LONG":
            score += 2.0
        if row.get("st_both_bear") and signal_type == "SHORT":
            score += 2.0
        if row.get("wt_cross_up") and signal_type == "LONG":
            score += 1.0
        if row.get("wt_cross_down") and signal_type == "SHORT":
            score += 1.0
        if row.get("recent_sqz"):
            score += 1.0
        if row.get("mom_rising") and signal_type == "LONG":
            score += 1.0
        if row.get("mom_falling") and signal_type == "SHORT":
            score += 1.0

        # =====================================================================
        # FILTER 1: Minimum score check
        # =====================================================================
        if score < min_score:
            skipped_low_score += 1
            continue

        # =====================================================================
        # FILTER 2: Session limit check (MAX 1 TRADE PER DAY)
        # =====================================================================
        session_trades = trades_per_session.get(current_date, 0)
        if session_trades >= max_trades_per_session:
            skipped_session_limit += 1
            continue

        # =====================================================================
        # FILTER 3: Cooldown check (wait after previous trade)
        # =====================================================================
        if last_trade_exit_idx is not None:
            bars_since_exit = current_idx - last_trade_exit_idx
            if bars_since_exit < cooldown_bars:
                skipped_cooldown += 1
                continue

        # =====================================================================
        # FILTER 4: Active trade check (no overlapping trades)
        # =====================================================================
        if active_trade is not None and active_trade.status == "ACTIVE":
            # Check if active trade has now completed
            active_trade = check_trade_outcome(
                df_15m, active_trade, max_trade_bars,
                use_breakeven=use_breakeven, breakeven_at_r=breakeven_at_r
            )
            if active_trade.status == "ACTIVE":
                # Still in trade, skip this signal
                continue
            else:
                # Trade completed, record exit
                last_trade_exit_idx = df_15m.index.get_loc(
                    active_trade.exit_time) if active_trade.exit_time else current_idx
                active_trade = None

        # =====================================================================
        # FILTER 5: Quality filter (volume, volatility, position)
        # =====================================================================
        if use_quality_filter:
            is_valid, reason = is_high_probability_entry(
                df_15m, ts, signal_type,
                min_volume_ratio=min_volume_ratio
            )
            if not is_valid:
                skipped_low_quality += 1
                continue

        # =====================================================================
        # FILTER 6: 60m TREND FILTER (NEW - avoid counter-trend trades)
        # Skip LONG signals if 60m trend is bearish (-1)
        # Skip SHORT signals if 60m trend is bullish (1)
        # This significantly reduces false breakouts during counter-trend rallies
        # =====================================================================
        if use_trend_filter and trend_15m_aligned is not None:
            current_60m_trend = trend_15m_aligned.get(ts, 0)
            if signal_type == "LONG" and current_60m_trend == -1:
                # Skip LONG if 60m is Bearish
                skipped_trend_filter += 1
                continue
            if signal_type == "SHORT" and current_60m_trend == 1:
                # Skip SHORT if 60m is Bullish
                skipped_trend_filter += 1
                continue

        # =====================================================================
        # CALCULATE SL/TP
        # =====================================================================
        if sl_mode == "ATR":
            atr_val = atr.loc[ts]
            if pd.isna(atr_val):
                continue

            if signal_type == "LONG":
                sl = close - sl_atr_mult * float(atr_val)
                risk = close - sl
                tp = close + (risk * tp_rr)
            else:  # SHORT
                sl = close + sl_atr_mult * float(atr_val)
                risk = sl - close
                tp = close - (risk * tp_rr)
        else:  # PCT mode
            if signal_type == "LONG":
                sl = close * (1 - sl_pct)
                tp = close * (1 + tp_pct)
            else:
                sl = close * (1 + sl_pct)
                tp = close * (1 - tp_pct)

        # =====================================================================
        # CREATE SIGNAL
        # =====================================================================
        signal = SqueezeSignal(
            signal_type=signal_type,
            timestamp=ts,
            entry_price=close,
            sl_price=sl,
            tp_price=tp,
            reasons=["Squeeze", "ST aligned", "WT aligned"],
            score=score,
            trade_date=current_date,
        )

        # =====================================================================
        # CHECK TRADE OUTCOME
        # =====================================================================
        signal = check_trade_outcome(
            df_15m, signal, max_trade_bars,
            use_breakeven=use_breakeven,
            breakeven_at_r=breakeven_at_r
        )

        # =====================================================================
        # UPDATE SESSION TRACKING
        # =====================================================================
        trades_per_session[current_date] = session_trades + 1

        if signal.status == "ACTIVE":
            active_trade = signal
        else:
            # Trade completed immediately or within lookforward
            if signal.exit_time:
                last_trade_exit_idx = df_15m.index.get_loc(signal.exit_time)

        all_signals.append(signal)

    # =========================================================================
    # FINALIZE: Check any remaining active trade
    # =========================================================================
    if active_trade and active_trade.status == "ACTIVE":
        active_trade = check_trade_outcome(
            df_15m, active_trade, max_trade_bars,
            use_breakeven=use_breakeven, breakeven_at_r=breakeven_at_r
        )
        # Update in list
        for i, sig in enumerate(all_signals):
            if sig.timestamp == active_trade.timestamp:
                all_signals[i] = active_trade
                break

    # =========================================================================
    # SEPARATE ACTIVE AND COMPLETED
    # =========================================================================
    active = [s for s in all_signals if s.status == "ACTIVE"]
    completed = [s for s in all_signals if s.status in [
        "SL_HIT", "TP_HIT", "EXPIRED", "BREAKEVEN"]]

    # =========================================================================
    # CALCULATE STATISTICS
    # =========================================================================
    result = BacktestResult(
        active_signals=active,
        completed_trades=completed,
        skipped_signals=skipped_signals,
        signals_skipped_session_limit=skipped_session_limit,
        signals_skipped_cooldown=skipped_cooldown,
        signals_skipped_low_quality=skipped_low_quality,
        signals_skipped_trend_filter=skipped_trend_filter,  # NEW
    )

    if completed:
        wins = [t for t in completed if t.pnl > 0]
        losses = [t for t in completed if t.pnl <= 0]
        breakevens = [t for t in completed if t.status == "BREAKEVEN"]

        result.win_count = len(wins)
        result.loss_count = len(losses)
        result.breakeven_count = len(breakevens)
        result.total_pnl = sum(t.pnl for t in completed)
        result.total_pnl_pct = sum(t.pnl_pct for t in completed)
        result.win_rate = (len(wins) / len(completed)
                           * 100) if completed else 0
        result.avg_win = (sum(t.pnl for t in wins) / len(wins)) if wins else 0
        result.avg_loss = (sum(t.pnl for t in losses) /
                           len(losses)) if losses else 0

        gross_profit = sum(t.pnl for t in wins) if wins else 0
        gross_loss = abs(sum(t.pnl for t in losses)) if losses else 0
        result.profit_factor = (
            gross_profit / gross_loss) if gross_loss > 0 else float('inf')

    return result


def is_high_probability_entry(
    df_15m: pd.DataFrame,
    timestamp: pd.Timestamp,
    signal_type: str,
    min_volume_ratio: float = 0.7,   # Default: 0.7x average
    max_volatility_ratio: float = 2.0,
    # V4-ROBUST: Skip signals before 10:00 AM
    blackout_until: time = time(10, 0),
) -> Tuple[bool, str]:
    """
    Filter out low-quality signals with enhanced checks.

    V4-Robust improvements:
    1. TIME BLACKOUT: Skip signals before 10:00 AM (opening-bell manipulation)
    2. VOLUME CHECK: Require min volume vs average
    3. VOLATILITY CHECK: Reject choppy markets
    4. POSITION CHECK: Don't buy at highs / sell at lows

    Returns:
        Tuple of (is_valid, rejection_reason)
    """
    try:
        idx = df_15m.index.get_loc(timestamp)
    except KeyError:
        return False, "timestamp_not_found"

    if idx < 20:  # Need enough history
        return False, "insufficient_history"

    # =========================================================================
    # FILTER 1: TIME-OF-DAY BLACKOUT (V4-Robust)
    # Skip signals before 10:00 AM - the 9:15-10:00 period has high noise
    # from overnight order matching and retail fakeouts
    # =========================================================================
    signal_time = timestamp.time()
    if signal_time < blackout_until:
        return False, "early_morning_volatility"

    close = df_15m["Close"].iloc[idx]

    # =========================================================================
    # FILTER 2: VOLUME CHECK
    # =========================================================================
    if "Volume" in df_15m.columns:
        current_volume = df_15m["Volume"].iloc[idx]
        avg_volume = df_15m["Volume"].iloc[idx-20:idx].mean()

        if avg_volume > 0 and current_volume < (avg_volume * min_volume_ratio):
            return False, "low_volume"

    # 2. VOLATILITY CHECK - Reject if too choppy
    recent_range = (df_15m["High"].iloc[idx-10:idx] -
                    df_15m["Low"].iloc[idx-10:idx]).mean()
    avg_range = (df_15m["High"].iloc[idx-50:idx] -
                 df_15m["Low"].iloc[idx-50:idx]).mean()

    if avg_range > 0 and recent_range > (avg_range * max_volatility_ratio):
        return False, "high_volatility"

    # 3. POSITION IN RANGE CHECK - Relaxed for squeeze reversals
    # Squeeze signals often fire after price has moved off extremes
    # Original thresholds (0.85/0.15) were too strict
    recent_high = df_15m["High"].iloc[idx-5:idx+1].max()
    recent_low = df_15m["Low"].iloc[idx-5:idx+1].min()
    range_size = recent_high - recent_low

    if range_size > 0:
        position_in_range = (close - recent_low) / range_size

        # Relaxed thresholds: only reject absolute extremes
        if signal_type == "LONG" and position_in_range > 0.95:
            return False, "buying_at_high"
        elif signal_type == "SHORT" and position_in_range < 0.05:
            return False, "selling_at_low"

    # 4. TREND CONFIRMATION (removed momentum check - squeeze is a reversal strategy)
    # For squeeze reversals, we expect:
    # - LONG signals after pullbacks (price down, then WaveTrend crosses up)
    # - SHORT signals after rallies (price up, then WaveTrend crosses down)
    # The original momentum check was backwards and rejecting best entries.

    # Instead, check that we're not in extreme trend exhaustion
    if idx >= 10:
        # Check for extreme moves that suggest blow-off top/bottom
        price_change_10bar = abs(
            df_15m["Close"].iloc[idx] - df_15m["Close"].iloc[idx-10])
        atr_10bar = (df_15m["High"].iloc[idx-10:idx] -
                     df_15m["Low"].iloc[idx-10:idx]).mean()

        # Reject if price moved > 3 ATRs in 10 bars (too extended)
        if atr_10bar > 0 and price_change_10bar > (3.0 * atr_10bar):
            return False, "overextended"

    return True, "passed"

# Convenience functions for different use cases


def build_15m_signals(
    df_15m: pd.DataFrame,
    sl_mode: Literal["ATR", "PCT"],
    sl_atr_mult: float = 2.0,
    tp_rr: float = 2.0,
    sl_pct: float = 0.01,
    tp_pct: float = 0.02,
) -> List[SqueezeSignal]:
    """
    Original function - returns all signals without outcome tracking.
    For backward compatibility.
    """
    result = build_15m_signals_with_backtest(
        df_15m,
        df_60m=None,  # No 60m filter for backward compatibility
        sl_mode=sl_mode,
        sl_atr_mult=sl_atr_mult,
        tp_rr=tp_rr,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        use_trend_filter=False,  # Disable trend filter for backward compatibility
    )
    return result.active_signals + result.completed_trades


def build_15m_signals_for_live_scan(
    df_15m: pd.DataFrame,
    sl_mode: Literal["ATR", "PCT"],
    sl_atr_mult: float = 2.0,
    tp_rr: float = 2.0,
    sl_pct: float = 0.01,
    tp_pct: float = 0.02,
    lookback_bars: int = 10,
    include_score_4: bool = False,
    debug: bool = False,
    df_60m: Optional[pd.DataFrame] = None,  # NEW V4: 60m data for trend filter
    use_trend_filter: bool = False,  # NEW V4: Enable 60m trend filtering
) -> Tuple[List[SqueezeSignal], List[SqueezeSignal]]:
    """
    For live scanning - returns (score_5_signals, score_4_signals) from recent bars.

    Args:
        df_15m: 15m OHLCV DataFrame
        sl_mode: "ATR" or "PCT"
        df_60m: Optional 60m data for trend filtering (V4)
        use_trend_filter: Enable 60m trend filter (V4)

    Returns:
        Tuple of (score_5_signals, score_4_signals)
    """
    # Only scan recent bars
    if len(df_15m) > lookback_bars + 100:
        df_scan = df_15m.iloc[-(lookback_bars + 100):]
    else:
        df_scan = df_15m

    result = build_15m_signals_with_backtest(
        df_scan,
        df_60m=df_60m,  # V4: Pass 60m data
        sl_mode=sl_mode,
        sl_atr_mult=sl_atr_mult,
        tp_rr=tp_rr,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        max_trade_bars=20,
        use_quality_filter=False,  # Disabled for live scan to see all signals
        min_score=0.0,  # Get all signals, we'll filter by score below
        use_trend_filter=use_trend_filter,  # V4: Optional trend filter
    )

    if debug:
        total_signals = len(result.active_signals) + \
            len(result.completed_trades)
        print(f"  → Total signals generated: {total_signals}")
        print(f"  → Active signals: {len(result.active_signals)}")
        print(f"  → Completed signals: {len(result.completed_trades)}")

        # Show ALL signals (active + completed) with scores
        all_sigs = result.active_signals + result.completed_trades
        if len(all_sigs) > 0:
            all_scores = [(s.timestamp.strftime('%H:%M'), s.score, s.status, s.signal_type)
                          for s in all_sigs[-20:]]  # Last 20
            print(f"  → All signals (last 20): {all_scores}")

    # Only return signals from last N bars that are still active
    cutoff_idx = len(df_15m) - lookback_bars
    recent_active = [
        s for s in result.active_signals
        if df_15m.index.get_loc(s.timestamp) >= cutoff_idx
    ]

    if debug:
        print(
            f"  → Recent active (last {lookback_bars} bars): {len(recent_active)}")
        # Show score distribution
        if len(recent_active) > 0:
            scores = [(s.timestamp.strftime('%H:%M'), s.score, s.signal_type)
                      for s in recent_active]
            print(f"  → Recent active details: {scores}")

    # Separate by score
    score_5_signals = [s for s in recent_active if s.score >= 5.0]
    score_4_signals = [s for s in recent_active if 4.0 <=
                       s.score < 5.0] if include_score_4 else []

    return score_5_signals, score_4_signals
