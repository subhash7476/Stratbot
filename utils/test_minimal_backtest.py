"""
MINIMAL BACKTEST TEST - NO FILTERS, NO UI, NO COMPLEXITY
Just: Load data -> Generate signals -> Execute trades

Run this from command line:
python test_minimal_backtest.py
"""

import pandas as pd
import numpy as np
from pathlib import Path
import sys
import os

# Add root to path
ROOT = Path(__file__).parent.parent if Path(__file__).parent.name == "utils" else Path(__file__).parent
sys.path.insert(0, str(ROOT))

print("="*80)
print("MINIMAL BACKTEST TEST - Finding the problem")
print("="*80)

# 1. Load data
print("\n[1/5] Loading data...")
DATA_DIR = Path("data/derived")
symbol = "RELIANCE"
timeframe = "15minute"

symbol_path = DATA_DIR / symbol / timeframe
parquet_files = list(symbol_path.glob("*.parquet"))

if not parquet_files:
    print(f"❌ ERROR: No data found at {symbol_path}")
    sys.exit(1)

data_file = sorted(parquet_files)[-1]
print(f"   Loading: {data_file}")

df = pd.read_parquet(data_file)
print(f"   ✅ Loaded {len(df)} bars")
print(f"   Date range: {df.index[0]} to {df.index[-1]}")
print(f"   Columns: {list(df.columns)}")

# 2. Generate signals with strategy
print("\n[2/5] Generating signals...")

# Import strategy
try:
    from core.strategies.simple_momentum import simple_momentum_strategy
    print("   ✅ Strategy imported")
except Exception as e:
    print(f"   ❌ ERROR importing strategy: {e}")
    sys.exit(1)

# Run strategy
try:
    df_strategy = simple_momentum_strategy(df.copy(), fast_ema=5, slow_ema=20, rsi_period=14)
    print("   ✅ Strategy executed")
except Exception as e:
    print(f"   ❌ ERROR running strategy: {e}")
    sys.exit(1)

# Check signals
if 'Signal' not in df_strategy.columns:
    print("   ❌ ERROR: No 'Signal' column generated!")
    sys.exit(1)

signals = df_strategy[df_strategy['Signal'] != 0]
print(f"   ✅ Generated {len(signals)} signals")
print(f"      Long: {len(signals[signals['Signal'] == 1])}")
print(f"      Short: {len(signals[signals['Signal'] == -1])}")

if len(signals) == 0:
    print("   ❌ ERROR: Strategy generated ZERO signals!")
    print("   This is the problem - strategy isn't working")
    sys.exit(1)

# 3. Run minimal backtest (NO FILTERS AT ALL)
print("\n[3/5] Running backtest (NO FILTERS)...")

balance = 100000
initial_capital = balance
position = None
trades = []

stop_loss_pct = 0.5
take_profit_pct = 1.5
risk_per_trade_pct = 1.0

for i in range(len(df_strategy)):
    bar = df_strategy.iloc[i]
    
    # Exit logic
    if position is not None:
        side = position['side']
        entry_price = position['entry_price']
        entry_bar = position['entry_bar']
        bars_held = i - entry_bar
        
        if side == 'LONG':
            current_pnl_pct = (bar['Close'] - entry_price) / entry_price * 100
        else:
            current_pnl_pct = (entry_price - bar['Close']) / entry_price * 100
        
        exit_reason = None
        
        if current_pnl_pct <= -stop_loss_pct:
            exit_reason = 'SL'
        elif current_pnl_pct >= take_profit_pct:
            exit_reason = 'TP'
        elif bars_held >= 100:
            exit_reason = 'TIME'
        
        if exit_reason:
            exit_price = bar['Close']
            pnl = (exit_price - entry_price) * position['qty'] if side == 'LONG' else (entry_price - exit_price) * position['qty']
            
            balance += pnl
            
            trades.append({
                'Entry': df_strategy.index[entry_bar],
                'Exit': df_strategy.index[i],
                'Side': side,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'pnl': pnl,
                'pnl_pct': current_pnl_pct,
                'bars_held': bars_held,
                'exit_reason': exit_reason
            })
            
            position = None
    
    # Entry logic (NO FILTERING)
    if position is None:
        signal = bar.get('Signal', 0)
        
        if signal == 1:  # Long
            qty = int((balance * risk_per_trade_pct / 100) / (bar['Close'] * stop_loss_pct / 100))
            qty = max(1, qty)
            
            position = {
                'side': 'LONG',
                'entry_price': bar['Close'],
                'entry_bar': i,
                'qty': qty
            }
            
            if len(trades) < 5:
                print(f"   TRADE #{len(trades)+1}: LONG at {df_strategy.index[i]}, price={bar['Close']:.2f}, qty={qty}")
        
        elif signal == -1:  # Short
            qty = int((balance * risk_per_trade_pct / 100) / (bar['Close'] * stop_loss_pct / 100))
            qty = max(1, qty)
            
            position = {
                'side': 'SHORT',
                'entry_price': bar['Close'],
                'entry_bar': i,
                'qty': qty
            }
            
            if len(trades) < 5:
                print(f"   TRADE #{len(trades)+1}: SHORT at {df_strategy.index[i]}, price={bar['Close']:.2f}, qty={qty}")

print(f"   ✅ Backtest complete: {len(trades)} trades executed")

# 4. Analyze results
print("\n[4/5] Results...")

if len(trades) == 0:
    print("   ❌ ERROR: ZERO trades executed!")
    print("   \nDEBUGGING INFO:")
    print(f"   - Signals generated: {len(signals)}")
    print(f"   - Trades executed: 0")
    print("   \nPossible causes:")
    print("   1. Position sizing results in qty=0 (unlikely)")
    print("   2. Signal column format wrong")
    print("   3. Hidden filter somewhere")
    print("   \nLet's check first 5 signal bars:")
    
    for idx in signals.index[:5]:
        bar_idx = df_strategy.index.get_loc(idx)
        bar = df_strategy.iloc[bar_idx]
        print(f"   Bar {idx}: Signal={bar['Signal']}, Close={bar['Close']:.2f}")
        
        # Calculate what qty would be
        qty = int((balance * risk_per_trade_pct / 100) / (bar['Close'] * stop_loss_pct / 100))
        print(f"      -> Calculated qty: {qty}")
    
    sys.exit(1)

trades_df = pd.DataFrame(trades)

wins = trades_df[trades_df['pnl'] > 0]
losses = trades_df[trades_df['pnl'] <= 0]

win_rate = len(wins) / len(trades_df) * 100 if len(trades_df) > 0 else 0
total_return = ((balance - initial_capital) / initial_capital) * 100

print(f"   Total Trades: {len(trades_df)}")
print(f"   Winners: {len(wins)} ({win_rate:.1f}%)")
print(f"   Losers: {len(losses)}")
print(f"   Final Balance: ${balance:,.2f}")
print(f"   Total Return: {total_return:+.2f}%")

# 5. Conclusion
print("\n[5/5] Conclusion...")

if len(trades_df) > 0:
    print("   ✅ SUCCESS! Minimal backtest WORKS!")
    print("   \nThis proves:")
    print("   - Data loading works")
    print("   - Strategy signal generation works")
    print("   - Backtest execution works")
    print("   \n   The problem is in the UI/filtering layer!")
    print("   \n   Next step: Check which filter is blocking trades in the app")
else:
    print("   ❌ FAILED! Even minimal backtest doesn't work!")
    print("   \n   The problem is more fundamental than filters")

print("\n" + "="*80)
print("TEST COMPLETE")
print("="*80)