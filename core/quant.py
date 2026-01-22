# core/quant.py
import pandas as pd
import numpy as np
from core.indicators import compute_supertrend

# Default parameters (tweakable)
DEFAULTS = {
    "atr_period": 10,
    "mult": 3.0,
    "ntz_atr_mult": 1.0,
    "ntz_body_atr": 0.3,
    "breakout_bars": 3,
    "regime_bars": 5,
    "doji_thresh": 0.1,
    "long_wick_ratio": 0.5,
    "conf_w_ewo": 1.0,
    "conf_w_aroon": 1.0,
    "conf_w_atr": 0.5,
    "conf_thresh": 0.5,
    "ml_thresh": 0.6,
    "pattern_lookback": 50,
    "pattern_double_tol": 0.01,
    "pattern_hs_tol": 0.03
}

# -------------------------
# Small helpers (EWO, Aroon)
# -------------------------
def elliott_oscillator(df):
    df = df.copy()
    df['EWO'] = df['Close'].rolling(5).mean() - df['Close'].rolling(35).mean()
    return df

def aroon_oscillator(df, period=14):
    highs = df['High']
    lows = df['Low']
    def pos_last_high(x): return (len(x)-1) - int(np.argmax(x))
    def pos_last_low(x): return (len(x)-1) - int(np.argmin(x))
    aroon_up = 100 * (period - highs.rolling(period).apply(pos_last_high, raw=True)) / period
    aroon_down = 100 * (period - lows.rolling(period).apply(pos_last_low, raw=True)) / period
    df['AroonOsc'] = aroon_up - aroon_down
    return df

# -------------------------
# Modules (copied/cleaned)
# -------------------------
def module_no_trade_zone(df, atr_mult_threshold=1.0, body_atr_ratio=0.3):
    df = df.copy()
    df['body'] = (df['Close'] - df['Open']).abs()
    df['ATR_MA20'] = df['ATR'].rolling(20).mean()
    df['NTZ_blocked'] = (df['ATR'] < (df['ATR_MA20'] * atr_mult_threshold)) | (df['body'] < (body_atr_ratio * df['ATR']))
    return df

def module_breakout_confirm(df, lookback_bars=3):
    df = df.copy()
    df['recent_high'] = df['High'].rolling(lookback_bars).max().shift(1)
    df['recent_low'] = df['Low'].rolling(lookback_bars).min().shift(1)
    df['Breakout_passed'] = False
    df.loc[(df['Signal'] == 1) & (df['Close'] > df['recent_high']), 'Breakout_passed'] = True
    df.loc[(df['Signal'] == -1) & (df['Close'] < df['recent_low']), 'Breakout_passed'] = True
    return df

def module_regime(df, atr_slope_bars=5):
    df = df.copy()
    df['ATR_slope'] = df['ATR'] - df['ATR'].shift(atr_slope_bars)
    df['ST_slope'] = df['Supertrend'] - df['Supertrend'].shift(atr_slope_bars)
    df['Regime_passed'] = (df['ATR_slope'] > 0) & (df['ST_slope'].abs() > 0)
    return df

def module_candle_patterns(df, doji_thresh=0.1, long_wick_ratio=0.5):
    df = df.copy()
    df['body'] = (df['Close'] - df['Open']).abs()
    df['upper_wick'] = df['High'] - df[['Open','Close']].max(axis=1)
    df['lower_wick'] = df[['Open','Close']].min(axis=1) - df['Low']
    df['doji'] = df['body'] < (doji_thresh * df['ATR'])
    df['long_wick'] = ((df['upper_wick'] > long_wick_ratio * df['ATR']) | (df['lower_wick'] > long_wick_ratio * df['ATR']))
    df['Candle_blocked'] = df['doji'] | df['long_wick']
    return df

def module_confidence_score(df, weight_ewo=1.0, weight_aroon=1.0, weight_atr=0.5):
    print("DEBUG: CONFIDENCE MODULE IS RUNNING! -----------------")  # <--- ADD THIS
    df = df.copy()
    
    # 1. Fill NaNs in inputs to prevent score becoming NaN
    # We use 0.0 or forward fill to ensure math works
    ewo_s = (df['EWO'] / (df['ATR'] + 1e-9)).fillna(0)
    aroon_s = (df['AroonOsc'] / 100.0).fillna(0)
    
    atr_std = df['ATR'].rolling(20).std().replace(0, 1)
    atr_s = ((df['ATR'] - df['ATR'].rolling(20).mean()) / atr_std).fillna(0)
    
    # 2. Calculate Raw Score
    score = weight_ewo * ewo_s + weight_aroon * aroon_s + weight_atr * atr_s
    
    # 3. Handle Normalization cleanly
    min_score = score.min()
    max_score = score.max()
    
    # Avoid division by zero if max == min
    if max_score == min_score:
        df['ConfidenceScore'] = 0.0
    else:
        df['ConfidenceScore'] = (score - min_score) / (max_score - min_score + 1e-9)
        
    # 4. FINAL SAFETY: Fill any remaining NaNs with 0.0
    df['ConfidenceScore'] = df['ConfidenceScore'].fillna(0.0)
    
    return df

