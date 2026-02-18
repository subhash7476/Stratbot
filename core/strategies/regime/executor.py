"""
Layer 3: EXECUTOR — Regime-aware intraday trading strategy.

Daily regime classification gates intraday 15m signal generation.
Supports both live (process_bar) and batch (vectorized) modes.
"""
import numpy as np
import pandas as pd
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict
from hashlib import sha256

from core.strategies.base import BaseStrategy, StrategyContext
from core.events import SignalEvent, SignalType, OHLCVBar
from core.strategies.regime.classifier import HMMRegimeClassifier, RegimeState, RegimeClassification
from core.strategies.regime.observer import RegimeObserver
from core.strategies.regime.sizing import RegimeRiskEngine
from core.strategies.regime.circuit_breaker import WeeklyCircuitBreaker


def batch_generate_regime_signals(
    nifty_15m: pd.DataFrame,
    daily_features: pd.DataFrame,
    classifier: HMMRegimeClassifier,
    config: dict,
    run_id: str = '',
    symbol: str = 'NSE_INDEX|Nifty 50',
) -> List[SignalEvent]:
    """
    Vectorized signal generation for backtesting.

    1. Classify each day's regime from daily_features
    2. For each 15m bar, check regime + technical confirmation
    3. Return list of SignalEvent with full metadata (SL/TP/quantity)

    Args:
        nifty_15m: resampled 15m OHLCV DataFrame (index=timestamp)
        daily_features: output of RegimeObserver.compute_features()
        classifier: trained HMMRegimeClassifier
        config: strategy config dict
        run_id: backtest run ID (for signal ID generation)
        symbol: trading symbol
    """
    # Config
    ema_fast_period = config.get('ema_fast', 9)
    ema_slow_period = config.get('ema_slow', 21)
    atr_period = config.get('atr_period', 14)
    vol_z_threshold = config.get('vol_z_threshold', 1.0)
    expansion_threshold = config.get('expansion_threshold', 0.70)
    shock_threshold = config.get('shock_threshold', 0.65)
    contraction_threshold = config.get('contraction_threshold', 0.65)
    persistence_days = config.get('persistence_days', 2)
    time_stop_bars = config.get('time_stop_bars', 20)
    bar_minutes = config.get('bar_minutes', 15)

    risk_engine = RegimeRiskEngine(config.get('sizing', {}))
    initial_capital = config.get('initial_capital', 100000.0)

    # Get HMM features and classify all days
    hmm_features = RegimeObserver().get_hmm_features(daily_features)
    regime_proba = classifier.classify_all(hmm_features, raw_features=daily_features)

    # Build daily regime lookup: date -> RegimeClassification
    daily_regime = {}
    for idx, row in regime_proba.iterrows():
        probs = {}
        for state in RegimeState:
            col = f'P({state.value})'
            probs[state] = row.get(col, 0.0)
        daily_regime[idx] = RegimeClassification(
            date=idx,
            state=RegimeState[row['regime']],
            probabilities=probs,
            entropy=row['entropy'],
        )

    # Compute intraday indicators on 15m data
    df = nifty_15m.copy()
    df['ema_fast'] = df['close'].ewm(span=ema_fast_period, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=ema_slow_period, adjust=False).mean()

    # ATR
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr'] = df['tr'].ewm(alpha=1.0/atr_period, adjust=False).mean()

    # Detect if volume data is available (indices often have volume=0)
    has_volume = df['volume'].sum() > 0

    if has_volume:
        # Volume z-score (20-bar rolling)
        vol_mean = df['volume'].rolling(20, min_periods=5).mean()
        vol_std = df['volume'].rolling(20, min_periods=5).std()
        df['vol_z'] = (df['volume'] - vol_mean) / (vol_std + 1e-10)

        # Session VWAP (reset each day)
        df['_date'] = df.index.date if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df.index).date
        df['cum_vol'] = df.groupby('_date')['volume'].cumsum()
        df['cum_vp'] = (df['close'] * df['volume']).groupby(df['_date']).cumsum()
        df['vwap'] = df['cum_vp'] / (df['cum_vol'] + 1e-10)
    else:
        df['vol_z'] = 0.0
        df['vwap'] = 0.0

    # EMA crossover detection
    df['ema_cross_up'] = (df['ema_fast'] > df['ema_slow']) & (df['ema_fast'].shift(1) <= df['ema_slow'].shift(1))

    # Pre-compute consecutive expansion day counts per date
    # This avoids counting per-bar (which inflates the counter)
    sorted_dates = sorted(daily_regime.keys())
    consec_expansion = {}
    streak = 0
    for d in sorted_dates:
        r = daily_regime[d]
        exp_p = r.probabilities.get(RegimeState.EXPANSION, 0)
        if exp_p > expansion_threshold:
            streak += 1
        else:
            streak = 0
        consec_expansion[d] = streak

    # Generate signals
    signals = []
    in_position = False

    for i in range(max(ema_slow_period, atr_period, 20), len(df)):
        row = df.iloc[i]
        ts = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df.index[i])
        bar_date = ts.date() if hasattr(ts, 'date') else ts

        # Get today's regime
        regime = daily_regime.get(bar_date)
        if regime is None:
            # Try previous trading day
            for delta in range(1, 5):
                prev_date = bar_date - timedelta(days=delta)
                regime = daily_regime.get(prev_date)
                if regime is not None:
                    bar_date = prev_date
                    break
        if regime is None:
            continue

        expansion_prob = regime.probabilities.get(RegimeState.EXPANSION, 0)
        shock_prob = regime.probabilities.get(RegimeState.SHOCK, 0)
        contraction_prob = regime.probabilities.get(RegimeState.CONTRACTION, 0)
        expansion_days = consec_expansion.get(bar_date, 0)

        atr_val = row['atr']
        if pd.isna(atr_val) or atr_val <= 0:
            continue

        # EXIT signal: shock or contraction while in position
        if in_position and (shock_prob > shock_threshold or contraction_prob > contraction_threshold):
            sig_id = sha256(f"{run_id}_{symbol}_{ts}_EXIT".encode()).hexdigest()
            signals.append(SignalEvent(
                strategy_id='hmm_regime',
                symbol=symbol,
                timestamp=ts,
                signal_type=SignalType.EXIT,
                confidence=max(shock_prob, contraction_prob),
                metadata={
                    'signal_id': sig_id,
                    'exit_reason': 'regime_shift',
                    'regime': regime.state.value,
                    'entropy': regime.entropy,
                }
            ))
            in_position = False
            continue

        # LONG entry: expansion with persistence + technical confirmation
        if not in_position and expansion_days >= persistence_days:
            # Technical confirmation on 15m
            # EMA trend alignment (not crossover — expansion regime is the gate)
            ema_ok = row['ema_fast'] > row['ema_slow']

            # Volume filters only apply when volume data exists
            if has_volume:
                vol_ok = row['vol_z'] > vol_z_threshold
                vwap_ok = row['close'] > row['vwap']
            else:
                vol_ok = True
                vwap_ok = True

            if ema_ok and vol_ok and vwap_ok:
                sizing = risk_engine.calculate_position(
                    capital=initial_capital,
                    price=row['close'],
                    atr=atr_val,
                    direction='BUY',
                    entropy=regime.entropy,
                )

                if sizing['quantity'] > 0:
                    sig_id = sha256(f"{run_id}_{symbol}_{ts}_BUY".encode()).hexdigest()
                    signals.append(SignalEvent(
                        strategy_id='hmm_regime',
                        symbol=symbol,
                        timestamp=ts,
                        signal_type=SignalType.BUY,
                        confidence=expansion_prob,
                        metadata={
                            'signal_id': sig_id,
                            'quantity': sizing['quantity'],
                            'sl': sizing['sl'],
                            'tp': sizing['tp'],
                            'h_bars': time_stop_bars,
                            'entry_price_basis': 'current_close',
                            'regime': regime.state.value,
                            'expansion_prob': expansion_prob,
                            'entropy': regime.entropy,
                            'atr_at_event': atr_val,
                            'ema_fast': row['ema_fast'],
                            'ema_slow': row['ema_slow'],
                            'vol_z': row['vol_z'],
                            'vwap': row['vwap'],
                            'bar_minutes': bar_minutes,
                        }
                    ))
                    in_position = True

    return signals


