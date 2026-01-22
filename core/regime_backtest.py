# core/regime_backtest.py
"""
Regime Persistence Backtesting
Validates whether high persistence % actually predicts regime continuation
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple
from .regime_gmm import MarketRegimeGMM


def backtest_regime_persistence(
    df: pd.DataFrame,
    n_regimes: int = 4,
    persistence_threshold: float = 0.7,
    lookback_days: int = 100,
    test_days: int = 30
) -> Dict:
    """
    Backtest regime persistence predictions
    
    Process:
    1. For each day in test period:
       - Fit GMM on data up to that day
       - Predict persistence
       - Check if regime actually continued next day
    2. Calculate accuracy metrics
    
    Args:
        df: Daily OHLCV data
        n_regimes: Number of regimes to detect
        persistence_threshold: Threshold for "high persistence"
        lookback_days: Minimum days needed to train
        test_days: How many recent days to test on
    
    Returns:
        Dict with accuracy, precision, recall, confusion matrix
    """
    
    if len(df) < lookback_days + test_days:
        return {'Error': f'Need at least {lookback_days + test_days} days of data'}
    
    # Test on recent days
    test_start_idx = len(df) - test_days
    
    results = []
    
    for i in range(test_start_idx, len(df) - 1):  # -1 because we need next day
        # Step 1: Train on data up to day i
        train_df = df.iloc[:i+1]
        
        if len(train_df) < lookback_days:
            continue
        
        try:
            # Step 2: Fit GMM and detect regimes
            gmm = MarketRegimeGMM(n_regimes=n_regimes)
            df_regimes = gmm.detect_regimes(train_df)
            
            if df_regimes.empty:
                continue
            
            # Step 3: Get current regime and persistence prediction
            current_regime = df_regimes['Regime'].iloc[-1]
            current_prob = df_regimes['Regime_Prob'].iloc[-1]
            
            # Get persistence prediction
            persist_result = gmm.predict_next_regime(df_regimes, threshold=persistence_threshold)
            persistence_prob = persist_result.get('Persistence Prob %', 0) / 100
            
            # Step 4: Check ACTUAL next day regime
            # Fit on data including next day
            next_train_df = df.iloc[:i+2]
            gmm_next = MarketRegimeGMM(n_regimes=n_regimes)
            df_regimes_next = gmm_next.detect_regimes(next_train_df)
            
            actual_next_regime = df_regimes_next['Regime'].iloc[-1]
            
            # Step 5: Compare prediction vs reality
            predicted_persist = persistence_prob > persistence_threshold
            actual_persist = (current_regime == actual_next_regime)
            
            results.append({
                'date': df.index[i],
                'current_regime': current_regime,
                'regime_confidence': current_prob,
                'persistence_prob': persistence_prob,
                'predicted_persist': predicted_persist,
                'actual_next_regime': actual_next_regime,
                'actual_persist': actual_persist,
                'correct': predicted_persist == actual_persist
            })
        
        except Exception as e:
            # Skip days with errors
            continue
    
    if not results:
        return {'Error': 'No valid test days found'}
    
    # Calculate metrics
    results_df = pd.DataFrame(results)
    
    # Overall accuracy
    accuracy = results_df['correct'].mean()
    
    # When we predicted persistence, how often did it actually persist?
    high_persist_predictions = results_df[results_df['predicted_persist'] == True]
    if len(high_persist_predictions) > 0:
        precision = high_persist_predictions['actual_persist'].mean()
    else:
        precision = 0
    
    # Of all times regime actually persisted, how many did we catch?
    actual_persistences = results_df[results_df['actual_persist'] == True]
    if len(actual_persistences) > 0:
        recall = actual_persistences['predicted_persist'].mean()
    else:
        recall = 0
    
    # Regime-specific accuracy
    regime_accuracy = {}
    for regime in results_df['current_regime'].unique():
        regime_mask = results_df['current_regime'] == regime
        regime_results = results_df[regime_mask]
        regime_accuracy[regime] = {
            'count': len(regime_results),
            'accuracy': regime_results['correct'].mean(),
            'avg_persistence': regime_results['persistence_prob'].mean()
        }
    
    return {
        'test_days': len(results_df),
        'overall_accuracy': round(accuracy * 100, 2),
        'precision': round(precision * 100, 2),
        'recall': round(recall * 100, 2),
        'regime_breakdown': regime_accuracy,
        'results_df': results_df
    }


def backtest_trade_signals(
    df: pd.DataFrame,
    n_regimes: int = 4,
    persistence_threshold: float = 0.7,
    confidence_threshold: float = 0.6,
    min_duration: int = 2,
    lookback_days: int = 100,
    test_days: int = 60
) -> Dict:
    """
    Backtest actual TRADE SIGNALS (like your Nifty 100 scanner)
    
    Simulates:
    1. Each day, check if stock is "tradeable" (Bullish + high persist + confidence)
    2. If yes, enter trade (assume buy at close)
    3. Exit after N days or when regime changes
    4. Calculate win rate and returns
    
    Args:
        df: Daily OHLCV data
        n_regimes: Number of regimes
        persistence_threshold: Min persistence for entry
        confidence_threshold: Min confidence for entry
        min_duration: Min regime duration for entry
        lookback_days: Min data needed
        test_days: Test period
    
    Returns:
        Trade results with win rate, avg return, etc.
    """
    
    if len(df) < lookback_days + test_days:
        return {'Error': f'Need at least {lookback_days + test_days} days'}
    
    test_start_idx = len(df) - test_days
    
    trades = []
    in_trade = False
    entry_price = 0
    entry_date = None
    entry_regime = None
    
    for i in range(test_start_idx, len(df) - 5):  # -5 to allow holding period
        if in_trade:
            # Check exit conditions
            current_price = df['Close'].iloc[i]
            days_held = (df.index[i] - entry_date).days
            
            # Exit if: 5 days passed OR regime changed OR reached end
            if days_held >= 5 or i >= len(df) - 1:
                exit_price = current_price
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                
                trades.append({
                    'entry_date': entry_date,
                    'exit_date': df.index[i],
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'pnl_pct': pnl_pct,
                    'days_held': days_held,
                    'entry_regime': entry_regime
                })
                
                in_trade = False
        
        else:
            # Check entry conditions
            train_df = df.iloc[:i+1]
            
            if len(train_df) < lookback_days:
                continue
            
            try:
                # Detect regime
                gmm = MarketRegimeGMM(n_regimes=n_regimes)
                df_regimes = gmm.detect_regimes(train_df)
                
                if df_regimes.empty:
                    continue
                
                current_regime = df_regimes['Regime'].iloc[-1]
                current_prob = df_regimes['Regime_Prob'].iloc[-1]
                
                # Calculate duration
                regime_duration = 1
                for idx in range(len(df_regimes) - 2, -1, -1):
                    if df_regimes['Regime'].iloc[idx] == current_regime:
                        regime_duration += 1
                    else:
                        break
                
                # Get persistence
                persist_result = gmm.predict_next_regime(df_regimes, threshold=persistence_threshold)
                persistence_prob = persist_result.get('Persistence Prob %', 0) / 100
                
                # ENTRY LOGIC (same as your Nifty 100 scanner)
                is_bullish = 'Bullish' in current_regime
                high_persist = persistence_prob > persistence_threshold
                high_confidence = current_prob > confidence_threshold
                established = regime_duration >= min_duration
                
                if is_bullish and high_persist and high_confidence and established:
                    # ENTER TRADE
                    in_trade = True
                    entry_price = df['Close'].iloc[i]
                    entry_date = df.index[i]
                    entry_regime = current_regime
            
            except Exception:
                continue
    
    if not trades:
        return {'Error': 'No trades generated in test period'}
    
    # Calculate metrics
    trades_df = pd.DataFrame(trades)
    
    win_trades = trades_df[trades_df['pnl_pct'] > 0]
    loss_trades = trades_df[trades_df['pnl_pct'] <= 0]
    
    metrics = {
        'total_trades': len(trades_df),
        'win_trades': len(win_trades),
        'loss_trades': len(loss_trades),
        'win_rate': round(len(win_trades) / len(trades_df) * 100, 2) if len(trades_df) > 0 else 0,
        'avg_win': round(win_trades['pnl_pct'].mean(), 2) if len(win_trades) > 0 else 0,
        'avg_loss': round(loss_trades['pnl_pct'].mean(), 2) if len(loss_trades) > 0 else 0,
        'avg_return': round(trades_df['pnl_pct'].mean(), 2),
        'best_trade': round(trades_df['pnl_pct'].max(), 2),
        'worst_trade': round(trades_df['pnl_pct'].min(), 2),
        'avg_hold_days': round(trades_df['days_held'].mean(), 1),
        'trades_df': trades_df
    }
    
    return metrics


def validate_current_signals(
    symbols: List[str],
    data_root: str,
    n_regimes: int = 4,
    persistence_threshold: float = 0.7
) -> pd.DataFrame:
    """
    For symbols in current "trade zone", backtest their signals
    
    Returns DataFrame with:
    - Symbol
    - Current persistence %
    - Historical accuracy of that persistence level
    - Recommended action
    """
    from pathlib import Path
    
    results = []
    
    for symbol in symbols:
        sym_path = Path(data_root) / symbol / "1day"
        files = list(sym_path.glob("*.parquet"))
        
        if not files:
            continue
        
        try:
            df = pd.read_parquet(files[0])
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            
            # Backtest
            backtest_result = backtest_regime_persistence(
                df,
                n_regimes=n_regimes,
                persistence_threshold=persistence_threshold,
                test_days=30
            )
            
            if 'Error' in backtest_result:
                continue
            
            # Current prediction
            gmm = MarketRegimeGMM(n_regimes=n_regimes)
            df_regimes = gmm.detect_regimes(df)
            current_persist = gmm.predict_next_regime(df_regimes, threshold=persistence_threshold)
            
            results.append({
                'Symbol': symbol,
                'Current Regime': df_regimes['Regime'].iloc[-1],
                'Predicted Persistence %': current_persist.get('Persistence Prob %', 0),
                'Historical Accuracy %': backtest_result['overall_accuracy'],
                'Historical Precision %': backtest_result['precision'],
                'Recommendation': 'TRADE' if backtest_result['precision'] > 60 else 'SKIP'
            })
        
        except Exception:
            continue
    
    return pd.DataFrame(results)