def module_ml_placeholder(df, threshold=0.6):
    df = df.copy()
    X = pd.DataFrame({'ewo': df['EWO'], 'aroon': df['AroonOsc']})
    prob = 1/(1+np.exp(- (0.01*X['ewo'] + 0.01*X['aroon'])))
    df['ML_prob'] = prob
    df['ML_passed'] = df['ML_prob'] > threshold
    return df

# lightweight pattern detector (double top/bottom + H&S lite)
def module_patterns(df, lookback=50, double_tol_pct=0.01, hs_tolerance=0.03, max_sep=30, confirm_only=False):
    df = df.copy()
    df['pivot_high'] = (df['High'] > df['High'].shift(1)) & (df['High'] > df['High'].shift(-1))
    df['pivot_low'] = (df['Low'] < df['Low'].shift(1)) & (df['Low'] < df['Low'].shift(-1))
    df['Pattern_blocked'] = False

    highs_idx = df.index[df['pivot_high']].tolist()
    lows_idx = df.index[df['pivot_low']].tolist()
    idx_pos = {t:i for i,t in enumerate(df.index)}

    # Double Top
    for i in range(len(highs_idx)-1):
        p1 = highs_idx[i]; p2 = highs_idx[i+1]
        pos1 = idx_pos[p1]; pos2 = idx_pos[p2]; sep = pos2-pos1
        if sep < 2 or sep > max_sep: continue
        price1 = df.at[p1,'High']; price2 = df.at[p2,'High']
        if abs(price1-price2)/max(price1,price2) <= double_tol_pct:
            neckline = df['Low'].iloc[pos1:pos2+1].min()
            for j in range(pos2, min(pos2+lookback, len(df))):
                if df['Close'].iat[j] < neckline:
                    break
                df.at[df.index[j],'Pattern_blocked'] = True

    # Double Bottom (inverse)
    for i in range(len(lows_idx)-1):
        p1 = lows_idx[i]; p2 = lows_idx[i+1]
        pos1 = idx_pos[p1]; pos2 = idx_pos[p2]; sep = pos2-pos1
        if sep < 2 or sep > max_sep: continue
        price1 = df.at[p1,'Low']; price2 = df.at[p2,'Low']
        if abs(price1-price2)/max(price1,price2) <= double_tol_pct:
            neckline = df['High'].iloc[pos1:pos2+1].max()
            for j in range(pos2, min(pos2+lookback, len(df))):
                if df['Close'].iat[j] > neckline:
                    break
                df.at[df.index[j],'Pattern_blocked'] = True

    # Head & Shoulders (lite)
    for i in range(len(highs_idx)-2):
        ls = highs_idx[i]; hd = highs_idx[i+1]; rs = highs_idx[i+2]
        pos_ls = idx_pos[ls]; pos_hd = idx_pos[hd]; pos_rs = idx_pos[rs]
        if not (2 <= pos_hd-pos_ls <= max_sep and 2 <= pos_rs-pos_hd <= max_sep): continue
        p_ls = df.at[ls,'High']; p_hd = df.at[hd,'High']; p_rs = df.at[rs,'High']
        if p_hd > p_ls and p_hd > p_rs and (abs(p_ls - p_rs)/max(p_ls,p_rs) <= hs_tolerance):
            neckline = df['Low'].iloc[pos_ls:pos_rs+1].max()
            for j in range(pos_rs, min(pos_rs+lookback, len(df))):
                if df['Close'].iat[j] < neckline: break
                df.at[df.index[j],'Pattern_blocked'] = True

    df['Pattern_passed'] = ~df['Pattern_blocked']
    return df

