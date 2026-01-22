import pandas as pd
import numpy as np

def compute_metrics(trades: pd.DataFrame, initial_capital=100000.0):
    if trades.empty:
        return {
            "Trades": 0,
            "Win Rate %": 0.0,
            "Total PnL": 0.0,
            "Profit Factor": 0.0,
            "Avg Win/Loss": 0.0,
            "Sharpe Ratio": 0.0,
            "Max Drawdown %": 0.0
        }

    # Basic Stats
    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] < 0]
    
    total_trades = len(trades)
    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
    total_pnl = trades["pnl"].sum()
    
    # 1. Profit Factor (Gross Win / Gross Loss)
    gross_win = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf")
    
    # 2. Avg Win / Avg Loss Ratio (Profit Trade Ratio)
    avg_win = wins["pnl"].mean() if not wins.empty else 0
    avg_loss = abs(losses["pnl"].mean()) if not losses.empty else 0
    win_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0

    # 3. Sharpe Ratio (Simplified per trade)
    # Note: Annualized Sharpe usually requires daily returns. 
    # Here we calculate 'Trade Sharpe' = Mean PnL / Std Dev of PnL
    mean_pnl = trades["pnl"].mean()
    std_pnl = trades["pnl"].std()
    
    if std_pnl > 0:
        # Annualized approximation (assuming ~252 trading days, ~5 trades/day? 
        # For per-trade Sharpe, we usually just return the raw ratio or normalize it.
        # We will return the Per-Trade Sharpe here. > 0.2 is usually good per trade.)
        sharpe = round(mean_pnl / std_pnl, 2)
    else:
        sharpe = 0.0

    # 4. Max Drawdown % (Equity Curve based)
    equity = initial_capital + trades["pnl"].cumsum()
    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    max_dd_pct = round(drawdown.min() * 100, 2)

    return {
        "Trades": total_trades,
        "Win Rate %": round(win_rate, 2),
        "Total PnL": round(total_pnl, 2),
        "Profit Factor": profit_factor,      # Risk Metric 1
        "Avg Win/Loss": win_loss_ratio,      # Risk Metric 2
        "Sharpe Ratio": sharpe,              # Risk Metric 3
        "Max Drawdown %": max_dd_pct         # Risk Metric 4
    }

def expectancy(trades: pd.DataFrame):
    if trades.empty or "R" not in trades.columns:
        return {}

    avg_R = trades["R"].mean()
    win_rate = (trades["R"] > 0).mean()
    loss_rate = 1 - win_rate
    avg_win = trades.loc[trades["R"] > 0, "R"].mean()
    avg_loss = trades.loc[trades["R"] < 0, "R"].mean()

    expectancy_val = (win_rate * avg_win) + (loss_rate * avg_loss)

    return {
        "Avg R": round(avg_R, 3),
        "Win Rate %": round(win_rate * 100, 2),
        "Expectancy (R)": round(expectancy_val, 3)
    }