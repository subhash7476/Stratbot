"""
Compare Baseline vs Enhanced 10:00 AM Stock Models
===================================================
1. Trains both models from 2023-01-01 to 2025-11-30.
2. Runs a walk-forward simulated backtest on the Test Set (2025-12-01 to 2026-02-26)
   using exactly the same SL/TP/Trailing logic as `stock_daytype_paper.py`.
"""

import sys
import warnings
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings('ignore')

ROOT = Path(__file__).resolve().parent.parent
CANDLE_DIR = ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
LABEL_CSV = ROOT / "data" / "features" / "day_type" / "stocks_universal_labels.csv"

CHECKPOINT_BARS = 45    # 9:15 to 10:00
ENTRY_BARS = 47         # Enter at 10:01
EXIT_BARS = 374         # Exit at 15:28
NF_SYMBOL = 'NSE_INDEX|Nifty 50'

FEATURES_BASE = ["e_ret", "e_range", "e_close_loc"]
FEATURES_ENHANCED = ["e_ret", "e_range", "e_close_loc", "rel_strength", "dist_vwap", "vol_surge"]

def simulate_trade(direction, entry_price, bars_after_entry):
    stop_dist = entry_price * 0.01
    target_dist = entry_price * 0.02
    
    if direction == "long":
        stop_price = entry_price - stop_dist
        target_price = entry_price + target_dist
    else:
        stop_price = entry_price + stop_dist
        target_price = entry_price - target_dist
        
    high_water = entry_price
    low_water = entry_price
    
    highs = bars_after_entry['high'].values
    lows = bars_after_entry['low'].values
    closes = bars_after_entry['close'].values
    
    for h, l in zip(highs, lows):
        if direction == "long":
            high_water = max(high_water, h)
            if high_water >= entry_price + stop_dist:
                stop_price = max(stop_price, high_water - stop_dist)
                
            if l <= stop_price:
                return stop_price
            if h >= target_price:
                return target_price
        else:
            low_water = min(low_water, l)
            if low_water <= entry_price - stop_dist:
                stop_price = min(stop_price, low_water + stop_dist)
                
            if h >= stop_price:
                return stop_price
            if l <= target_price:
                return target_price
                
    return float(closes[-1])

def extract_all_data(symbols, labels_df):
    db_files = sorted(CANDLE_DIR.glob("*.duckdb"))
    
    train_data = []
    test_data = []
    
    labels_df['date'] = pd.to_datetime(labels_df['date']).dt.strftime('%Y-%m-%d')
    label_dict = labels_df.set_index(['date', 'symbol'])['cluster'].to_dict()
    
    sym_placeholder = ",".join(["?"] * (len(symbols) + 1))
    all_syms = symbols + [NF_SYMBOL]
    
    print(f"Scanning {len(db_files)} files to build sets (this may take a minute)...")
    
    for db_path in db_files:
        date_str = db_path.stem
        is_test = "2025-12-01" <= date_str <= "2026-02-26"
        is_train = "2023-01-01" <= date_str <= "2025-11-30"
        
        if not (is_train or is_test):
            continue
            
        try:
            con = duckdb.connect(str(db_path), read_only=True)
            df = con.execute(f"SELECT symbol, timestamp, open, high, low, close, volume FROM candles WHERE symbol IN ({sym_placeholder}) ORDER BY timestamp", all_syms).df()
            con.close()
        except:
            continue
            
        if df.empty: continue
        
        nf_df = df[df["symbol"] == NF_SYMBOL].sort_values('timestamp').reset_index(drop=True)
        if len(nf_df) < CHECKPOINT_BARS: continue
        
        for sym, grp in df[df["symbol"] != NF_SYMBOL].groupby("symbol"):
            cluster = label_dict.get((date_str, sym))
            if is_train and pd.isna(cluster):
                continue
                
            grp = grp.sort_values('timestamp').reset_index(drop=True)
            if len(grp) < CHECKPOINT_BARS: continue
            
            sub = grp.iloc[:CHECKPOINT_BARS]
            nf_sub = nf_df.iloc[:CHECKPOINT_BARS]
            
            s_open = float(sub["open"].iloc[0])
            if s_open == 0: continue
            
            s_curr = float(sub["close"].iloc[-1])
            s_high = float(sub["high"].max())
            s_low  = float(sub["low"].min())
            
            e_ret = (s_curr - s_open) / s_open
            e_range = (s_high - s_low) / s_open
            hl_span = s_high - s_low
            e_close_loc = (s_curr - s_low) / hl_span if hl_span > 1e-9 else 0.5
            
            nf_open = float(nf_sub["open"].iloc[0])
            nf_curr = float(nf_sub["close"].iloc[-1])
            nf_ret = (nf_curr - nf_open) / nf_open if nf_open != 0 else 0
            rel_strength = e_ret - nf_ret
            
            sub_copy = sub.copy()
            sub_copy['pv'] = sub_copy['close'] * sub_copy['volume']
            cum_pv = sub_copy['pv'].sum()
            cum_v = sub_copy['volume'].sum()
            vwap = cum_pv / cum_v if cum_v > 0 else s_curr
            dist_vwap = (s_curr - vwap) / s_curr
            
            base_vol = sub['volume'].iloc[:15].mean()
            curr_vol = sub['volume'].iloc[30:45].mean()
            vol_surge = curr_vol / base_vol if base_vol > 0 else 1.0
            
            row = {
                "date": date_str,
                "symbol": sym,
                "e_ret": e_ret,
                "e_range": e_range,
                "e_close_loc": e_close_loc,
                "rel_strength": rel_strength,
                "dist_vwap": dist_vwap,
                "vol_surge": vol_surge,
                "cluster": cluster
            }
            
            if is_test:
                if len(grp) >= ENTRY_BARS:
                    entry_price = float(grp.iloc[ENTRY_BARS-1]['open'])
                    bars_after = grp.iloc[ENTRY_BARS-1 : EXIT_BARS]
                    
                    row["entry_price"] = entry_price
                    row["long_exit"] = simulate_trade("long", entry_price, bars_after)
                    row["short_exit"] = simulate_trade("short", entry_price, bars_after)
                    test_data.append(row)
            else:
                train_data.append(row)
                
    return pd.DataFrame(train_data), pd.DataFrame(test_data)


