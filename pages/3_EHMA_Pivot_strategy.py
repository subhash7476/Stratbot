# pages/3_EHMA_Pivot_Strategy.py
"""
🎯 EHMA PIVOT STRATEGY - MTF EDITION v4.0
==========================================
✅ 60/15/5 Multi-Timeframe Stack
✅ 60m Bias Filter (BULLISH/BEARISH)
✅ 15m Signal Generation
✅ 5m Entry Confirmation
✅ Reduced choppy signals through MTF alignment

NEW IN v4.0:
✅ MTF BATCH SCANNER - Scans all F&O stocks with 60/15/5 alignment
✅ SINGLE STOCK MTF SCAN - Detailed analysis for individual stocks
✅ ALIGNMENT SCORE - Prioritizes best opportunities
✅ BIAS VISUALIZATION - See 60m trend direction

Author: Trading Bot Pro
Version: 4.0 (MTF Edition)
"""

from core.option_selector import (
    UnderlyingSignal,
    OptionSelector,
    OptionSelectorConfig
)
from core.option_chain_provider import OptionChainProvider
from core.strategies.ehma_pivot_strategy import (
    generate_ehma_pivot_signals,
    backtest_ehma_strategy,
    calculate_performance_metrics,
    EHMA_PIVOT_INFO,
    compute_ehma,
    compute_atr,
    compute_rsi,
    compute_60m_bias,
    detect_15m_signals,
    confirm_5m_entry,
    generate_ehma_mtf_signals,
    detect_ehma_signal_mtf_fast,
)
from core.database import get_db
import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
import sys
import os
from datetime import datetime, timedelta, time as dt_time, date
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import warnings
import time
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from core.live_trading_manager import LiveTradingManager
from core.config import get_access_token
import core.live_trading_manager as ltm
warnings.filterwarnings('ignore')


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from core.option_chain_provider import OptionChainProvider
    from core.option_selector import OptionSelector, OptionSelectorConfig, OptionSelection
    OPTIONS_AVAILABLE = True
except ImportError as e:
    OPTIONS_AVAILABLE = False

# Try to import live trading manager
try:
    from core.live_trading_manager import (
        LiveTradingManager,
        is_market_hours,
        get_next_candle_time,
        seconds_until_next_candle
    )
    LIVE_TRADING_AVAILABLE = True
except ImportError as e:
    LIVE_TRADING_AVAILABLE = False
    print(f"Live trading manager not available: {e}")

    st.write(
        "Has start_websocket_if_needed:",
        hasattr(live_manager, "start_websocket_if_needed")
    )


st.set_page_config(page_title="EHMA MTF Strategy",
                   layout="wide", page_icon="📊")

st.markdown("""
<style>
    .mtf-bullish { background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); color: white; padding: 10px 15px; border-radius: 8px; display: inline-block; font-weight: bold; }
    .mtf-bearish { background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%); color: white; padding: 10px 15px; border-radius: 8px; display: inline-block; font-weight: bold; }
    .mtf-neutral { background: linear-gradient(135deg, #606c88 0%, #3f4c6b 100%); color: white; padding: 10px 15px; border-radius: 8px; display: inline-block; font-weight: bold; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] { height: 50px; padding-left: 20px; padding-right: 20px; }
</style>
""", unsafe_allow_html=True)

db = get_db()


# ========================================
# DATABASE FUNCTIONS
# ========================================

def save_signals_to_universe(scan_results: pd.DataFrame) -> int:
    signals = scan_results[scan_results['Signal'].isin(
        ['LONG', 'SHORT'])].copy()
    if signals.empty:
        st.warning("No LONG / SHORT signals to save")
        return 0

    today = date.today()
    saved_count = 0
    errors = []

    for _, row in signals.iterrows():
        try:
            # Safe delete
            db.con.execute("""
                DELETE FROM ehma_universe
                WHERE signal_date = ? AND symbol = ? AND signal_type = ?
            """, [today, row['Symbol'], row['Signal']])

            # Correct column mapping
            db.con.execute("""
                INSERT INTO ehma_universe (
                    signal_date,
                    symbol,
                    instrument_key,
                    signal_type,
                    signal_strength,
                    bars_ago,
                    current_price,
                    entry_price,
                    stop_loss,
                    target_price,
                    rsi,
                    trend,
                    reasons,
                    status,
                    scan_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', CURRENT_TIMESTAMP)
            """, [
                today,
                row['Symbol'],
                row.get('Instrument Key'),
                row['Signal'],
                float(row['Alignment Score']
                      ) if row['Alignment Score'] != '-' else 0,
                int(row.get('Bars Ago', 0)),
                float(row['Price']),
                float(row['Entry']),
                float(row['SL']),
                float(row['TP']),
                float(row['RSI']) if row['RSI'] != '-' else None,
                row['60m Bias'],
                row['Reasons']
            ])

            saved_count += 1

        except Exception as e:
            errors.append(f"{row['Symbol']}: {e}")

    if errors:
        st.error("Some signals failed to save")
        st.code("\n".join(errors[:10]))

    return saved_count