# -------------------------
# Main generator
# -------------------------
def generate_signals(df, params=None):
    """
    Input: df with columns ['Open','High','Low','Close','Volume'] and datetime index
    Output: df augmented with indicators and 'FinalSignal' where:
      - 1 = buy
      - -1 = sell
      - 0 = no action
    """
    if params is None:
        params = DEFAULTS
    p = params.copy()
    print("DEBUG: generate_signals STARTED")
    # ensure clean index & sorts
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    # compute supertrend (adds ATR & Supertrend)
    df = compute_supertrend(df, atr_period=p['atr_period'], mult=p['mult'])
    print(f"DEBUG: Supertrend Done. Columns: {list(df.columns)}")
    # EWO & Aroon
    df = elliott_oscillator(df)
    df = aroon_oscillator(df)
    print(f"DEBUG: Oscillators Done. EWO head: {df['EWO'].head(5).tolist()}")
    # basic raw signal (trend flip)
    df['ST_prev'] = df['Trend'].shift(1).fillna(0).astype(int)
    df['Signal'] = 0
    df.loc[(df['ST_prev'] == -1) & (df['Trend'] == 1), 'Signal'] = 1
    df.loc[(df['ST_prev'] == 1) & (df['Trend'] == -1), 'Signal'] = -1

    # default diagnostics
    df['ATR_MA20'] = df['ATR'].rolling(20).mean()
    df['ATR_ok'] = df['ATR'] > df['ATR_MA20']
    df['EWO_pos'] = df['EWO'] > 0
    df['Aroon_pos'] = df['AroonOsc'] > 0

    # apply modules
    df = module_no_trade_zone(df, atr_mult_threshold=p['ntz_atr_mult'], body_atr_ratio=p['ntz_body_atr'])
    df = module_breakout_confirm(df, lookback_bars=p['breakout_bars'])
    df = module_regime(df, atr_slope_bars=p['regime_bars'])
    df = module_candle_patterns(df, doji_thresh=p['doji_thresh'], long_wick_ratio=p['long_wick_ratio'])
    df = module_confidence_score(df, weight_ewo=p['conf_w_ewo'], weight_aroon=p['conf_w_aroon'], weight_atr=p['conf_w_atr'])
    # !!! CRITICAL DEBUG !!!
    print("DEBUG: Calling module_confidence_score...") 
    df = module_confidence_score(df, weight_ewo=p['conf_w_ewo'], weight_aroon=p['conf_w_aroon'], weight_atr=p['conf_w_atr'])
    print(f"DEBUG: Confidence Score Sample: {df['ConfidenceScore'].tail(5).tolist()}") # <--- DEBUG 4

    df['Conf_passed'] = df['ConfidenceScore'] >= p['conf_thresh']
    df = module_ml_placeholder(df, threshold=p['ml_thresh'])
    df = module_patterns(df, lookback=p['pattern_lookback'], double_tol_pct=p['pattern_double_tol'], hs_tolerance=p['pattern_hs_tol'])

    # combine into FinalSignal
    df['FinalSignal'] = 0
    if 'Pattern_passed' not in df.columns:
        df['Pattern_passed'] = True

    # ... (Keep all your previous code up to the 'buy_mask' definition) ...

    # =========================================================
    # 🔍 FILTER AUDIT (Paste this to debug the "Killer")
    # =========================================================
    raw_signals = (df['Signal'] != 0).sum()
    print(f"\n📊 FILTER AUDIT (Total Raw Signals: {raw_signals})")
    print("-" * 40)
    
    # Check how many signals survive each filter individually
    # We look at rows where Signal is ACTIVE (1 or -1)
    sig_mask = df['Signal'] != 0
    
    print(f"1. EWO/Aroon Trend : {((df['EWO_pos'] | ~df['EWO_pos']) & sig_mask).sum()} (Baseline)")
    print(f"2. ATR Volatility  : {(df['ATR_ok'] & sig_mask).sum()} survivors")
    print(f"3. No Trade Zone   : {(~df['NTZ_blocked'] & sig_mask).sum()} survivors")
    print(f"4. Breakout Check  : {(df['Breakout_passed'] & sig_mask).sum()} survivors")
    print(f"5. Regime (Slope)  : {(df['Regime_passed'] & sig_mask).sum()} survivors")
    print(f"6. Candle Pattern  : {(~df['Candle_blocked'] & sig_mask).sum()} survivors")
    print(f"7. Confidence Score: {(df['Conf_passed'] & sig_mask).sum()} survivors")
    
    print("-" * 40)

    # 🛑 TEMPORARY: Relaxed Mask (Only using the safest filters)
    # We comment out the ones likely to cause 0 trades for now
    buy_mask = (
        (df['Signal'] == 1)
        & (~df['NTZ_blocked'])      # Basic Chop Filter
        #& (df['ATR_ok'])          # <--- Often too strict in bull markets
        & (df['Regime_passed'])   # <--- The usual suspect for 0 trades
        #& (df['Breakout_passed'])
        & (df['Conf_passed'])       # We keep this to test your new fix
        # & (~df['Candle_blocked']) # ❌ DISABLED: Kills too many trades (Audit: 22/63 survivors)
    )

    sell_mask = (
        (df['Signal'] == -1)
        & (~df['NTZ_blocked'])
        #& (df['ATR_ok'])
        & (df['Regime_passed'])
        #& (df['Breakout_passed'])
        & (df['Conf_passed'])
        # & (~df['Candle_blocked']) # ❌ DISABLED: Kills too many trades (Audit: 22/63 survivors)
    )
    
    # =========================================================
    
    df.loc[buy_mask, 'FinalSignal'] = 1
    df.loc[sell_mask, 'FinalSignal'] = -1

    # Debug Final Mask Counts
    print(f"DEBUG: Final Buy Signals: {buy_mask.sum()}, Final Sell Signals: {sell_mask.sum()}")

    # final cleanup
    df.drop(columns=['ST_prev'], errors='ignore', inplace=True)
    return df