def run_backtest(name, model, scaler, features, test_df):
    X_test = scaler.transform(test_df[features])
    proba = model.predict_proba(X_test)
    preds = model.classes_[np.argmax(proba, axis=1)]
    confs = np.max(proba, axis=1)
    
    df = test_df.copy()
    df['pred_cluster'] = preds
    df['conf'] = confs
    
    trades = []
    bull_trades = []
    bear_trades = []
    trade_logs = []
    
    for _, row in df.iterrows():
        # Minimum confidence threshold matched to paper trading
        if row['conf'] < 0.50: continue
        
        direction = None
        if row['pred_cluster'] == 1: direction = "long"
        elif row['pred_cluster'] == 0: direction = "short"
        else: continue # Chop
        
        entry = row['entry_price']
        if direction == "long":
            exit_price = row['long_exit']
            gross_pnl_pct = (exit_price - entry) / entry
        else:
            exit_price = row['short_exit']
            gross_pnl_pct = (entry - exit_price) / entry
            
        net_pnl_pct = gross_pnl_pct - 0.0004 # 0.04% Upstox Equity cost
        
        trades.append(net_pnl_pct)
        if direction == "long": bull_trades.append(net_pnl_pct)
        if direction == "short": bear_trades.append(net_pnl_pct)
        
        trade_logs.append(f"{row['date']} | {row['symbol']:<20} | {direction.upper():<5} | Conf: {row['conf']:.2f} | Entry: {entry:>8.2f} | Exit: {exit_price:>8.2f} | PnL: {net_pnl_pct:>+7.2%}")
        
    trades = np.array(trades)
    bull_trades = np.array(bull_trades)
    bear_trades = np.array(bear_trades)
    
    win_rate = np.mean(trades > 0) if len(trades) > 0 else 0
    mean_ret = np.mean(trades) if len(trades) > 0 else 0
    sum_ret = np.sum(trades) if len(trades) > 0 else 0
    
    b_win = np.mean(bull_trades > 0) if len(bull_trades) > 0 else 0
    b_mean = np.mean(bull_trades) if len(bull_trades) > 0 else 0
    s_win = np.mean(bear_trades > 0) if len(bear_trades) > 0 else 0
    s_mean = np.mean(bear_trades) if len(bear_trades) > 0 else 0
    
    print(f"\n======================================")
    print(f"{name} BACKTEST RESULTS")
    print(f"======================================")
    print(f"Total Trades : {len(trades)}")
    print(f"Win Rate     : {win_rate:.2%}")
    print(f"Avg Net PnL  : {mean_ret:+.3%}")
    print(f"Total Return : {sum_ret:+.2%}")
    print(f"---")
    print(f"Bull Trades  : {len(bull_trades)} (Win: {b_win:.2%}, Avg: {b_mean:+.3%})")
    print(f"Bear Trades  : {len(bear_trades)} (Win: {s_win:.2%}, Avg: {s_mean:+.3%})")
    print(f"---")
    print("TRADE LOG:")
    for log in trade_logs:
        print(log)
    print(f"======================================\n")

def main():
    print(f"Loading labels from {LABEL_CSV.name}...")
    labels_df = pd.read_csv(LABEL_CSV, usecols=["date", "symbol", "cluster"])
    # Top 10 symbols to ensure smooth memory profile and fast execution
    top_symbols = labels_df["symbol"].value_counts().head(10).index.tolist()
    
    train_df, test_df = extract_all_data(top_symbols, labels_df)
    print(f"\nData Extracted -> Train: {len(train_df)} rows | Test (Walk-Forward): {len(test_df)} rows")
    
    # Baseline Model
    scaler_base = StandardScaler()
    X_train_base = scaler_base.fit_transform(train_df[FEATURES_BASE])
    model_base = LogisticRegression(class_weight="balanced", random_state=42)
    model_base.fit(X_train_base, train_df["cluster"])
    
    # Enhanced Model
    scaler_enh = StandardScaler()
    X_train_enh = scaler_enh.fit_transform(train_df[FEATURES_ENHANCED])
    model_enh = LogisticRegression(class_weight="balanced", random_state=42)
    model_enh.fit(X_train_enh, train_df["cluster"])
    
    # Compare
    run_backtest("BASELINE 10 AM (Currently Running)", model_base, scaler_base, FEATURES_BASE, test_df)
    run_backtest("ENHANCED 10 AM", model_enh, scaler_enh, FEATURES_ENHANCED, test_df)

if __name__ == "__main__":
    main()
