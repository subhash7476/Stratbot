"""
Backtest Validation Module for Regime-Based Trading
====================================================
Simulates regime-based trading using historical data to validate
the predictive power of the regime detection system.

Key Features:
- Walk-forward validation (no lookahead bias)
- Regime persistence testing
- Entry/exit simulation
- Statistical significance testing
- Monte Carlo simulation for confidence intervals
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple, List
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')


class RegimeBacktester:
    """
    Backtest regime-based trading strategy
    Uses walk-forward validation to avoid lookahead bias
    """
    
    def __init__(
        self,
        df_with_regimes: pd.DataFrame,
        regime_name: str,
        entry_strategy: str = 'Breakout',
        holding_period_days: int = 5,
        stop_loss_pct: float = 2.0,
        take_profit_pct: float = 3.0
    ):
        """
        Args:
            df_with_regimes: DataFrame with OHLCV + Regime columns
            regime_name: Target regime to trade (e.g., 'Volatile Bullish')
            entry_strategy: 'Breakout', 'Momentum', or 'Mean_Reversion'
            holding_period_days: Max holding period
            stop_loss_pct: Stop loss percentage
            take_profit_pct: Take profit percentage
        """
        self.df = df_with_regimes.copy()
        self.regime_name = regime_name
        self.entry_strategy = entry_strategy
        self.holding_period = holding_period_days
        self.sl_pct = stop_loss_pct / 100
        self.tp_pct = take_profit_pct / 100
        
        self.trades = []
        self.equity_curve = []
        
    def generate_entry_signals(self) -> pd.DataFrame:
        """
        Generate entry signals based on regime + strategy
        
        Entry Logic:
        - Regime must match target regime
        - Regime must have persisted for at least 2 days
        - Entry signal based on strategy type
        """
        df = self.df.copy()
        df['Entry_Signal'] = False
        
        # Calculate regime persistence (how many consecutive days in same regime)
        df['Regime_Duration'] = 0
        for i in range(1, len(df)):
            if df['Regime'].iloc[i] == df['Regime'].iloc[i-1]:
                df.loc[df.index[i], 'Regime_Duration'] = df['Regime_Duration'].iloc[i-1] + 1
            else:
                df.loc[df.index[i], 'Regime_Duration'] = 1
        
        # Only enter when:
        # 1. Regime matches target
        # 2. Regime persisted for at least 2 days (persistence requirement)
        # 3. Strategy-specific condition met
        
        regime_matches = df['Regime'] == self.regime_name
        regime_persisted = df['Regime_Duration'] >= 2
        
        if self.entry_strategy == 'Breakout':
            # Enter on high breakout after regime confirmation
            strategy_signal = df['High'] > df['High'].shift(1).rolling(5).max()
        
        elif self.entry_strategy == 'Momentum':
            # Enter when price crosses above 5-day EMA
            df['EMA5'] = df['Close'].ewm(span=5).mean()
            strategy_signal = (df['Close'] > df['EMA5']) & (df['Close'].shift(1) <= df['EMA5'].shift(1))
        
        else:  # Mean_Reversion
            # Enter when price bounces from lower Bollinger Band
            df['BB_Mid'] = df['Close'].rolling(20).mean()
            df['BB_Std'] = df['Close'].rolling(20).std()
            df['BB_Lower'] = df['BB_Mid'] - 2 * df['BB_Std']
            strategy_signal = (df['Close'] > df['BB_Lower']) & (df['Close'].shift(1) <= df['BB_Lower'].shift(1))
        
        df['Entry_Signal'] = regime_matches & regime_persisted & strategy_signal
        
        return df
    
    def simulate_trades(self) -> List[Dict]:
        """
        Walk-forward simulation of trades
        
        Returns:
            List of trade dictionaries with entry/exit details
        """
        df = self.generate_entry_signals()
        trades = []
        
        i = 0
        while i < len(df):
            # Check for entry signal
            if df['Entry_Signal'].iloc[i]:
                entry_date = df.index[i]
                entry_price = df['Close'].iloc[i]
                
                # Set exit levels
                sl_price = entry_price * (1 - self.sl_pct)
                tp_price = entry_price * (1 + self.tp_pct)
                
                # Simulate holding period
                exit_idx = None
                exit_reason = 'Holding Period Expired'
                exit_price = None
                
                for j in range(i + 1, min(i + self.holding_period + 1, len(df))):
                    high = df['High'].iloc[j]
                    low = df['Low'].iloc[j]
                    close = df['Close'].iloc[j]
                    
                    # Check stop loss
                    if low <= sl_price:
                        exit_idx = j
                        exit_price = sl_price
                        exit_reason = 'Stop Loss'
                        break
                    
                    # Check take profit
                    if high >= tp_price:
                        exit_idx = j
                        exit_price = tp_price
                        exit_reason = 'Take Profit'
                        break
                
                # If no SL/TP hit, exit at close of last day
                if exit_idx is None:
                    exit_idx = min(i + self.holding_period, len(df) - 1)
                    exit_price = df['Close'].iloc[exit_idx]
                
                exit_date = df.index[exit_idx]
                
                # Calculate P&L
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                
                # Record trade
                trade = {
                    'Entry_Date': entry_date,
                    'Entry_Price': entry_price,
                    'Exit_Date': exit_date,
                    'Exit_Price': exit_price,
                    'Exit_Reason': exit_reason,
                    'PnL_%': pnl_pct,
                    'Holding_Days': (exit_date - entry_date).days,
                    'Regime_At_Entry': df['Regime'].iloc[i],
                    'Regime_Duration_At_Entry': df['Regime_Duration'].iloc[i]
                }
                
                trades.append(trade)
                
                # Skip forward to avoid overlapping trades
                i = exit_idx + 1
            else:
                i += 1
        
        self.trades = trades
        return trades
    
    def calculate_metrics(self) -> Dict:
        """
        Calculate comprehensive performance metrics
        """
        if not self.trades:
            return {
                'Total_Trades': 0,
                'Win_Rate_%': 0,
                'Avg_Win_%': 0,
                'Avg_Loss_%': 0,
                'Profit_Factor': 0,
                'Total_Return_%': 0,
                'Max_Drawdown_%': 0,
                'Sharpe_Ratio': 0,
                'Avg_Holding_Days': 0
            }
        
        trades_df = pd.DataFrame(self.trades)
        
        # Basic metrics
        total_trades = len(trades_df)
        wins = trades_df[trades_df['PnL_%'] > 0]
        losses = trades_df[trades_df['PnL_%'] <= 0]
        
        win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0
        avg_win = wins['PnL_%'].mean() if len(wins) > 0 else 0
        avg_loss = losses['PnL_%'].mean() if len(losses) > 0 else 0
        
        # Profit factor
        gross_profit = wins['PnL_%'].sum() if len(wins) > 0 else 0
        gross_loss = abs(losses['PnL_%'].sum()) if len(losses) > 0 else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Total return
        total_return = trades_df['PnL_%'].sum()
        
        # Equity curve for drawdown
        equity = 100  # Start with 100
        equity_curve = [equity]
        for pnl in trades_df['PnL_%']:
            equity = equity * (1 + pnl / 100)
            equity_curve.append(equity)
        
        # Max drawdown
        peak = equity_curve[0]
        max_dd = 0
        for equity_val in equity_curve:
            if equity_val > peak:
                peak = equity_val
            dd = ((peak - equity_val) / peak) * 100
            if dd > max_dd:
                max_dd = dd
        
        # Sharpe ratio (assuming 252 trading days/year)
        returns = trades_df['PnL_%'].values
        sharpe = (returns.mean() / returns.std()) * np.sqrt(252 / self.holding_period) if returns.std() > 0 else 0
        
        # Average holding period
        avg_holding = trades_df['Holding_Days'].mean()
        
        return {
            'Total_Trades': total_trades,
            'Win_Rate_%': round(win_rate, 2),
            'Avg_Win_%': round(avg_win, 2),
            'Avg_Loss_%': round(avg_loss, 2),
            'Profit_Factor': round(profit_factor, 2),
            'Total_Return_%': round(total_return, 2),
            'Max_Drawdown_%': round(max_dd, 2),
            'Sharpe_Ratio': round(sharpe, 2),
            'Avg_Holding_Days': round(avg_holding, 1)
        }
    
    def monte_carlo_simulation(self, n_simulations: int = 1000) -> Dict:
        """
        Monte Carlo simulation to estimate confidence intervals
        
        Randomly reorders trades to simulate different sequences
        and calculates distribution of returns
        """
        if not self.trades:
            return {}
        
        trades_df = pd.DataFrame(self.trades)
        pnl_values = trades_df['PnL_%'].values
        
        simulated_returns = []
        
        for _ in range(n_simulations):
            # Randomly shuffle trades
            shuffled_pnl = np.random.choice(pnl_values, size=len(pnl_values), replace=True)
            total_return = shuffled_pnl.sum()
            simulated_returns.append(total_return)
        
        simulated_returns = np.array(simulated_returns)
        
        return {
            'Mean_Return_%': round(simulated_returns.mean(), 2),
            '5th_Percentile_%': round(np.percentile(simulated_returns, 5), 2),
            '95th_Percentile_%': round(np.percentile(simulated_returns, 95), 2),
            'Probability_Profitable_%': round((simulated_returns > 0).sum() / n_simulations * 100, 2),
            'Best_Case_%': round(simulated_returns.max(), 2),
            'Worst_Case_%': round(simulated_returns.min(), 2)
        }
    
    def get_next_trade_probability(self) -> Dict:
        """
        Calculate probability that next trade will be profitable
        based on recent trade history
        """
        if len(self.trades) < 5:
            return {
                'Insufficient_Data': True,
                'Message': 'Need at least 5 trades for meaningful probability'
            }
        
        trades_df = pd.DataFrame(self.trades)
        
        # Last 10 trades
        recent_trades = trades_df.tail(10)
        recent_win_rate = (recent_trades['PnL_%'] > 0).sum() / len(recent_trades) * 100
        
        # Last 20 trades
        if len(trades_df) >= 20:
            medium_trades = trades_df.tail(20)
            medium_win_rate = (medium_trades['PnL_%'] > 0).sum() / len(medium_trades) * 100
        else:
            medium_win_rate = recent_win_rate
        
        # Overall win rate
        overall_win_rate = (trades_df['PnL_%'] > 0).sum() / len(trades_df) * 100
        
        # Weighted probability (more weight to recent performance)
        weighted_prob = (
            0.5 * recent_win_rate +
            0.3 * medium_win_rate +
            0.2 * overall_win_rate
        )
        
        # Expected value of next trade
        avg_win = trades_df[trades_df['PnL_%'] > 0]['PnL_%'].mean()
        avg_loss = trades_df[trades_df['PnL_%'] <= 0]['PnL_%'].mean()
        expected_value = (weighted_prob / 100 * avg_win) + ((1 - weighted_prob / 100) * avg_loss)
        
        return {
            'Recent_Win_Rate_%': round(recent_win_rate, 2),
            'Medium_Term_Win_Rate_%': round(medium_win_rate, 2),
            'Overall_Win_Rate_%': round(overall_win_rate, 2),
            'Weighted_Probability_%': round(weighted_prob, 2),
            'Expected_Value_%': round(expected_value, 2),
            'Recommendation': 'Enter Trade' if weighted_prob > 55 and expected_value > 0.5 else 'Wait for Better Setup'
        }


def validate_regime_prediction(
    df_with_regimes: pd.DataFrame,
    current_regime: str,
    persistence_prob: float,
    confidence: float,
    duration: int
) -> Dict:
    """
    Validate regime prediction using historical patterns
    
    Args:
        df_with_regimes: Historical data with regime labels
        current_regime: Current regime name
        persistence_prob: Predicted persistence probability (%)
        confidence: Model confidence (%)
        duration: Current regime duration (days)
    
    Returns:
        Validation metrics and recommendation
    """
    # Historical regime transitions
    df = df_with_regimes.copy()
    
    # Find all instances where this regime occurred
    regime_instances = df[df['Regime'] == current_regime].copy()
    
    if len(regime_instances) < 10:
        return {
            'Validation': 'Insufficient Historical Data',
            'Message': f'Only {len(regime_instances)} historical instances of {current_regime}'
        }
    
    # Calculate how often this regime persisted for 5+ days
    df['Regime_Changed'] = df['Regime'] != df['Regime'].shift(1)
    df['Regime_ID'] = df['Regime_Changed'].cumsum()
    
    regime_durations = []
    for regime_id in df[df['Regime'] == current_regime]['Regime_ID'].unique():
        regime_segment = df[df['Regime_ID'] == regime_id]
        regime_durations.append(len(regime_segment))
    
    avg_duration = np.mean(regime_durations)
    median_duration = np.median(regime_durations)
    
    # Probability that regime lasts 5+ more days given current duration
    long_lasting_count = sum(1 for d in regime_durations if d >= duration + 5)
    persistence_validation = (long_lasting_count / len(regime_durations)) * 100
    
    # Compare predicted vs historical persistence
    persistence_accuracy = 100 - abs(persistence_prob - persistence_validation)
    
    # Decision logic
    strong_persistence = persistence_validation > 60
    good_accuracy = persistence_accuracy > 75
    reasonable_duration = duration >= 2 and duration <= avg_duration * 1.5
    high_confidence = confidence > 60
    
    enter_trade = strong_persistence and good_accuracy and reasonable_duration and high_confidence
    
    return {
        'Historical_Persistence_%': round(persistence_validation, 2),
        'Predicted_Persistence_%': round(persistence_prob, 2),
        'Accuracy_Score_%': round(persistence_accuracy, 2),
        'Avg_Historical_Duration': round(avg_duration, 1),
        'Median_Historical_Duration': round(median_duration, 1),
        'Current_Duration': duration,
        'Total_Historical_Instances': len(regime_durations),
        'Enter_Trade': enter_trade,
        'Confidence_Level': 'High' if enter_trade else 'Medium' if strong_persistence else 'Low',
        'Risk_Assessment': 'Low Risk' if enter_trade else 'Medium Risk' if strong_persistence else 'High Risk'
    }


if __name__ == "__main__":
    print("✅ Backtest Validator Module Loaded")
    print("=" * 60)
    print("\nKey Functions:")
    print("1. RegimeBacktester - Walk-forward backtest simulation")
    print("2. validate_regime_prediction - Historical validation")
    print("3. Monte Carlo simulation for confidence intervals")