def normalize_live_signals_for_universe(live_df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert live scanner results into universe-compatible format
    """
    if live_df.empty:
        return pd.DataFrame()

    df = live_df.copy()

    df['Symbol'] = df['Symbol']
    df['Signal'] = df['Signal']
    df['Alignment Score'] = pd.to_numeric(
        df['Alignment'], errors='coerce').fillna(0)
    df['Price'] = df['Price']
    df['Entry'] = df['Entry']
    df['SL'] = df['SL']
    df['TP'] = df['TP']
    df['RSI'] = '-'   # RSI not available live
    df['60m Bias'] = df.get('60m Bias', '-')
    df['Reasons'] = 'Live MTF Signal'
    df['Instrument Key'] = df['Instrument Key']
    df['Bars Ago'] = 0

    return df


def load_ehma_universe(signal_date: date = None, status: str = None) -> pd.DataFrame:
    """Load signals from ehma_universe table"""
    if signal_date is None:
        signal_date = date.today()
    query = "SELECT * FROM ehma_universe WHERE signal_date = ?"
    params = [signal_date]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY signal_strength DESC"
    try:
        return db.con.execute(query, params).df()
    except Exception as e:
        return pd.DataFrame()


def update_option_details(symbol: str, signal_type: str, option_data: dict):
    """Update option details for a signal in the universe"""
    today = date.today()
    try:
        db.con.execute("""
            UPDATE ehma_universe SET
                option_type = ?,
                option_instrument_key = ?,
                strike_price = ?,
                expiry_date = ?,
                lot_size = ?,
                option_ltp = ?,
                option_delta = ?,
                option_iv = ?,
                option_theta = ?
            WHERE signal_date = ? AND symbol = ? AND signal_type = ?
        """, [
            option_data.get('option_type'),
            option_data.get('instrument_key'),
            option_data.get('strike'),
            option_data.get('expiry'),
            option_data.get('lot_size'),
            option_data.get('ltp'),
            option_data.get('delta'),
            option_data.get('iv'),
            option_data.get('theta'),
            today, symbol, signal_type
        ])
        return True
    except Exception as e:
        print(f"Error updating option details: {e}")
        return False


# ========================================
# DATA LOADING FUNCTIONS
# ========================================

@st.cache_data(ttl=300)
def get_fo_stocks():
    """Get F&O stocks from master table"""
    query = """
    SELECT DISTINCT f.trading_symbol, f.instrument_key, f.name, f.lot_size, f.is_active
    FROM fo_stocks_master f WHERE f.is_active = TRUE ORDER BY f.trading_symbol
    """
    try:
        return db.con.execute(query).df()
    except Exception as e:
        st.error(f"Error loading F&O stocks: {e}")
        return pd.DataFrame()


def load_data_fast(instrument_key: str, timeframe: str, lookback_days: int = 30) -> pd.DataFrame:
    """Fast data loading for batch scanning"""
    cutoff_date = (datetime.now() - timedelta(days=lookback_days)
                   ).strftime('%Y-%m-%d')
    query = """
    SELECT timestamp, open as Open, high as High, low as Low, close as Close, volume as Volume
    FROM ohlcv_resampled WHERE instrument_key = ? AND timeframe = ? AND timestamp >= ?
    ORDER BY timestamp
    """
    try:
        df = db.con.execute(
            query, [instrument_key, timeframe, cutoff_date]).df()
        if df.empty:
            return None
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        return df
    except Exception:
        return None


def load_mtf_data(instrument_key: str, lookback_days: int = 60):
    """Load all three timeframes for MTF analysis"""
    # NOTE: Database uses '60minute' not '1hour'
    df_60m = load_data_fast(instrument_key, '60minute', lookback_days)
    df_15m = load_data_fast(instrument_key, '15minute', lookback_days)
    df_5m = load_data_fast(instrument_key, '5minute', lookback_days)
    return df_60m, df_15m, df_5m


@st.cache_data(ttl=60)
def get_data_availability(instrument_key: str) -> pd.DataFrame:
    """Check what timeframes are available for a symbol"""
    query = """
    SELECT timeframe, COUNT(*) as candle_count, MIN(DATE(timestamp)) as first_date, MAX(DATE(timestamp)) as last_date
    FROM ohlcv_resampled WHERE instrument_key = ? GROUP BY timeframe ORDER BY timeframe
    """
    try:
        return db.con.execute(query, [instrument_key]).df()
    except:
        return pd.DataFrame()


# ========================================
# OPTION CHAIN FUNCTIONS
# ========================================

# In 3_EHMA_Pivot_strategy.py

# REMOVE this function (lines 335-392):
# def select_best_option(...)

# INSTEAD, use the shared selector:


def get_option_for_ehma_signal(symbol: str, signal_type: str, entry_price: float, sl_price: float, tp_price: float):
    """Use shared option selector for EHMA signals"""

    # Create UnderlyingSignal (same format as VCB)
    signal = UnderlyingSignal(
        instrument_key=get_instrument_key(symbol),
        symbol=symbol,
        side=signal_type,  # "LONG" or "SHORT"
        timeframe="15minute",
        entry=entry_price,
        stop=sl_price,
        target=tp_price,
        strength=0.0,
        strategy="EHMA_MTF",
        timestamp=datetime.now(),
        reason={"source": "EHMA Pivot MTF"}
    )

    # Fetch chain
    provider = OptionChainProvider()
    chain_dict = provider.fetch_option_chain(signal)

    # Convert to DataFrame
    chain_df = pd.DataFrame(chain_dict.get(
        "CE", []) + chain_dict.get("PE", []))

    if chain_df.empty:
        return None

    # Use shared selector
    config = OptionSelectorConfig(
        min_delta=0.40,
        max_delta=0.60,
        stop_loss_pct=20.0,
        target_pct=40.0,
        min_rr=2.0
    )
    selector = OptionSelector(config)

    return selector.select_option(signal, chain_df)


def select_best_option(symbol: str, signal_type: str, spot_price: float, chain: dict) -> dict:
    """Select the best option from the chain"""
    if not chain:
        return None

    opt_type = "CE" if signal_type == "LONG" else "PE"
    options = chain.get(opt_type, [])

    if not options:
        return None

    df = pd.DataFrame(options)
    if df.empty:
        return None

    # Filter by strike proximity to spot (ATM ± 3 strikes)
    df['dist'] = abs(df['strike'] - spot_price)
    df = df.sort_values('dist').head(7)

    # Calculate score based on delta, IV, OI
    df['score'] = 0.0

    # Delta score (prefer 0.45-0.55)
    if 'delta' in df.columns and df['delta'].notna().any():
        df['delta_score'] = 1 - abs(df['delta'].abs() - 0.5) * 2
        df['score'] += df['delta_score'].fillna(0) * 0.4

    # IV score (lower is better)
    if 'iv' in df.columns and df['iv'].notna().any():
        iv_min, iv_max = df['iv'].min(), df['iv'].max()
        if iv_max > iv_min:
            df['iv_score'] = 1 - (df['iv'] - iv_min) / (iv_max - iv_min)
            df['score'] += df['iv_score'].fillna(0) * 0.3

    # OI score (higher is better - liquidity)
    if 'oi' in df.columns and df['oi'].notna().any():
        oi_max = df['oi'].max()
        if oi_max > 0:
            df['oi_score'] = df['oi'] / oi_max
            df['score'] += df['oi_score'].fillna(0) * 0.3

    best = df.sort_values('score', ascending=False).iloc[0]

    return {
        'option_type': opt_type,
        'instrument_key': best.get('instrument_key'),
        'strike': best['strike'],
        'expiry': best.get('expiry'),
        'ltp': best.get('ltp', 0),
        'delta': best.get('delta'),
        'iv': best.get('iv'),
        'theta': best.get('theta'),
        'gamma': best.get('gamma'),
        'vega': best.get('vega'),
        'oi': best.get('oi', 0),
        'volume': best.get('volume', 0),
        'score': best['score']
    }


def get_lot_size(symbol: str) -> int:
    """Get lot size for a symbol from fo_stocks_master"""
    try:
        result = db.con.execute("""
            SELECT lot_size FROM fo_stocks_master 
            WHERE trading_symbol = ? LIMIT 1
        """, [symbol]).fetchone()
        return result[0] if result else 1
    except:
        return 1


# ========================================
# MTF SIGNAL DETECTION
# ========================================

def scan_single_stock_mtf(args: Tuple) -> Dict:
    """Scan a single stock for MTF signals (60/15/5 stack)"""
    symbol, instrument_key, lookback_days, ehma_length, require_bias, require_confirm = args

    try:
        df_60m, df_15m, df_5m = load_mtf_data(instrument_key, lookback_days)

        if df_60m is None or len(df_60m) < 110:
            return {'symbol': symbol, 'status': 'no_60m_data'}
        if df_15m is None or len(df_15m) < 110:
            return {'symbol': symbol, 'status': 'no_15m_data'}
        if df_5m is None or len(df_5m) < 110:
            return {'symbol': symbol, 'status': 'no_5m_data'}

        result = detect_ehma_signal_mtf_fast(
            df_60m=df_60m, df_15m=df_15m, df_5m=df_5m,
            ehma_length=ehma_length, lookback_bars=5,
            require_bias_alignment=require_bias, require_5m_confirmation=require_confirm
        )

        if result is None:
            return {'symbol': symbol, 'status': 'detection_failed'}

        return {'symbol': symbol, 'instrument_key': instrument_key, 'status': 'success', **result}
    except Exception as e:
        return {'symbol': symbol, 'status': 'error', 'error': str(e)}


def run_batch_scan_mtf(fo_stocks: pd.DataFrame, lookback_days: int = 60,
                       ehma_length: int = 16, require_bias: bool = True,
                       require_confirm: bool = True, progress_bar=None) -> pd.DataFrame:
    """Scan all F&O stocks for MTF EHMA signals (60/15/5 stack)"""
    results = []
    total = len(fo_stocks)

    scan_args = [
        (row['trading_symbol'], row['instrument_key'],
         lookback_days, ehma_length, require_bias, require_confirm)
        for _, row in fo_stocks.iterrows()
    ]

    for i, args in enumerate(scan_args):
        result = scan_single_stock_mtf(args)
        results.append(result)
        if progress_bar:
            progress_bar.progress(
                (i + 1) / total, f"Scanning {args[0]}... ({i+1}/{total})")

    processed = []
    for r in results:
        if r['status'] == 'success':
            signals = r.get('signals', [])
            latest_signal = signals[0] if signals else None

            if latest_signal:
                align_score = latest_signal.get('strength', 0)
                align_class = 'HIGH' if align_score >= 1.5 else (
                    'MEDIUM' if align_score >= 1.0 else 'LOW')

                processed.append({
                    'Symbol': r['symbol'],
                    'Status': '🎯' if latest_signal.get('mtf_aligned', False) else '⚪',
                    'Signal': latest_signal['type'],
                    '60m Bias': latest_signal.get('bias_60m', r.get('bias_60m', '-')),
                    'Bias Str': f"{latest_signal.get('bias_strength', 0):.1f}",
                    '5m ✓': '✅' if latest_signal.get('confirmed_5m', False) else '⏳',
                    'Alignment Score': f"{align_score:.2f}",
                    'Align': align_class,
                    'Time': latest_signal['timestamp'],
                    'Price': round(r['latest_price'], 2),
                    'Entry': round(latest_signal['entry_price'], 2),
                    'SL': round(latest_signal['sl_price'], 2),
                    'TP': round(latest_signal['tp_price'], 2),
                    'RSI': round(r['rsi'], 1) if pd.notna(r['rsi']) else '-',
                    'Reasons': ', '.join(latest_signal.get('reasons', [])[:3]),
                    'Instrument Key': r['instrument_key']
                })
            else:
                processed.append({
                    'Symbol': r['symbol'], 'Status': '⚪', 'Signal': '-',
                    '60m Bias': r.get('bias_60m', '-'), 'Bias Str': f"{r.get('bias_strength', 0):.1f}",
                    '5m ✓': '-', 'Alignment Score': '-', 'Align': '-', 'Time': None,
                    'Price': round(r['latest_price'], 2) if pd.notna(r.get('latest_price')) else '-',
                    'Entry': '-', 'SL': '-', 'TP': '-',
                    'RSI': round(r['rsi'], 1) if pd.notna(r.get('rsi')) else '-',
                    'Reasons': 'No aligned signal', 'Instrument Key': r['instrument_key']
                })
        else:
            processed.append({
                'Symbol': r['symbol'], 'Status': '🔴', 'Signal': 'NO DATA',
                '60m Bias': '-', 'Bias Str': '-', '5m ✓': '-', 'Alignment Score': '-',
                'Align': '-', 'Time': None, 'Price': '-', 'Entry': '-', 'SL': '-', 'TP': '-',
                'RSI': '-', 'Reasons': r.get('error', r['status']), 'Instrument Key': '-'
            })

    df = pd.DataFrame(processed)
    df['_has_signal'] = df['Signal'].isin(['LONG', 'SHORT'])
    df['_align_score'] = pd.to_numeric(
        df['Alignment Score'], errors='coerce').fillna(0)
    df = df.sort_values(['_has_signal', '_align_score'],
                        ascending=[False, False])
    df = df.drop(columns=['_has_signal', '_align_score'])
    return df


# ========================================
# SINGLE STOCK DETAILED SCAN
# ========================================

def scan_single_stock_detailed(symbol: str, instrument_key: str, ehma_length: int = 16, lookback_days: int = 60) -> Dict:
    """Detailed MTF analysis for a single stock"""
    result = {'symbol': symbol, 'status': 'unknown',
              'bias_60m': None, 'signals_15m': [], 'tradeable_signals': []}

    df_60m, df_15m, df_5m = load_mtf_data(instrument_key, lookback_days)

    if df_60m is None or len(df_60m) < 110:
        result['status'] = 'insufficient_60m_data'
        return result
    if df_15m is None or len(df_15m) < 110:
        result['status'] = 'insufficient_15m_data'
        return result
    if df_5m is None or len(df_5m) < 110:
        result['status'] = 'insufficient_5m_data'
        return result

    bias = compute_60m_bias(df_60m, ehma_length)
    result['bias_60m'] = {
        'direction': bias.direction, 'strength': bias.strength,
        'mhull': bias.mhull, 'ema100': bias.ema100, 'timestamp': bias.timestamp
    }

    signals_all = detect_15m_signals(
        df_15m, bias, ehma_length=ehma_length, lookback_bars=10)
    result['signals_15m'] = [
        {'type': s.signal_type, 'timestamp': s.timestamp, 'price': s.price,
         'entry_price': s.entry_price, 'sl_price': s.sl_price, 'tp_price': s.tp_price,
         'atr': s.atr, 'rsi': s.rsi, 'strength': s.strength,
         'reasons': s.reasons, 'bias_aligned': s.bias_aligned}
        for s in signals_all
    ]

    tradeable = generate_ehma_mtf_signals(
        df_60m=df_60m, df_15m=df_15m, df_5m=df_5m, symbol=symbol,
        ehma_length=ehma_length, require_bias_alignment=True, require_5m_confirmation=True
    )

    result['tradeable_signals'] = [
        {'type': t.signal_type, 'bias_60m': t.bias_60m, 'bias_strength': t.bias_strength,
         'signal_time_15m': t.signal_time_15m, 'signal_strength': t.signal_strength,
         'confirmed_5m': t.confirmed_5m, 'confirm_time_5m': t.confirm_time_5m,
         'entry_price': t.entry_price, 'sl_price': t.sl_price, 'tp_price': t.tp_price,
         'atr': t.atr, 'rsi': t.rsi, 'reasons': t.reasons, 'alignment_score': t.alignment_score}
        for t in tradeable
    ]

    result['status'] = 'success'
    result['current_price'] = df_15m['Close'].iloc[-1]
    result['current_rsi'] = compute_rsi(df_15m['Close'], 14).iloc[-1]
    return result


# ========================================
# VISUALIZATION
# ========================================

def create_mtf_signal_chart(scan_results: pd.DataFrame) -> go.Figure:
    """Create bar chart of MTF signals by alignment score"""
    signals_df = scan_results[scan_results['Signal'].isin(
        ['LONG', 'SHORT'])].head(30)
    if signals_df.empty:
        return None
    colors = ['#00E676' if s ==
              'LONG' else '#FF1744' for s in signals_df['Signal']]
    scores = pd.to_numeric(
        signals_df['Alignment Score'], errors='coerce').fillna(0)
    fig = go.Figure(data=[go.Bar(
        x=signals_df['Symbol'], y=scores, marker_color=colors,
        text=[f"{s}<br>60m:{b}" for s, b in zip(
            signals_df['Signal'], signals_df['60m Bias'])],
        textposition='auto',
    )])
    fig.update_layout(title='MTF Signal Alignment Scores (Top 30)',
                      xaxis_title='Symbol', yaxis_title='Alignment Score', height=400)
    return fig


def create_bias_distribution_chart(scan_results: pd.DataFrame) -> go.Figure:
    """Create pie chart of 60m bias distribution"""
    bias_counts = scan_results['60m Bias'].value_counts()
    colors = {'BULLISH': '#00E676', 'BEARISH': '#FF1744',
              'NEUTRAL': '#9E9E9E', '-': '#616161'}
    fig = go.Figure(data=[go.Pie(
        labels=bias_counts.index, values=bias_counts.values,
        marker_colors=[colors.get(b, '#616161') for b in bias_counts.index],
        hole=0.4, textinfo='label+percent'
    )])
    fig.update_layout(title='60-Minute Bias Distribution', height=350)
    return fig


# ========================================
# MAIN APP
# ========================================

st.title("📊 EHMA Pivot Strategy - MTF Edition (60/15/5)")

with st.expander("ℹ️ Multi-Timeframe Strategy Information", expanded=False):
    st.markdown(f"""
    **{EHMA_PIVOT_INFO['name']}**
    
    ### 📈 60/15/5 Multi-Timeframe Stack
    | Timeframe | Purpose | Condition |
    |-----------|---------|-----------|
    | **60m** | Bias Filter | MHULL > EMA100 = BULLISH |
    | **15m** | Signal Gen | EHMA crossovers with filters |
    | **5m** | Entry Confirm | MHULL alignment + candle |
    
    **Only takes trades when ALL three timeframes align!**
    """)

fo_stocks = get_fo_stocks()
if fo_stocks.empty:
    st.error("❌ No F&O stocks found in database!")
    st.stop()

# ========================================
# TABS
# ========================================

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "🔍 MTF Batch Scanner", "🔴 Live Scanner", "📈 Single Stock MTF", "💎 EHMA Universe",
    "📈 Options Trading", "📊 Backtest", "📋 Trade Log"
])

# ========================================
# TAB 1: MTF BATCH SCANNER
# ========================================

with tab1:
    st.markdown("### 🔍 Scan All F&O Stocks with 60/15/5 MTF Alignment")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        scan_lookback = st.slider(
            "Lookback Days", 30, 120, 60, key='scan_lb_mtf')
    with col2:
        scan_ehma = st.slider("EHMA Length", 10, 30, 16, key='scan_ehma_mtf')
    with col3:
        require_bias = st.checkbox(
            "Require 60m Bias Alignment", value=True, key='req_bias')
    with col4:
        require_5m = st.checkbox(
            "Require 5m Confirmation", value=True, key='req_5m')

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🚀 Start MTF Scan", type="primary", use_container_width=True):
            progress = st.progress(0, "Initializing MTF scan...")
            start_time = time.time()

            scan_results = run_batch_scan_mtf(
                fo_stocks, lookback_days=scan_lookback, ehma_length=scan_ehma,
                require_bias=require_bias, require_confirm=require_5m, progress_bar=progress
            )

            elapsed = time.time() - start_time
            progress.progress(1.0, f"Scan complete in {elapsed:.1f}s")

            st.session_state['mtf_scan_results'] = scan_results
            st.session_state['mtf_scan_time'] = datetime.now()

    with col2:
        if st.button("💾 Save Signals to Universe", type="secondary", use_container_width=True):
            if 'mtf_scan_results' in st.session_state:
                saved = save_signals_to_universe(
                    st.session_state['mtf_scan_results'])
                st.success(f"✅ Saved {saved} MTF-aligned signals to Universe")

    # Display results
    if 'mtf_scan_results' in st.session_state:
        scan_results = st.session_state['mtf_scan_results']
        scan_time = st.session_state.get('mtf_scan_time', datetime.now())

        st.markdown(
            f"**Last Scan:** {scan_time.strftime('%Y-%m-%d %H:%M:%S')}")

        # Summary metrics
        signals_df = scan_results[scan_results['Signal'].isin(
            ['LONG', 'SHORT'])]
        long_signals = len(signals_df[signals_df['Signal'] == 'LONG'])
        short_signals = len(signals_df[signals_df['Signal'] == 'SHORT'])
        bullish_bias = len(scan_results[scan_results['60m Bias'] == 'BULLISH'])
        bearish_bias = len(scan_results[scan_results['60m Bias'] == 'BEARISH'])

        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total Stocks", len(scan_results))
        col2.metric("🟢 LONG Signals", long_signals)
        col3.metric("🔴 SHORT Signals", short_signals)
        col4.metric("📈 Bullish Bias (60m)", bullish_bias)
        col5.metric("📉 Bearish Bias (60m)", bearish_bias)

        # Charts
        col1, col2 = st.columns(2)
        with col1:
            chart = create_mtf_signal_chart(scan_results)
            if chart:
                st.plotly_chart(chart, use_container_width=True)
        with col2:
            chart = create_bias_distribution_chart(scan_results)
            if chart:
                st.plotly_chart(chart, use_container_width=True)

        # Filter options
        st.markdown("#### 🔍 Filter Results")
        col1, col2, col3 = st.columns(3)
        with col1:
            filter_signal = st.multiselect(
                "Signal Type", ['LONG', 'SHORT', '-'], default=['LONG', 'SHORT'])
        with col2:
            filter_bias = st.multiselect(
                "60m Bias", ['BULLISH', 'BEARISH', 'NEUTRAL', '-'], default=['BULLISH', 'BEARISH'])
        with col3:
            filter_align = st.multiselect(
                "Alignment", ['HIGH', 'MEDIUM', 'LOW', '-'], default=['HIGH', 'MEDIUM'])

        filtered = scan_results[
            (scan_results['Signal'].isin(filter_signal)) &
            (scan_results['60m Bias'].isin(filter_bias)) &
            (scan_results['Align'].isin(filter_align))
        ]

        st.markdown(f"#### 📋 Filtered Results ({len(filtered)} stocks)")
        st.dataframe(filtered, use_container_width=True, height=400)

        # Export
        csv = filtered.to_csv(index=False)
        st.download_button("📥 Export Results", data=csv,
                           file_name=f"mtf_scan_{date.today()}.csv", mime="text/csv")

# ========================================
# TAB 2: LIVE SCANNER (INTRADAY)
# ========================================

with tab2:
    st.markdown("### 🔴 Live Intraday Scanner (60/15/5 MTF)")

    if "live_manager" not in st.session_state:
        try:
            st.session_state["live_manager"] = LiveTradingManager()
        except Exception as e:
            st.session_state["live_manager"] = None
            st.error(f"Failed to initialize LiveTradingManager: {e}")

    live_manager = st.session_state["live_manager"]

    if live_manager:
        access_token = get_access_token()
        if access_token:
            live_manager.start_websocket_if_needed(access_token)

    if live_manager.ws_connected and live_manager.ws_builder:
        ws_time = live_manager.ws_builder.ws_started_at
        st.success(
            f"🟢 WebSocket Connected (since {ws_time.strftime('%H:%M:%S') if ws_time else 'N/A'})")

    else:
        st.warning("🟡 WebSocket not connected (REST fallback)")

    if not LIVE_TRADING_AVAILABLE:
        st.warning(
            "⚠️ Live trading manager not available. Please ensure live_trading_manager.py is in core/")
        st.info("""
        To enable live scanning:
        1. Copy `live_trading_manager.py` to your `core/` folder
        2. Restart the Streamlit app
        """)
    else:
        # Market status
        market_open = is_market_hours()

        col1, col2, col3 = st.columns(3)
        with col1:
            if market_open:
                st.markdown("🟢 **Market is OPEN**")
            else:
                st.markdown("🔴 **Market is CLOSED**")
        with col2:
            next_15m = get_next_candle_time("15minute")
            st.markdown(
                f"⏱️ Next 15m candle: **{next_15m.strftime('%H:%M')}**")
        with col3:
            secs_remaining = seconds_until_next_candle("15minute")
            st.markdown(
                f"⏳ In **{secs_remaining // 60}m {secs_remaining % 60}s**")

        st.divider()

        # Initialize live trading manager
        live_manager = st.session_state["live_manager"]

        if live_manager:
            # Show current data status at the top
            st.markdown("#### 📊 Live Data Status")
            try:
                summary = live_manager.get_live_data_summary()

                col1, col2, col3, col4, col5 = st.columns(5)

                col1.metric(
                    "Instruments",
                    summary.get("instruments_with_data", 0)
                )

                col2.metric(
                    "Total Candles",
                    summary.get("total_candles_today", 0)
                )

                first_candle = summary.get("first_candle")
                if first_candle:
                    col3.metric(
                        "First Candle",
                        pd.to_datetime(first_candle).strftime("%H:%M")
                    )
                else:
                    col3.metric("First Candle", "N/A")

                last_candle = summary.get("last_candle")
                if last_candle:
                    col4.metric(
                        "Last Candle",
                        pd.to_datetime(last_candle).strftime("%H:%M")
                    )
                else:
                    col4.metric("Last Candle", "N/A")

                latest_fetch = summary.get("latest_fetch")
                if latest_fetch:
                    col5.metric(
                        "Last Fetch",
                        pd.to_datetime(latest_fetch).strftime("%H:%M:%S")
                    )
                else:
                    col5.metric("Last Fetch", "Never")

            except Exception as e:
                st.info(
                    "No live data yet. Click 'Initialize Day' first, then 'Refresh Live Data'.")

            st.divider()

            # Control buttons
            col1, col2, col3, col4 = st.columns(4)

            with col1:
                full_refresh = st.checkbox(
                    "Full refresh (ignore cache)", value=False, key='full_refresh')

            with col2:
                pass  # Spacer

            with col3:
                pass  # Spacer

            with col4:
                auto_refresh = st.checkbox(
                    "Auto-refresh (60s)", value=False, key='auto_refresh_live')

            col1, col2, col3 = st.columns(3)

            with col1:
                if st.button("🔄 Refresh Live Data", type="primary", use_container_width=True):
                    incremental = not full_refresh
                    mode_text = "full" if full_refresh else "incremental"

                    with st.spinner(f"Fetching intraday data ({mode_text} mode)..."):
                        progress = st.progress(0, "Starting...")

                        def update_progress(current, total, symbol):
                            progress.progress(
                                current / total, f"Fetching {symbol}... ({current}/{total})")

                        status = live_manager.refresh_live_data(
                            progress_callback=update_progress,
                            incremental=incremental
                        )

                        if status.success:
                            if status.candles_inserted > 0:
                                st.success(
                                    f"✅ Inserted {status.candles_inserted} new candles from {status.instruments_updated} instruments")
                            else:
                                st.info(
                                    "✅ Data is up to date - no new candles to fetch")
                        else:
                            st.warning(
                                f"⚠️ Partial success. Errors: {len(status.errors)}")
                            if status.errors:
                                with st.expander("Show errors"):
                                    for err in status.errors:
                                        st.text(err)

                        st.session_state['live_data_refreshed'] = datetime.now()
                # if st.button("🚑 Fill Today Gap (09:15 → WS start)"):
                #        live_manager.fill_today_gap_if_needed(access_token)
                #        st.success("Today gap fill completed")

            with col2:
                if st.button("📊 Rebuild Resampled", type="secondary", use_container_width=True):
                    with st.spinner("Resampling to 5m/15m/60m..."):
                        live_manager.rebuild_today_resampled()
                        st.success("✅ Resampled data rebuilt for today")
                        st.session_state['resampled_rebuilt'] = datetime.now()

            with col3:
                if st.button("Initialize Day"):
                    if is_market_hours():
                        st.warning(
                            "Initialize Day is disabled during market hours")
                    else:
                        live_manager.initialize_day()
                        st.success("Initialize Day completed")

            st.divider()

            # Live MTF Scan
            st.markdown("#### 🎯 Live MTF Signal Scan")

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                live_ehma = st.slider(
                    "EHMA Length", 10, 30, 16, key='live_ehma')
            with col2:
                live_require_bias = st.checkbox(
                    "Require 60m Bias", value=True, key='live_req_bias')
            with col3:
                live_require_5m = st.checkbox(
                    "Require 5m Confirm", value=True, key='live_req_5m')
            with col4:
                if st.button("💾 Save Live Signals to Universe", use_container_width=True):
                    live_df = st.session_state.get('live_scan_results')

                    if live_df is None or live_df.empty:
                        st.warning("No live signals to save")
                    else:
                        universe_df = normalize_live_signals_for_universe(
                            live_df)
                        saved = save_signals_to_universe(universe_df)
                        st.success(
                            f"✅ Saved {saved} LIVE signals to EHMA Universe")

            if st.button("🚀 Scan Live Signals", type="primary", use_container_width=True):
                progress = st.progress(0, "Scanning...")

                # Get active instruments
                instruments = live_manager.get_active_instruments()
                results = []

                for i, (instrument_key, symbol) in enumerate(instruments):
                    progress.progress(
                        (i + 1) / len(instruments), f"Scanning {symbol}...")

                    try:
                        # Load live MTF data
                        df_60m, df_15m, df_5m = live_manager.get_live_mtf_data(
                            instrument_key, lookback_days=60)

                        if df_60m is None or df_15m is None:
                            continue

                        # Run MTF detection
                        mtf_result = detect_ehma_signal_mtf_fast(
                            df_60m=df_60m,
                            df_15m=df_15m,
                            df_5m=df_5m,
                            ehma_length=live_ehma,
                            lookback_bars=3,  # Only look at last 3 bars for freshness
                            require_bias_alignment=live_require_bias,
                            require_5m_confirmation=live_require_5m
                        )

                        if mtf_result and mtf_result.get('signals'):
                            latest_signal = mtf_result['signals'][0]
                            results.append({
                                'Symbol': symbol,
                                'Signal': latest_signal['type'],
                                '60m Bias': latest_signal.get('bias_60m', mtf_result.get('bias_60m', '-')),
                                '5m ✓': '✅' if latest_signal.get('confirmed_5m', False) else '⏳',
                                'Alignment': f"{latest_signal.get('strength', 0):.2f}",
                                'Price': round(mtf_result['latest_price'], 2),
                                'Entry': round(latest_signal['entry_price'], 2),
                                'SL': round(latest_signal['sl_price'], 2),
                                'TP': round(latest_signal['tp_price'], 2),
                                'Time': latest_signal['timestamp'].strftime('%H:%M') if pd.notna(latest_signal.get('timestamp')) else '-',
                                'Instrument Key': instrument_key
                            })
                    except Exception as e:
                        continue

                progress.progress(1.0, "Scan complete!")

                if results:
                    results_df = pd.DataFrame(results)
                    results_df = results_df.sort_values(
                        'Alignment', ascending=False)

                    # Summary
                    long_count = len(
                        results_df[results_df['Signal'] == 'LONG'])
                    short_count = len(
                        results_df[results_df['Signal'] == 'SHORT'])

                    col1, col2, col3 = st.columns(3)
                    col1.metric("Total Signals", len(results_df))
                    col2.metric("🟢 LONG", long_count)
                    col3.metric("🔴 SHORT", short_count)

                    st.dataframe(
                        results_df, use_container_width=True, height=400)

                    st.session_state['live_scan_results'] = results_df
                    st.session_state['live_scan_time'] = datetime.now()
                else:
                    st.info("No MTF-aligned signals found in live data.")

            # Show last scan results
            if 'live_scan_results' in st.session_state and 'live_scan_time' in st.session_state:
                scan_time = st.session_state['live_scan_time']
                age_seconds = (datetime.now() - scan_time).total_seconds()
                st.caption(
                    f"Last scan: {scan_time.strftime('%H:%M:%S')} ({int(age_seconds)}s ago)")

            # Auto-refresh logic
            if auto_refresh:
                st.info("🔄 Auto-refresh enabled. Page will reload in 60 seconds.")
                time.sleep(1)
                st.rerun()

# ========================================
# TAB 3: SINGLE STOCK MTF SCAN
# ========================================

with tab3:
    st.markdown("### 📈 Single Stock MTF Analysis")

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        symbol_options = {row['trading_symbol']: row['instrument_key']
                          for _, row in fo_stocks.iterrows()}
        selected_symbol = st.selectbox("Select Stock", list(
            symbol_options.keys()), key='single_sym')
        instrument_key = symbol_options[selected_symbol]
    with col2:
        single_ehma = st.slider("EHMA Length", 10, 30, 16, key='single_ehma')
    with col3:
        single_lookback = st.slider(
            "Lookback Days", 30, 120, 60, key='single_lb')

    if st.button("🔍 Analyze Stock", type="primary"):
        with st.spinner(f"Analyzing {selected_symbol}..."):
            result = scan_single_stock_detailed(
                selected_symbol, instrument_key, single_ehma, single_lookback)

        if result['status'] == 'success':
            # 60m Bias Display
            st.markdown("#### 📊 60-Minute Bias (Trend Filter)")
            bias = result['bias_60m']

            bias_color = 'mtf-bullish' if bias['direction'] == 'BULLISH' else (
                'mtf-bearish' if bias['direction'] == 'BEARISH' else 'mtf-neutral')
            st.markdown(
                f"<span class='{bias_color}'>{bias['direction']} (Strength: {bias['strength']:.2f})</span>", unsafe_allow_html=True)

            col1, col2, col3 = st.columns(3)
            col1.metric("MHULL (60m)", f"{bias['mhull']:.2f}")
            col2.metric("EMA100 (60m)", f"{bias['ema100']:.2f}")
            col3.metric(
                "Bias Time", bias['timestamp'].strftime('%Y-%m-%d %H:%M'))

            # 15m Signals
            st.markdown("#### 📈 15-Minute Signals (All)")
            if result['signals_15m']:
                signals_data = []
                for s in result['signals_15m']:
                    signals_data.append({
                        'Type': s['type'],
                        'Time': s['timestamp'],
                        'Price': f"₹{s['price']:.2f}",
                        'RSI': f"{s['rsi']:.1f}" if pd.notna(s['rsi']) else '-',
                        'Strength': f"{s['strength']:.2f}",
                        'Bias Aligned': '✅' if s['bias_aligned'] else '❌',
                        'Reasons': ', '.join(s['reasons'][:2])
                    })
                st.dataframe(pd.DataFrame(signals_data),
                             use_container_width=True)
            else:
                st.info("No 15m signals in lookback period")

            # Tradeable Signals (MTF Aligned)
            st.markdown("#### 🎯 TRADEABLE Signals (MTF Aligned)")
            if result['tradeable_signals']:
                for t in result['tradeable_signals']:
                    signal_emoji = '🟢' if t['type'] == 'LONG' else '🔴'
                    st.markdown(
                        f"### {signal_emoji} **{t['type']}** Signal - Alignment: {t['alignment_score']:.2f}")

                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Entry Price", f"₹{t['entry_price']:.2f}")
                    col2.metric("Stop Loss", f"₹{t['sl_price']:.2f}")
                    col3.metric("Target", f"₹{t['tp_price']:.2f}")
                    col4.metric("RSI", f"{t['rsi']:.1f}" if pd.notna(
                        t['rsi']) else '-')

                    col1, col2, col3 = st.columns(3)
                    col1.metric(
                        "60m Bias", f"{t['bias_60m']} ({t['bias_strength']:.1f})")
                    col2.metric("15m Signal Time",
                                t['signal_time_15m'].strftime('%H:%M'))
                    col3.metric("5m Confirmed",
                                '✅ Yes' if t['confirmed_5m'] else '❌ No')

                    st.markdown(f"**Reasons:** {', '.join(t['reasons'])}")
                    st.divider()
            else:
                st.warning(
                    "⚠️ No tradeable signals with full MTF alignment. Check individual timeframes above.")

            # Current State
            st.markdown("#### 📍 Current State")
            col1, col2 = st.columns(2)
            col1.metric("Current Price", f"₹{result['current_price']:.2f}")
            col2.metric("Current RSI", f"{result['current_rsi']:.1f}")

        else:
            st.error(f"Analysis failed: {result['status']}")

# ========================================
# TAB 4: EHMA UNIVERSE
# ========================================

with tab4:
    st.markdown("### 💎 EHMA Universe - Saved Signals")

    universe_df = load_ehma_universe()
    if not universe_df.empty:
        st.dataframe(universe_df, use_container_width=True, height=400)
    else:
        st.info("No signals saved today. Run a scan and save signals first!")

# ========================================
# TAB 5: OPTIONS TRADING
# ========================================

with tab5:
    st.markdown("### 📈 Options Trading - Auto Select CE/PE")

    if not OPTIONS_AVAILABLE:
        st.warning(
            "⚠️ Option modules not available. Please ensure option_chain_provider.py and option_selector.py are in core/")
    else:
        # Load active signals from universe
        active_signals = load_ehma_universe(date.today(), 'ACTIVE')

        if active_signals.empty:
            st.info(
                "No active signals in universe. Run a scan and save signals first.")
        else:
            st.markdown(
                f"**{len(active_signals)} active signals** ready for options")

            # Configuration
            col1, col2, col3 = st.columns(3)
            with col1:
                capital_per_trade = st.number_input(
                    "Capital per Trade (₹)", 10000, 500000, 50000, key='opt_cap')
            with col2:
                max_positions = st.slider(
                    "Max Positions", 1, 10, 5, key='opt_max')
            with col3:
                delta_range = st.slider(
                    "Delta Range", 0.30, 0.70, (0.40, 0.60), key='opt_delta')

            st.divider()

            # Fetch options for each signal
            if st.button("🔍 Fetch Option Chains", type="primary"):
                progress = st.progress(0)
                options_data = []

                for i, (_, signal) in enumerate(active_signals.head(max_positions).iterrows()):
                    progress.progress((i + 1) / min(len(active_signals), max_positions),
                                      f"Fetching {signal['symbol']}...")

                    try:
                        chain = get_option_chain_for_ehma_signal(
                            signal['symbol'],
                            signal['signal_type'],
                            signal['entry_price']
                        )

                        if chain:
                            best_option = select_best_option(
                                signal['symbol'],
                                signal['signal_type'],
                                signal['entry_price'],
                                chain
                            )

                            if best_option:
                                lot_size = get_lot_size(signal['symbol'])

                                option_entry = {
                                    'symbol': signal['symbol'],
                                    'signal_type': signal['signal_type'],
                                    'spot_price': signal['entry_price'],
                                    'sl': signal['stop_loss'],
                                    'target': signal['target_price'],
                                    **best_option,
                                    'lot_size': lot_size,
                                    'lots': max(1, int(capital_per_trade / (best_option['ltp'] * lot_size))),
                                }
                                options_data.append(option_entry)

                                update_option_details(signal['symbol'], signal['signal_type'], {
                                    **best_option,
                                    'lot_size': lot_size
                                })

                    except Exception as e:
                        st.warning(f"Error with {signal['symbol']}: {e}")

                if options_data:
                    st.session_state['options_data'] = options_data
                    st.success(
                        f"✅ Fetched options for {len(options_data)} signals")
                else:
                    st.warning("No options data fetched")

            # Display options data
            if 'options_data' in st.session_state and st.session_state['options_data']:
                options_data = st.session_state['options_data']

                st.markdown("#### 📋 Option Recommendations")

                options_list = []
                for opt in options_data:
                    opt_type = opt['option_type']
                    emoji = '📈' if opt_type == 'CE' else '📉'
                    investment = opt['ltp'] * opt['lot_size'] * opt['lots']
                    options_list.append({
                        'Symbol': f"{emoji} {opt['symbol']}",
                        'Signal': opt['signal_type'],
                        'Strike': f"{opt['strike']} {opt_type}",
                        'Expiry': opt.get('expiry', 'N/A'),
                        'Premium': f"₹{opt['ltp']:.2f}",
                        'Lots': opt['lots'],
                        'Lot Size': opt['lot_size'],
                        'Investment': f"₹{investment:,.0f}",
                        'Delta': f"{opt.get('delta', 0):.3f}" if opt.get('delta') else "N/A",
                        'IV': f"{opt.get('iv', 0):.1f}%" if opt.get('iv') else "N/A",
                        'Theta': f"{opt.get('theta', 0):.2f}" if opt.get('theta') else "N/A",
                        'OI': f"{opt.get('oi', 0):,}",
                        'Volume': f"{opt.get('volume', 0):,}",
                        'Underlying Entry': f"₹{opt['spot_price']:.2f}",
                        'SL': f"₹{opt['sl']:.2f}",
                        'Target': f"₹{opt['target']:.2f}"
                    })

                if options_list:
                    st.dataframe(pd.DataFrame(options_list),
                                 use_container_width=True)
                    st.divider()

                # Summary table
                st.markdown("#### 📊 Portfolio Summary")
                summary_df = pd.DataFrame(options_data)
                summary_df['investment'] = summary_df['ltp'] * \
                    summary_df['lot_size'] * summary_df['lots']

                total_investment = summary_df['investment'].sum()

                col1, col2, col3 = st.columns(3)
                col1.metric("Total Positions", len(summary_df))
                col2.metric("Total Investment", f"₹{total_investment:,.0f}")
                col3.metric("Avg Premium", f"₹{summary_df['ltp'].mean():.2f}")

                # Export
                csv = summary_df.to_csv(index=False)
                st.download_button("📥 Export Options", data=csv,
                                   file_name=f"ehma_options_{date.today()}.csv", mime="text/csv")

# ========================================
# TAB 6: SINGLE BACKTEST
# ========================================

with tab6:
    st.markdown("### 📊 Single Stock Backtester")

    col1, col2 = st.columns([1, 3])
    with col1:
        bt_symbol_options = {
            row['trading_symbol']: row['instrument_key'] for _, row in fo_stocks.iterrows()}
        bt_selected_symbol = st.selectbox(
            "Select Stock", list(bt_symbol_options.keys()), key='bt_sym')
        bt_instrument_key = bt_symbol_options[bt_selected_symbol]

        availability = get_data_availability(bt_instrument_key)
        available_tfs = availability['timeframe'].tolist(
        ) if not availability.empty else ['15minute']

        bt_timeframe = st.selectbox("Timeframe", available_tfs, key='bt_tf')
        bt_lookback = st.slider("Lookback Days", 30, 365, 90, key='bt_lb')
        bt_ehma = st.slider("EHMA Length", 10, 30, 16, key='bt_ehma')
        bt_capital = st.number_input(
            "Initial Capital", 10000, 10000000, 100000, key='bt_cap')
        run_bt = st.button("🚀 Run Backtest", type="primary", key='run_bt')

    with col2:
        if run_bt:
            df = load_data_fast(bt_instrument_key, bt_timeframe, bt_lookback)

            if df is not None and not df.empty:
                df_signals = generate_ehma_pivot_signals(
                    df.copy(), ehma_length=bt_ehma)
                trades_df, equity_df = backtest_ehma_strategy(
                    df_signals, initial_capital=bt_capital)
                metrics = calculate_performance_metrics(trades_df, equity_df)

                col_a, col_b, col_c, col_d, col_e = st.columns(5)
                col_a.metric("Trades", metrics['Total Trades'])
                col_b.metric("Win Rate", f"{metrics['Win Rate %']:.1f}%")
                col_c.metric("Profit Factor",
                             f"{metrics['Profit Factor']:.2f}")
                col_d.metric("Return", f"{metrics['Total Return %']:.1f}%")
                col_e.metric("Max DD", f"{metrics['Max Drawdown %']:.1f}%")

                if not trades_df.empty:
                    st.dataframe(
                        trades_df, use_container_width=True, height=300)
            else:
                st.error("No data available")

# ========================================
# TAB 7: TRADE LOG
# ========================================

with tab7:
    st.markdown("### 📋 Trade Log & History")
    try:
        history_df = db.con.execute("""
            SELECT signal_date, symbol, signal_type, signal_strength, 
                   entry_price, stop_loss, target_price, status
            FROM ehma_universe
            ORDER BY signal_date DESC, signal_strength DESC
            LIMIT 100
        """).df()

        if not history_df.empty:
            st.dataframe(history_df, use_container_width=True, height=500)
            csv = history_df.to_csv(index=False)
            st.download_button("📥 Export History", data=csv,
                               file_name="ehma_trade_history.csv", mime="text/csv")
        else:
            st.info("No trade history yet.")
    except Exception as e:
        st.error(f"Error loading history: {e}")

# ========================================
# FOOTER
# ========================================

st.divider()
st.markdown("""
<div style='text-align: center; color: gray; padding: 20px;'>
    <p>EHMA Pivot Strategy v4.0 (MTF Edition - 60/15/5 Stack)</p>
    <p>⚠️ For educational purposes only. Trade at your own risk.</p>
</div>
""", unsafe_allow_html=True)
