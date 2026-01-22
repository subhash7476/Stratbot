import pandas as pd
import numpy as np

def analyze_trend_quality(df: pd.DataFrame):
    df = df.copy()

    # Work only on active trend bars
    if "Trend" not in df.columns:
        return {
            "error": "Trend column missing — compute indicators first"
        }

    trend_df = df[df["Trend"] != 0].copy()

    if trend_df.empty:
        return {
            "Total Candles": len(df),
            "Trend Changes": 0,
            "Avg Trend Duration": 0,
            "Bullish Bars": 0,
            "Bearish Bars": 0,
            "Whipsaw Ratio": "0.00%",
            "Whipsaw Risk": "HIGH"
        }

    # Correct trend change detection
    trend_df['TrendChange'] = trend_df['Trend'] != trend_df['Trend'].shift(1)
    trend_changes = trend_df['TrendChange'].sum()

    # True average trend duration
    trend_groups = trend_df['TrendChange'].cumsum()
    avg_duration = trend_df.groupby(trend_groups).size().mean()

    bullish_bars = (trend_df['Trend'] == 1).sum()
    bearish_bars = (trend_df['Trend'] == -1).sum()

    whipsaw_ratio = trend_changes / max(len(trend_df), 1)

    if whipsaw_ratio > 0.08:
        whipsaw_risk = "HIGH"
    elif whipsaw_ratio > 0.04:
        whipsaw_risk = "MEDIUM"
    else:
        whipsaw_risk = "LOW"

    return {
        "Total Candles": len(df),
        "Trend Changes": int(trend_changes),
        "Avg Trend Duration": round(avg_duration, 1),
        "Bullish Bars": bullish_bars,
        "Bearish Bars": bearish_bars,
        "Whipsaw Ratio": f"{whipsaw_ratio:.2%}",
        "Whipsaw Risk": whipsaw_risk
    }



def compare_supertrend_versions(df: pd.DataFrame):
    """
    Compare standard vs adaptive supertrend.
    Returns both dataframes.
    """
    from backtest.indicators import supertrend, supertrend_adaptive
    
    df_std = supertrend(df.copy(), period=10, mult=3.0)
    df_adp = supertrend_adaptive(df.copy(), base_period=10)
    
    stats_std = analyze_trend_quality(df_std)
    stats_adp = analyze_trend_quality(df_adp)
    
    return {
        "standard": {"df": df_std, "stats": stats_std},
        "adaptive": {"df": df_adp, "stats": stats_adp}
    } 
