# core/entry_signals.py
"""
Entry Signal Generation & Timing Module
Takes validated stocks from regime analysis and finds optimal entry points

Flow:
1. Regime detected → Stock validated → Move to Watch List
2. Monitor for entry triggers (pullbacks, extremes, volume)
3. Multi-timeframe confirmation
4. Risk/reward validation
5. Generate BUY signal with entry price, SL, TP
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional


def calculate_multi_timeframe_trend(
    df_5m: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_60m: pd.DataFrame,
    df_daily: pd.DataFrame
) -> Dict:
    """
    Analyze trend across multiple timeframes
    
    Returns:
        Dict with trend for each TF and alignment status
    """
    
    def get_trend(df: pd.DataFrame) -> str:
        """Determine trend using EMA crossover"""
        if len(df) < 50:
            return 'Unknown'
        
        ema_fast = df['Close'].ewm(span=9).mean().iloc[-1]
        ema_slow = df['Close'].ewm(span=21).mean().iloc[-1]
        
        if ema_fast > ema_slow * 1.002:  # 0.2% buffer
            return 'Bullish'
        elif ema_fast < ema_slow * 0.998:
            return 'Bearish'
        else:
            return 'Neutral'
    
    trends = {
        '5min': get_trend(df_5m),
        '15min': get_trend(df_15m),
        '60min': get_trend(df_60m),
        'daily': get_trend(df_daily)
    }
    
    # Check alignment (all bullish or all bearish)
    bullish_count = sum(1 for t in trends.values() if t == 'Bullish')
    bearish_count = sum(1 for t in trends.values() if t == 'Bearish')
    
    if bullish_count >= 3:
        alignment = 'Strong Bullish'
    elif bearish_count >= 3:
        alignment = 'Strong Bearish'
    elif bullish_count >= 2:
        alignment = 'Weak Bullish'
    elif bearish_count >= 2:
        alignment = 'Weak Bearish'
    else:
        alignment = 'Mixed'
    
    return {
        'trends': trends,
        'alignment': alignment,
        'aligned': bullish_count >= 3 or bearish_count >= 3
    }


def detect_pullback_entry(df: pd.DataFrame, regime: str = 'Trending Bullish') -> Dict:
    """
    For TRENDING regimes: Wait for pullback to value zone
    
    Entry criteria:
    - Price pulls back to 9 or 21 EMA
    - RSI cools off (40-60 range)
    - Volume dries up (lower than average)
    - Then first bounce candle with volume spike
    
    Returns:
        Dict with signal, entry price, reason
    """
    
    if len(df) < 50:
        return {'signal': False, 'reason': 'Insufficient data'}
    
    # Calculate indicators
    df = df.copy()
    df['EMA_9'] = df['Close'].ewm(span=9).mean()
    df['EMA_21'] = df['Close'].ewm(span=21).mean()
    df['RSI'] = calculate_rsi(df['Close'], period=14)
    df['Volume_MA'] = df['Volume'].rolling(20).mean()
    
    # Current values
    current_close = df['Close'].iloc[-1]
    current_rsi = df['RSI'].iloc[-1]
    current_volume = df['Volume'].iloc[-1]
    ema_9 = df['EMA_9'].iloc[-1]
    ema_21 = df['EMA_21'].iloc[-1]
    vol_ma = df['Volume_MA'].iloc[-1]
    
    # Previous values
    prev_close = df['Close'].iloc[-2]
    prev_volume = df['Volume'].iloc[-2]
    
    # Check if in pullback zone
    near_ema_9 = abs(current_close - ema_9) / current_close < 0.005  # Within 0.5%
    near_ema_21 = abs(current_close - ema_21) / current_close < 0.01  # Within 1%
    
    # Check RSI cooled off
    rsi_cooled = 40 <= current_rsi <= 60
    
    # Check for bounce (current candle green after red)
    bounced = current_close > prev_close and prev_close < df['Close'].iloc[-3]
    
    # Check volume spike (confirmation)
    volume_spike = current_volume > vol_ma * 1.3 and current_volume > prev_volume * 1.2
    
    # ENTRY SIGNAL
    if (near_ema_9 or near_ema_21) and rsi_cooled and bounced and volume_spike:
        entry_price = current_close
        stop_loss = min(ema_21, df['Low'].iloc[-5:].min()) * 0.995  # 0.5% below recent low
        target = entry_price + (entry_price - stop_loss) * 2  # 1:2 R/R
        
        return {
            'signal': True,
            'type': 'Pullback Entry',
            'entry_price': round(entry_price, 2),
            'stop_loss': round(stop_loss, 2),
            'target': round(target, 2),
            'risk_reward': 2.0,
            'reason': f'Pullback to EMA, RSI cooled ({current_rsi:.1f}), Bounce + Volume',
            'confidence': 'High'
        }
    
    # WAITING STATE
    elif (near_ema_9 or near_ema_21) and rsi_cooled:
        return {
            'signal': False,
            'state': 'WAIT',
            'reason': f'In pullback zone (RSI {current_rsi:.1f}), waiting for bounce + volume',
            'watch_for': 'Green candle with volume spike'
        }
    
    else:
        return {
            'signal': False,
            'reason': 'Not in pullback zone or no confirmation'
        }


def detect_extreme_entry(df: pd.DataFrame, regime: str = 'Ranging') -> Dict:
    """
    For RANGING/MEAN-REVERTING regimes: Enter at extremes
    
    Entry criteria:
    - RSI < 30 (oversold) or RSI > 70 (overbought)
    - Price at Bollinger Band extreme
    - Z-score > 2 standard deviations
    - Reversal candlestick pattern
    
    Returns:
        Dict with signal, entry price, reason
    """
    
    if len(df) < 50:
        return {'signal': False, 'reason': 'Insufficient data'}
    
    df = df.copy()
    df['RSI'] = calculate_rsi(df['Close'], period=14)
    
    # Bollinger Bands
    df['BB_Mid'] = df['Close'].rolling(20).mean()
    df['BB_Std'] = df['Close'].rolling(20).std()
    df['BB_Upper'] = df['BB_Mid'] + (2 * df['BB_Std'])
    df['BB_Lower'] = df['BB_Mid'] - (2 * df['BB_Std'])
    
    # Z-Score
    df['Z_Score'] = (df['Close'] - df['BB_Mid']) / df['BB_Std']
    
    current_close = df['Close'].iloc[-1]
    current_rsi = df['RSI'].iloc[-1]
    current_z = df['Z_Score'].iloc[-1]
    bb_lower = df['BB_Lower'].iloc[-1]
    bb_upper = df['BB_Upper'].iloc[-1]
    
    # Check for reversal candle (hammer, engulfing, etc.)
    reversal = detect_reversal_pattern(df.tail(3))
    
    # OVERSOLD ENTRY (for long)
    if current_rsi < 30 and current_z < -2 and current_close <= bb_lower * 1.01:
        if reversal['type'] in ['Hammer', 'Bullish Engulfing']:
            entry_price = current_close
            stop_loss = df['Low'].iloc[-5:].min() * 0.995
            target = df['BB_Mid'].iloc[-1]  # Target mean reversion
            
            return {
                'signal': True,
                'type': 'Extreme Entry (Oversold)',
                'entry_price': round(entry_price, 2),
                'stop_loss': round(stop_loss, 2),
                'target': round(target, 2),
                'risk_reward': round((target - entry_price) / (entry_price - stop_loss), 2),
                'reason': f'RSI {current_rsi:.1f}, Z-Score {current_z:.2f}, {reversal["type"]}',
                'confidence': 'Medium'
            }
    
    # OVERBOUGHT ENTRY (for short - skip if only long trading)
    # (Implementation similar to above)
    
    return {
        'signal': False,
        'reason': f'Not at extreme (RSI {current_rsi:.1f}, Z {current_z:.2f})'
    }


def detect_momentum_breakout(df: pd.DataFrame) -> Dict:
    """
    Momentum breakout entry
    
    Entry criteria:
    - MACD crossover (histogram turns positive)
    - Price breaks above recent high
    - Volume spike (1.5x average)
    - Bollinger Bands expanding (volatility breakout)
    
    Returns:
        Dict with signal, entry price, reason
    """
    
    if len(df) < 50:
        return {'signal': False, 'reason': 'Insufficient data'}
    
    df = df.copy()
    
    # MACD
    ema_12 = df['Close'].ewm(span=12).mean()
    ema_26 = df['Close'].ewm(span=26).mean()
    df['MACD'] = ema_12 - ema_26
    df['Signal_Line'] = df['MACD'].ewm(span=9).mean()
    df['MACD_Hist'] = df['MACD'] - df['Signal_Line']
    
    # Bollinger Bands width
    df['BB_Mid'] = df['Close'].rolling(20).mean()
    df['BB_Std'] = df['Close'].rolling(20).std()
    df['BB_Width'] = (df['BB_Std'] / df['BB_Mid']) * 100
    
    # Volume
    df['Volume_MA'] = df['Volume'].rolling(20).mean()
    
    # Current values
    current_close = df['Close'].iloc[-1]
    current_macd_hist = df['MACD_Hist'].iloc[-1]
    prev_macd_hist = df['MACD_Hist'].iloc[-2]
    current_volume = df['Volume'].iloc[-1]
    vol_ma = df['Volume_MA'].iloc[-1]
    bb_width_current = df['BB_Width'].iloc[-1]
    bb_width_prev = df['BB_Width'].iloc[-10:].mean()
    
    # Recent high
    recent_high = df['High'].iloc[-20:-1].max()
    
    # ENTRY CONDITIONS
    macd_crossover = current_macd_hist > 0 and prev_macd_hist <= 0
    price_breakout = current_close > recent_high
    volume_spike = current_volume > vol_ma * 1.5
    bb_expanding = bb_width_current > bb_width_prev * 1.2
    
    if macd_crossover and price_breakout and volume_spike:
        entry_price = current_close
        stop_loss = df['Low'].iloc[-10:].min()
        target = entry_price + (entry_price - stop_loss) * 2.5
        
        return {
            'signal': True,
            'type': 'Momentum Breakout',
            'entry_price': round(entry_price, 2),
            'stop_loss': round(stop_loss, 2),
            'target': round(target, 2),
            'risk_reward': 2.5,
            'reason': f'MACD cross, Price breakout above {recent_high:.2f}, Volume {current_volume/vol_ma:.1f}x',
            'confidence': 'High' if bb_expanding else 'Medium'
        }
    
    return {
        'signal': False,
        'reason': 'No momentum breakout detected'
    }


def detect_bb_squeeze_breakout(df: pd.DataFrame) -> Dict:
    """
    Bollinger Band squeeze followed by breakout
    
    Entry criteria:
    - BB width in lowest 10% of last 100 bars (squeeze)
    - Price breaks out of BB
    - Volume confirms breakout
    
    Returns:
        Dict with signal, entry price, reason
    """
    
    if len(df) < 100:
        return {'signal': False, 'reason': 'Insufficient data'}
    
    df = df.copy()
    
    # Bollinger Bands
    df['BB_Mid'] = df['Close'].rolling(20).mean()
    df['BB_Std'] = df['Close'].rolling(20).std()
    df['BB_Upper'] = df['BB_Mid'] + (2 * df['BB_Std'])
    df['BB_Lower'] = df['BB_Mid'] - (2 * df['BB_Std'])
    df['BB_Width'] = ((df['BB_Upper'] - df['BB_Lower']) / df['BB_Mid']) * 100
    
    # Volume
    df['Volume_MA'] = df['Volume'].rolling(20).mean()
    
    # Check if in squeeze
    current_width = df['BB_Width'].iloc[-1]
    width_percentile = (df['BB_Width'].iloc[-100:] < current_width).sum() / 100
    
    in_squeeze = width_percentile < 0.2  # Bottom 20%
    
    if in_squeeze:
        # Check for breakout
        current_close = df['Close'].iloc[-1]
        bb_upper = df['BB_Upper'].iloc[-1]
        bb_lower = df['BB_Lower'].iloc[-1]
        current_volume = df['Volume'].iloc[-1]
        vol_ma = df['Volume_MA'].iloc[-1]
        
        # Breakout up
        if current_close > bb_upper and current_volume > vol_ma * 1.3:
            entry_price = current_close
            stop_loss = df['BB_Mid'].iloc[-1]
            target = entry_price + (entry_price - stop_loss) * 2
            
            return {
                'signal': True,
                'type': 'BB Squeeze Breakout',
                'entry_price': round(entry_price, 2),
                'stop_loss': round(stop_loss, 2),
                'target': round(target, 2),
                'risk_reward': 2.0,
                'reason': f'Squeeze (width {current_width:.2f}%) + Breakout + Volume',
                'confidence': 'High'
            }
        
        # In squeeze but no breakout yet
        return {
            'signal': False,
            'state': 'SQUEEZE',
            'reason': f'In squeeze (width {current_width:.2f}%), waiting for breakout',
            'watch_for': 'Price breaking BB with volume'
        }
    
    return {
        'signal': False,
        'reason': 'No squeeze detected'
    }


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def detect_reversal_pattern(df: pd.DataFrame) -> Dict:
    """
    Detect candlestick reversal patterns
    
    Returns:
        Dict with pattern type and confidence
    """
    
    if len(df) < 2:
        return {'type': 'None', 'confidence': 0}
    
    # Last 2 candles
    prev = df.iloc[-2]
    curr = df.iloc[-1]
    
    prev_body = abs(prev['Close'] - prev['Open'])
    curr_body = abs(curr['Close'] - curr['Open'])
    prev_range = prev['High'] - prev['Low']
    curr_range = curr['High'] - curr['Low']
    
    # Hammer (bullish reversal)
    if (curr['Close'] > curr['Open'] and  # Green candle
        curr_body < curr_range * 0.3 and  # Small body
        (curr['Close'] - curr['Low']) > curr_body * 2):  # Long lower shadow
        return {'type': 'Hammer', 'confidence': 0.7}
    
    # Bullish Engulfing
    if (prev['Close'] < prev['Open'] and  # Prev red
        curr['Close'] > curr['Open'] and  # Curr green
        curr['Close'] > prev['Open'] and  # Engulfs prev
        curr['Open'] < prev['Close']):
        return {'type': 'Bullish Engulfing', 'confidence': 0.8}
    
    # Doji (indecision, potential reversal)
    if curr_body < curr_range * 0.1:
        return {'type': 'Doji', 'confidence': 0.5}
    
    return {'type': 'None', 'confidence': 0}


def generate_entry_signal(
    symbol: str,
    regime: str,
    df_5m: pd.DataFrame,
    df_15m: pd.DataFrame,
    df_60m: pd.DataFrame,
    df_daily: pd.DataFrame,
    strategy: str = 'auto'
) -> Dict:
    """
    Master function: Generate entry signal with multi-timeframe confirmation
    
    Args:
        symbol: Stock symbol
        regime: Current regime from GMM (e.g., 'Trending Bullish')
        df_5m, df_15m, df_60m, df_daily: OHLCV data for each timeframe
        strategy: 'auto', 'pullback', 'extreme', 'momentum', 'squeeze'
    
    Returns:
        Dict with signal, entry price, SL, TP, confidence, multi-TF status
    """
    
    # Step 1: Multi-timeframe trend analysis
    mtf = calculate_multi_timeframe_trend(df_5m, df_15m, df_60m, df_daily)
    
    # Step 2: Select strategy based on regime
    if strategy == 'auto':
        if 'Trending' in regime:
            strategy = 'pullback'
        elif 'Ranging' in regime or 'Quiet' in regime:
            strategy = 'extreme'
        else:
            strategy = 'momentum'
    
    # Step 3: Detect entry signal (using 15min as primary timeframe)
    if strategy == 'pullback':
        signal = detect_pullback_entry(df_15m, regime)
    elif strategy == 'extreme':
        signal = detect_extreme_entry(df_15m, regime)
    elif strategy == 'momentum':
        signal = detect_momentum_breakout(df_15m)
    elif strategy == 'squeeze':
        signal = detect_bb_squeeze_breakout(df_15m)
    else:
        signal = {'signal': False, 'reason': 'Unknown strategy'}
    
    # Step 4: Multi-timeframe confirmation
    if signal.get('signal', False):
        # Require at least 3 aligned timeframes for high confidence
        if not mtf['aligned']:
            signal['confidence'] = 'Low'
            signal['reason'] += ' | MTF NOT aligned'
            signal['warning'] = 'Timeframes not aligned - risky entry'
    
    # Step 5: Add multi-timeframe context
    signal['symbol'] = symbol
    signal['regime'] = regime
    signal['multi_timeframe'] = mtf
    signal['strategy_used'] = strategy
    signal['timestamp'] = df_15m.index[-1]
    
    return signal