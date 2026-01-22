# Diagnostic Script: Why is v3.0 Blocking Everything?
"""
Run this to diagnose v3.0 confidence issues
"""

import pandas as pd
import numpy as np
from pathlib import Path
import sys, os


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Test on one stock
symbol = "RELIANCE"
timeframe = "15minute"


DATA_DIR = Path("data/derived")
symbol_path = DATA_DIR / symbol / timeframe
parquet_files = list(symbol_path.glob("*.parquet"))

if parquet_files:
    data_file = sorted(parquet_files)[-1]
    df = pd.read_parquet(data_file)
    
    print(f"Loaded {len(df)} bars for {symbol}")
    print(f"Date range: {df.index[0]} to {df.index[-1]}")
    
    # Compute regime indicators (copy from v3.0)
    from core.strategies.simple_momentum import simple_momentum_strategy
    df = simple_momentum_strategy(df.copy(), fast_ema=5, slow_ema=20, rsi_period=14)
    
    # Check signals
    signals = df[df['Signal'] != 0]
    print(f"\nTotal signals generated: {len(signals)}")
    print(f"Long signals: {len(signals[signals['Signal'] == 1])}")
    print(f"Short signals: {len(signals[signals['Signal'] == -1])}")
    
    if len(signals) == 0:
        print("\n❌ PROBLEM: Strategy is not generating any signals!")
        print("Check if strategy module is working correctly")
    else:
        print("\n✅ Strategy is generating signals")
        
        # Now check regime computation
        # Simplified regime computation for diagnosis
        
        # ATR
        high_low = df['High'] - df['Low']
        high_close = abs(df['High'] - df['Close'].shift(1))
        low_close = abs(df['Low'] - df['Close'].shift(1))
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['ATR'] = true_range.rolling(14).mean()
        
        # Efficiency Ratio
        lookback = 20
        price_change = abs(df['Close'] - df['Close'].shift(lookback))
        path_length = abs(df['Close'].diff()).rolling(lookback).sum()
        df['efficiency_ratio'] = (price_change / (path_length + 1e-10)).clip(0, 1).fillna(0)
        
        # Trend Strength
        up_move = df['High'] - df['High'].shift(1)
        down_move = df['Low'].shift(1) - df['Low']
        
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)
        
        plus_di = plus_dm.ewm(span=14).mean() / (df['ATR'] + 1e-10)
        minus_di = minus_dm.ewm(span=14).mean() / (df['ATR'] + 1e-10)
        
        dx = abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        df['trend_strength'] = dx.ewm(span=14).mean().clip(0, 1).fillna(0)
        
        print("\n📊 Regime Indicator Statistics:")
        print(f"trend_strength: min={df['trend_strength'].min():.3f}, "
              f"median={df['trend_strength'].median():.3f}, "
              f"max={df['trend_strength'].max():.3f}")
        print(f"efficiency_ratio: min={df['efficiency_ratio'].min():.3f}, "
              f"median={df['efficiency_ratio'].median():.3f}, "
              f"max={df['efficiency_ratio'].max():.3f}")
        
        # Check adaptive thresholds
        trend_threshold = df['trend_strength'].quantile(0.70)
        efficiency_threshold = df['efficiency_ratio'].quantile(0.70)
        
        print(f"\n🎯 Adaptive Thresholds (70th percentile):")
        print(f"trend_threshold: {trend_threshold:.3f}")
        print(f"efficiency_threshold: {efficiency_threshold:.3f}")
        
        # Apply simple classification
        trending_mask = (df['trend_strength'] > trend_threshold) & (df['efficiency_ratio'] > efficiency_threshold)
        trending_count = trending_mask.sum()
        
        print(f"\n📈 Bars classified as Trending: {trending_count} ({trending_count/len(df)*100:.1f}%)")
        
        # Check signals in trending bars
        signals_in_trending = signals[signals.index.isin(df[trending_mask].index)]
        print(f"Signals in Trending bars: {len(signals_in_trending)}")
        
        if len(signals_in_trending) == 0:
            print("\n❌ PROBLEM: No signals in Trending bars!")
            print("Either:")
            print("1. Adaptive thresholds too high (need to lower percentile)")
            print("2. Strategy signals don't align with trending detection")
        else:
            print("\n✅ Signals exist in Trending bars")
            
            # Now check confidence for these signal bars
            print("\n🔍 Confidence Diagnostic for Signal Bars:")
            
            for idx in signals_in_trending.index[:5]:  # Check first 5
                bar = df.loc[idx]
                
                # Simple confidence calculation
                confidence = 1.0
                
                # Indicator agreement
                indicators = {
                    'trend': bar['trend_strength'],
                    'efficiency': bar['efficiency_ratio']
                }
                disagreement = np.std(list(indicators.values()))
                confidence -= disagreement * 0.4
                
                # Signal strength penalty
                if bar['trend_strength'] < 0.40:
                    confidence -= 0.20
                
                print(f"\nBar {idx}:")
                print(f"  trend_strength: {bar['trend_strength']:.3f}")
                print(f"  efficiency_ratio: {bar['efficiency_ratio']:.3f}")
                print(f"  disagreement: {disagreement:.3f}")
                print(f"  confidence: {confidence:.3f}")
                print(f"  Would pass 0.30 threshold? {'YES' if confidence >= 0.30 else 'NO'}")
                print(f"  Would pass 0.50 threshold? {'YES' if confidence >= 0.50 else 'NO'}")
        
else:
    print(f"❌ No data found for {symbol} {timeframe}")

print("\n" + "="*80)
print("DIAGNOSIS COMPLETE")
print("="*80)