def batch_generate_vix_baseline_signals(
    nifty_15m: pd.DataFrame,
    daily_features: pd.DataFrame,
    config: dict,
    run_id: str = '',
    symbol: str = 'NSE_INDEX|Nifty 50',
) -> List[SignalEvent]:
    """
    VIX baseline: simple threshold rules instead of HMM.
    BUY when VIX < vix_entry_threshold AND EMA(9) > EMA(21).
    EXIT when VIX > vix_exit_threshold.
    Same sizing, SL/TP, time-stop as HMM version for fair comparison.
    """
    ema_fast_period = config.get('ema_fast', 9)
    ema_slow_period = config.get('ema_slow', 21)
    atr_period = config.get('atr_period', 14)
    time_stop_bars = config.get('time_stop_bars', 20)
    bar_minutes = config.get('bar_minutes', 15)
    vix_entry = config.get('vix_entry_threshold', 18.0)
    vix_exit = config.get('vix_exit_threshold', 22.0)

    risk_engine = RegimeRiskEngine(config.get('sizing', {}))
    initial_capital = config.get('initial_capital', 100000.0)

    # Build daily VIX lookup from features
    daily_vix = {}
    for idx, row in daily_features.iterrows():
        daily_vix[idx] = row.get('vix_level', 999)

    # Compute intraday indicators
    df = nifty_15m.copy()
    df['ema_fast'] = df['close'].ewm(span=ema_fast_period, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=ema_slow_period, adjust=False).mean()
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr'] = df['tr'].ewm(alpha=1.0/atr_period, adjust=False).mean()
    df['vol_z'] = 0.0
    df['vwap'] = 0.0

    signals = []
    in_position = False

    for i in range(max(ema_slow_period, atr_period, 20), len(df)):
        row = df.iloc[i]
        ts = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df.index[i])
        bar_date = ts.date() if hasattr(ts, 'date') else ts

        # Get today's VIX
        vix_val = daily_vix.get(bar_date)
        if vix_val is None:
            for delta in range(1, 5):
                prev_date = bar_date - timedelta(days=delta)
                vix_val = daily_vix.get(prev_date)
                if vix_val is not None:
                    break
        if vix_val is None:
            continue

        atr_val = row['atr']
        if pd.isna(atr_val) or atr_val <= 0:
            continue

        # EXIT: VIX above exit threshold
        if in_position and vix_val > vix_exit:
            sig_id = sha256(f"{run_id}_{symbol}_{ts}_EXIT_VIX".encode()).hexdigest()
            signals.append(SignalEvent(
                strategy_id='vix_baseline',
                symbol=symbol,
                timestamp=ts,
                signal_type=SignalType.EXIT,
                confidence=1.0,
                metadata={
                    'signal_id': sig_id,
                    'exit_reason': 'vix_high',
                    'vix': vix_val,
                }
            ))
            in_position = False
            continue

        # ENTRY: VIX below entry threshold + EMA alignment
        if not in_position and vix_val < vix_entry:
            ema_ok = row['ema_fast'] > row['ema_slow']
            if ema_ok:
                sizing = risk_engine.calculate_position(
                    capital=initial_capital,
                    price=row['close'],
                    atr=atr_val,
                    direction='BUY',
                    entropy=0.0,
                )
                if sizing['quantity'] > 0:
                    sig_id = sha256(f"{run_id}_{symbol}_{ts}_BUY_VIX".encode()).hexdigest()
                    signals.append(SignalEvent(
                        strategy_id='vix_baseline',
                        symbol=symbol,
                        timestamp=ts,
                        signal_type=SignalType.BUY,
                        confidence=1.0,
                        metadata={
                            'signal_id': sig_id,
                            'quantity': sizing['quantity'],
                            'sl': sizing['sl'],
                            'tp': sizing['tp'],
                            'h_bars': time_stop_bars,
                            'entry_price_basis': 'current_close',
                            'vix': vix_val,
                            'atr_at_event': atr_val,
                            'ema_fast': row['ema_fast'],
                            'ema_slow': row['ema_slow'],
                            'vol_z': 0.0,
                            'vwap': 0.0,
                            'bar_minutes': bar_minutes,
                        }
                    ))
                    in_position = True

    return signals
