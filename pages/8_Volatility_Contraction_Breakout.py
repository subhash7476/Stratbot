# pages/14_Volatility_Contraction_Breakout.py
"""
🎯 VCB SCANNER - Volatility Contraction Breakout Strategy
==========================================================
✅ Scans regime-approved stocks from tradable_universe
✅ Detects volatility squeeze → breakout patterns
✅ Volume spike confirmation
✅ Trend alignment (200 EMA)
✅ Full option chain integration with Greeks
✅ Position sizing and risk management
✅ Visual breakout box display

Strategy Logic:
1. Volatility contracts (TR slope negative over N bars)
2. Price breaks out of consolidation box
3. Volume confirms (> 1.5x 20-day MA)
4. Trend aligned (price vs 200 EMA)

Author: Trading Bot Pro
Version: 2.0 (Production)
"""

from __future__ import annotations
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
import json
import os
import sys
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from typing import Optional, List, Dict, Tuple

# Path setup
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database import get_db
from core.option_chain_provider import OptionChainProvider
from core.option_selector import (
    UnderlyingSignal,
    OptionSelection,
    OptionSelector,
    OptionSelectorConfig,
    get_lot_size,
    calculate_position_size
)

# Page config
st.set_page_config(
    page_title="VCB Scanner",
    page_icon="📊",
    layout="wide"
)

# Custom CSS
st.markdown("""
<style>
    .vcb-long { background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); color: white; padding: 8px 16px; border-radius: 8px; font-weight: bold; }
    .vcb-short { background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%); color: white; padding: 8px 16px; border-radius: 8px; font-weight: bold; }
    .vcb-neutral { background: #6c757d; color: white; padding: 8px 16px; border-radius: 8px; }
    .metric-card { background: #f8f9fa; border-radius: 10px; padding: 15px; margin: 5px 0; }
    .signal-box { border: 2px solid #28a745; border-radius: 10px; padding: 15px; margin: 10px 0; }
</style>
""", unsafe_allow_html=True)

# Initialize database
db = get_db()


# ============================================
# INDICATOR FUNCTIONS
# ============================================

def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential Moving Average"""
    return series.ewm(span=span, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range calculation"""
    prev_close = df["Close"].shift(1)
    tr1 = (df["High"] - df["Low"]).abs()
    tr2 = (df["High"] - prev_close).abs()
    tr3 = (df["Low"] - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range"""
    return true_range(df).rolling(period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))


# ============================================
# DATA LOADING
# ============================================

@st.cache_data(ttl=60)
def get_tradable_universe() -> pd.DataFrame:
    """Get regime-approved stocks from tradable_universe"""
    try:
        return db.con.execute("""
            SELECT
                instrument_key,
                trading_symbol,
                direction,
                recommended_strategy,
                regime_class,
                persistence,
                confidence,
                volatility,
                sharpe_ratio,
                regime_maturity
            FROM tradable_universe
            WHERE valid_for_date = CURRENT_DATE
              AND option_buy_ok = TRUE
            ORDER BY persistence DESC, sharpe_ratio DESC
        """).df()
    except Exception as e:
        st.error(f"Error loading tradable universe: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=120)
def load_ohlcv(instrument_key: str, timeframe: str, lookback_days: int) -> pd.DataFrame:
    """Load OHLCV data from DuckDB"""
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    
    query = """
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv_resampled
        WHERE instrument_key = ?
          AND timeframe = ?
          AND timestamp >= ?
        ORDER BY timestamp
    """
    
    try:
        df = db.con.execute(query, [instrument_key, timeframe, start_date]).df()
        
        if df.empty:
            return df
        
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp")
        df.columns = [c.title() for c in df.columns]
        
        return df
    except Exception as e:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def get_fo_stocks() -> pd.DataFrame:
    """Get F&O stocks for single stock analysis"""
    try:
        return db.con.execute("""
            SELECT trading_symbol, instrument_key, lot_size
            FROM fo_stocks_master
            WHERE is_active = TRUE
            ORDER BY trading_symbol
        """).df()
    except:
        return pd.DataFrame()


# ============================================
# VCB STRATEGY LOGIC
# ============================================

def detect_vcb_signal(
    df: pd.DataFrame,
    contraction_bars: int = 12,
    vol_mult: float = 1.5,
    ema_trend: int = 200,
    atr_period: int = 14,
    atr_stop_mult: float = 1.2,
    buffer_pct: float = 0.0005,
    min_rr: float = 2.0
) -> Tuple[Optional[Dict], pd.DataFrame, Dict]:
    """
    Detect Volatility Contraction Breakout signal.
    
    Returns:
        - signal dict or None
        - annotated DataFrame
        - diagnostics dict
    """
    diagnostics = {
        "has_data": False,
        "enough_bars": False,
        "is_contracting": False,
        "volume_ok": False,
        "trend_aligned": False,
        "breakout_detected": False,
        "rr_ok": False
    }
    
    if df.empty:
        return None, df, diagnostics
    
    min_bars = max(ema_trend, contraction_bars + 10, atr_period + 10)
    diagnostics["has_data"] = True
    diagnostics["enough_bars"] = len(df) >= min_bars
    
    if len(df) < min_bars:
        return None, df, diagnostics
    
    # Calculate indicators
    df = df.copy()
    df["EMA_Trend"] = ema(df["Close"], ema_trend)
    df["TR"] = true_range(df)
    df["ATR"] = atr(df, atr_period)
    df["VolMA"] = df["Volume"].rolling(20).mean()
    df["RSI"] = rsi(df["Close"], 14)
    
    # Define contraction box (last N completed bars, exclude current)
    box_df = df.iloc[-(contraction_bars + 1):-1]
    box_high = float(box_df["High"].max())
    box_low = float(box_df["Low"].min())
    box_range = box_high - box_low
    box_range_pct = (box_range / box_low) * 100
    
    # Contraction test: TR slope should be negative (decreasing volatility)
    tr_values = box_df["TR"].fillna(0).values
    tr_slope = np.polyfit(np.arange(len(tr_values)), tr_values, 1)[0]
    is_contracting = tr_slope < 0
    diagnostics["is_contracting"] = is_contracting
    
    # Current bar analysis
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # Volume confirmation
    vol_threshold = last["VolMA"] * vol_mult if pd.notna(last["VolMA"]) else 0
    volume_ok = last["Volume"] > vol_threshold
    diagnostics["volume_ok"] = volume_ok
    
    # Breakout detection
    long_breakout = last["Close"] > box_high * (1 + buffer_pct)
    short_breakout = last["Close"] < box_low * (1 - buffer_pct)
    diagnostics["breakout_detected"] = long_breakout or short_breakout
    
    # Trend alignment
    trend_bullish = last["Close"] > last["EMA_Trend"]
    trend_bearish = last["Close"] < last["EMA_Trend"]
    diagnostics["trend_aligned"] = (long_breakout and trend_bullish) or (short_breakout and trend_bearish)
    
    # Calculate signal strength (0-100)
    strength = 0.0
    strength += 35.0 if is_contracting else 0.0
    strength += 25.0 if volume_ok else 0.0
    strength += 25.0 if diagnostics["trend_aligned"] else 0.0
    strength += 15.0 if diagnostics["breakout_detected"] else 0.0
    
    # ATR value for stop calculation
    atr_val = float(last["ATR"]) if pd.notna(last["ATR"]) else None
    
    if not is_contracting:
        return None, df, diagnostics
    
    # Generate LONG signal
    if long_breakout and trend_bullish and volume_ok and atr_val:
        entry = float(last["Close"])
        stop = min(box_low, entry - atr_stop_mult * atr_val)
        risk = entry - stop
        target = entry + min_rr * risk
        
        rr = (target - entry) / risk if risk > 0 else 0
        diagnostics["rr_ok"] = rr >= min_rr
        
        reason = {
            "type": "VCB_LONG",
            "box_high": round(box_high, 2),
            "box_low": round(box_low, 2),
            "box_range_pct": round(box_range_pct, 2),
            "tr_slope": round(tr_slope, 6),
            "volume": int(last["Volume"]),
            "volume_ma": round(last["VolMA"], 0) if pd.notna(last["VolMA"]) else None,
            "ema_trend": ema_trend,
            "atr": round(atr_val, 2),
            "rsi": round(last["RSI"], 1) if pd.notna(last["RSI"]) else None
        }
        
        signal = {
            "side": "LONG",
            "strength": round(strength, 1),
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "risk": round(risk, 2),
            "rr": round(rr, 2),
            "timestamp": df.index[-1],
            "reason": reason,
            "box_high": box_high,
            "box_low": box_low
        }
        
        return signal, df, diagnostics
    
    # Generate SHORT signal
    if short_breakout and trend_bearish and volume_ok and atr_val:
        entry = float(last["Close"])
        stop = max(box_high, entry + atr_stop_mult * atr_val)
        risk = stop - entry
        target = entry - min_rr * risk
        
        rr = (entry - target) / risk if risk > 0 else 0
        diagnostics["rr_ok"] = rr >= min_rr
        
        reason = {
            "type": "VCB_SHORT",
            "box_high": round(box_high, 2),
            "box_low": round(box_low, 2),
            "box_range_pct": round(box_range_pct, 2),
            "tr_slope": round(tr_slope, 6),
            "volume": int(last["Volume"]),
            "volume_ma": round(last["VolMA"], 0) if pd.notna(last["VolMA"]) else None,
            "ema_trend": ema_trend,
            "atr": round(atr_val, 2),
            "rsi": round(last["RSI"], 1) if pd.notna(last["RSI"]) else None
        }
        
        signal = {
            "side": "SHORT",
            "strength": round(strength, 1),
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "risk": round(risk, 2),
            "rr": round(rr, 2),
            "timestamp": df.index[-1],
            "reason": reason,
            "box_high": box_high,
            "box_low": box_low
        }
        
        return signal, df, diagnostics
    
    return None, df, diagnostics


# ============================================
# VISUALIZATION
# ============================================

def plot_vcb_chart(df: pd.DataFrame, signal: Optional[Dict], symbol: str) -> go.Figure:
    """Create interactive chart showing VCB setup"""
    
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.6, 0.2, 0.2],
        subplot_titles=[f"{symbol} - VCB Setup", "Volume", "RSI"]
    )
    
    # Candlestick
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df["Open"],
        high=df["High"],
        low=df["Low"],
        close=df["Close"],
        name="OHLC",
        increasing_line_color='#26a69a',
        decreasing_line_color='#ef5350'
    ), row=1, col=1)
    
    # EMA Trend
    if "EMA_Trend" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index,
            y=df["EMA_Trend"],
            name="200 EMA",
            line=dict(color="orange", width=1.5)
        ), row=1, col=1)
    
    # Draw breakout box if signal exists
    if signal:
        box_high = signal.get("box_high")
        box_low = signal.get("box_low")
        
        if box_high and box_low:
            # Box rectangle (last 12 bars)
            box_start = df.index[-13]
            box_end = df.index[-1]
            
            fig.add_shape(
                type="rect",
                x0=box_start, x1=box_end,
                y0=box_low, y1=box_high,
                line=dict(color="blue", width=2),
                fillcolor="rgba(0, 100, 255, 0.1)",
                row=1, col=1
            )
            
            # Entry, SL, Target lines
            fig.add_hline(y=signal["entry"], line_dash="solid", line_color="green",
                         annotation_text=f"Entry: {signal['entry']}", row=1, col=1)
            fig.add_hline(y=signal["stop"], line_dash="dash", line_color="red",
                         annotation_text=f"SL: {signal['stop']}", row=1, col=1)
            fig.add_hline(y=signal["target"], line_dash="dash", line_color="blue",
                         annotation_text=f"Target: {signal['target']}", row=1, col=1)
    
    # Volume bars
    colors = ['#26a69a' if c >= o else '#ef5350' for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(go.Bar(
        x=df.index,
        y=df["Volume"],
        name="Volume",
        marker_color=colors,
        opacity=0.7
    ), row=2, col=1)
    
    # Volume MA
    if "VolMA" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index,
            y=df["VolMA"],
            name="Vol MA(20)",
            line=dict(color="purple", width=1)
        ), row=2, col=1)
    
    # RSI
    if "RSI" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index,
            y=df["RSI"],
            name="RSI(14)",
            line=dict(color="purple", width=1.5)
        ), row=3, col=1)
        
        # RSI levels
        fig.add_hline(y=70, line_dash="dash", line_color="red", row=3, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="green", row=3, col=1)
    
    fig.update_layout(
        height=700,
        showlegend=True,
        xaxis_rangeslider_visible=False,
        hovermode='x unified'
    )
    
    fig.update_xaxes(title_text="Time", row=3, col=1)
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    fig.update_yaxes(title_text="RSI", row=3, col=1)
    
    return fig


# ============================================
# OPTION CHAIN PROCESSING
# ============================================

def process_option_chain(signal: UnderlyingSignal, config: OptionSelectorConfig) -> Optional[OptionSelection]:
    """Fetch and process option chain for a signal"""
    try:
        provider = OptionChainProvider()
        chain_dict = provider.fetch_option_chain(signal)
        
        ce_list = chain_dict.get("CE", [])
        pe_list = chain_dict.get("PE", [])
        
        if not ce_list and not pe_list:
            return None
        
        chain_df = pd.DataFrame(ce_list + pe_list)
        
        if chain_df.empty:
            return None
        
        selector = OptionSelector(config)
        return selector.select_option(signal, chain_df)
        
    except Exception as e:
        st.error(f"Option chain error: {e}")
        return None


# ============================================
# MAIN UI
# ============================================

st.title("📊 VCB Scanner - Volatility Contraction Breakout")
st.caption("Scans regime-approved stocks for volatility squeeze → breakout patterns")

# Sidebar configuration
with st.sidebar:
    st.header("⚙️ Scan Settings")
    
    timeframe = st.selectbox("Timeframe", ["5minute", "15minute", "30minute"], index=1)
    lookback_days = st.selectbox("Lookback Days", [30, 60, 90, 120], index=1)
    
    st.divider()
    st.subheader("📐 Strategy Parameters")
    
    contraction_bars = st.slider("Contraction Bars", 8, 20, 12, 
                                  help="Number of bars to form consolidation box")
    ema_trend = st.slider("Trend EMA", 100, 300, 200, 10,
                          help="EMA period for trend filter")
    vol_mult = st.slider("Volume Multiplier", 1.0, 3.0, 1.5, 0.1,
                         help="Volume must exceed this × 20-day MA")
    atr_stop_mult = st.slider("ATR Stop Mult", 0.8, 2.0, 1.2, 0.1,
                              help="Stop loss = Box low - (ATR × this)")
    buffer_pct = st.slider("Breakout Buffer %", 0.0, 0.5, 0.05, 0.01,
                           help="Price must break box by this %") / 100
    min_rr = st.slider("Minimum R:R", 1.5, 3.0, 2.0, 0.1,
                       help="Minimum risk-reward ratio")
    
    st.divider()
    st.subheader("💰 Position Sizing")
    
    capital = st.number_input("Capital (₹)", 50000, 1000000, 100000, 10000)
    risk_pct = st.slider("Risk per Trade %", 0.5, 3.0, 1.5, 0.5)
    
    st.divider()
    st.subheader("🎛️ Option Selection")
    
    min_delta = st.slider("Min Delta", 0.30, 0.50, 0.40, 0.05)
    max_delta = st.slider("Max Delta", 0.50, 0.75, 0.65, 0.05)
    option_sl_pct = st.slider("Option SL %", 15, 40, 25, 5)
    option_target_pct = st.slider("Option Target %", 30, 80, 50, 5)

# Option selector config
option_config = OptionSelectorConfig(
    min_delta=min_delta,
    max_delta=max_delta,
    stop_loss_pct=option_sl_pct,
    target_pct=option_target_pct,
    min_rr=min_rr,
    allow_expiry_day=False,
    last_entry_time="14:45"
)

# Main tabs
tab1, tab2, tab3 = st.tabs(["🔍 Batch Scanner", "📈 Single Stock", "📋 Trade Log"])

# ============================================
# TAB 1: BATCH SCANNER
# ============================================

with tab1:
    st.subheader("🔍 Scan Regime-Approved Universe")
    
    # Load universe
    universe = get_tradable_universe()
    
    if universe.empty:
        st.warning("⚠️ No stocks in tradable universe. Run Daily Regime Analyzer first.")
        st.info("Go to Page 9 (Daily Regime Analyzer) → Batch Scanner → Save shortlist")
        st.stop()
    
    # Universe stats
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Stocks in Universe", len(universe))
    with col2:
        long_only = len(universe[universe['direction'] == 'LONG_ONLY'])
        st.metric("Long Eligible", long_only)
    with col3:
        avg_persistence = universe['persistence'].mean() if 'persistence' in universe.columns else 0
        st.metric("Avg Persistence", f"{avg_persistence:.1f}%")
    with col4:
        st.metric("Timeframe", timeframe)
    
    # Preview universe
    with st.expander("📋 View Tradable Universe", expanded=False):
        st.dataframe(universe, use_container_width=True, height=300)
    
    # Scan button
    if st.button("🚀 Run VCB Scan", type="primary", use_container_width=True):
        
        signals_found = []
        progress = st.progress(0, "Scanning...")
        status = st.empty()
        
        for idx, row in universe.iterrows():
            symbol = row['trading_symbol']
            instrument_key = row['instrument_key']
            direction = row['direction']
            
            status.text(f"Scanning {symbol}... ({idx + 1}/{len(universe)})")
            
            # Load data
            df = load_ohlcv(instrument_key, timeframe, lookback_days)
            
            if df.empty:
                progress.progress((idx + 1) / len(universe))
                continue
            
            # Detect signal
            signal, df_annotated, diag = detect_vcb_signal(
                df,
                contraction_bars=contraction_bars,
                vol_mult=vol_mult,
                ema_trend=ema_trend,
                atr_stop_mult=atr_stop_mult,
                buffer_pct=buffer_pct,
                min_rr=min_rr
            )
            
            if signal:
                # Check direction alignment
                if signal['side'] == 'LONG' and direction != 'LONG_ONLY':
                    progress.progress((idx + 1) / len(universe))
                    continue
                
                if signal['side'] == 'SHORT' and direction != 'SHORT_ONLY':
                    progress.progress((idx + 1) / len(universe))
                    continue
                
                signals_found.append({
                    'Symbol': symbol,
                    'Instrument Key': instrument_key,
                    'Side': signal['side'],
                    'Strength': signal['strength'],
                    'Entry': signal['entry'],
                    'Stop': signal['stop'],
                    'Target': signal['target'],
                    'R:R': signal['rr'],
                    'Risk ₹': signal['risk'],
                    'RSI': signal['reason'].get('rsi', '-'),
                    'Box Range %': signal['reason'].get('box_range_pct', '-'),
                    'Time': signal['timestamp'],
                    'Strategy': row.get('recommended_strategy', 'VCB'),
                    'Regime': row.get('regime_class', '-')
                })
            
            progress.progress((idx + 1) / len(universe))
        
        progress.empty()
        status.empty()
        
        # Display results
        if signals_found:
            st.success(f"✅ Found {len(signals_found)} VCB signals!")
            
            results_df = pd.DataFrame(signals_found).sort_values('Strength', ascending=False)
            
            # Highlight function
            def highlight_side(val):
                if val == 'LONG':
                    return 'background-color: #d4edda; color: #155724'
                elif val == 'SHORT':
                    return 'background-color: #f8d7da; color: #721c24'
                return ''
            
            st.dataframe(
                results_df.style.applymap(highlight_side, subset=['Side']),
                use_container_width=True,
                height=400
            )
            
            # Store in session for option processing
            st.session_state['vcb_signals'] = results_df
            
            # Download
            csv = results_df.to_csv(index=False)
            st.download_button(
                "📥 Download Signals CSV",
                csv,
                f"vcb_signals_{date.today()}.csv",
                use_container_width=True
            )
            
        else:
            st.info("No VCB signals found in current universe. Try adjusting parameters or wait for setups to form.")
    
    # Option Chain Processing
    st.divider()
    st.subheader("🎯 Option Chain Analysis")
    
    if 'vcb_signals' in st.session_state and not st.session_state['vcb_signals'].empty:
        signals_df = st.session_state['vcb_signals']
        
        if st.button("🔗 Fetch Option Chains", type="secondary", use_container_width=True):
            
            option_results = []
            progress = st.progress(0, "Fetching options...")
            
            for idx, row in signals_df.iterrows():
                progress.progress((idx + 1) / len(signals_df), f"Processing {row['Symbol']}...")
                
                # Create UnderlyingSignal
                underlying_signal = UnderlyingSignal(
                    instrument_key=row['Instrument Key'],
                    symbol=row['Symbol'],
                    side=row['Side'],
                    timeframe=timeframe,
                    entry=float(row['Entry']),
                    stop=float(row['Stop']),
                    target=float(row['Target']),
                    strength=float(row['Strength']),
                    strategy="VCB",
                    timestamp=pd.to_datetime(row['Time']),
                    reason={"source": "VCB Scanner"}
                )
                
                # Get option
                option = process_option_chain(underlying_signal, option_config)
                
                if option:
                    lot_size = get_lot_size(row['Symbol'])
                    lots = calculate_position_size(capital, option.ltp, lot_size, risk_pct)
                    investment = option.ltp * lot_size * lots
                    
                    option_results.append({
                        'Symbol': row['Symbol'],
                        'Underlying': row['Side'],
                        'Strike': f"{int(option.strike)} {option.option_type}",
                        'Expiry': option.expiry,
                        'Premium': f"₹{option.ltp:.2f}",
                        'Delta': f"{option.delta:.3f}",
                        'IV': f"{option.iv:.1f}%" if option.iv else "-",
                        'Theta': f"{option.theta:.2f}" if option.theta else "-",
                        'Lot Size': lot_size,
                        'Lots': lots,
                        'Investment': f"₹{investment:,.0f}",
                        'Option SL': f"₹{option.stop_loss_price:.2f}",
                        'Option Target': f"₹{option.target_price:.2f}",
                        'R:R': option.rr
                    })
            
            progress.empty()
            
            if option_results:
                st.success(f"✅ Found options for {len(option_results)} signals")
                
                options_df = pd.DataFrame(option_results)
                st.dataframe(options_df, use_container_width=True)
                
                # Summary
                total_investment = sum([float(r['Investment'].replace('₹', '').replace(',', '')) for r in option_results])
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Total Positions", len(option_results))
                with col2:
                    st.metric("Total Investment", f"₹{total_investment:,.0f}")
                with col3:
                    st.metric("Avg Delta", f"{options_df['Delta'].apply(lambda x: float(x)).mean():.3f}")
                
                # Store for trade log
                st.session_state['vcb_options'] = options_df
            else:
                st.warning("No suitable options found for current signals")
    else:
        st.info("Run the scanner first to find signals, then fetch option chains.")

# ============================================
# TAB 2: SINGLE STOCK ANALYSIS
# ============================================

with tab2:
    st.subheader("📈 Single Stock VCB Analysis")
    
    fo_stocks = get_fo_stocks()
    
    if fo_stocks.empty:
        st.warning("No F&O stocks available")
        st.stop()
    
    col1, col2 = st.columns([1, 3])
    
    with col1:
        symbol_options = {row['trading_symbol']: row['instrument_key'] for _, row in fo_stocks.iterrows()}
        selected_symbol = st.selectbox("Select Stock", list(symbol_options.keys()))
        selected_key = symbol_options[selected_symbol]
        
        analyze_btn = st.button("🔍 Analyze", type="primary", use_container_width=True)
    
    with col2:
        if analyze_btn:
            with st.spinner(f"Analyzing {selected_symbol}..."):
                df = load_ohlcv(selected_key, timeframe, lookback_days)
                
                if df.empty:
                    st.error(f"No data available for {selected_symbol}")
                else:
                    signal, df_annotated, diagnostics = detect_vcb_signal(
                        df,
                        contraction_bars=contraction_bars,
                        vol_mult=vol_mult,
                        ema_trend=ema_trend,
                        atr_stop_mult=atr_stop_mult,
                        buffer_pct=buffer_pct,
                        min_rr=min_rr
                    )
                    
                    # Display diagnostics
                    st.markdown("#### 🔬 Signal Diagnostics")
                    
                    diag_cols = st.columns(7)
                    diag_items = [
                        ("Data", diagnostics["has_data"]),
                        ("Bars", diagnostics["enough_bars"]),
                        ("Contracting", diagnostics["is_contracting"]),
                        ("Volume", diagnostics["volume_ok"]),
                        ("Trend", diagnostics["trend_aligned"]),
                        ("Breakout", diagnostics["breakout_detected"]),
                        ("R:R", diagnostics["rr_ok"])
                    ]
                    
                    for col, (name, ok) in zip(diag_cols, diag_items):
                        emoji = "✅" if ok else "❌"
                        col.metric(name, emoji)
                    
                    # Signal result
                    if signal:
                        st.success(f"🎯 **{signal['side']} SIGNAL** | Strength: {signal['strength']} | R:R: {signal['rr']}")
                        
                        col1, col2, col3, col4 = st.columns(4)
                        col1.metric("Entry", f"₹{signal['entry']:,.2f}")
                        col2.metric("Stop Loss", f"₹{signal['stop']:,.2f}")
                        col3.metric("Target", f"₹{signal['target']:,.2f}")
                        col4.metric("Risk", f"₹{signal['risk']:,.2f}")
                        
                        # Reason details
                        with st.expander("📋 Signal Details"):
                            st.json(signal['reason'])
                    else:
                        st.info("No VCB signal detected. Check diagnostics above.")
                    
                    # Chart
                    st.markdown("#### 📊 Price Chart")
                    fig = plot_vcb_chart(df_annotated.tail(100), signal, selected_symbol)
                    st.plotly_chart(fig, use_container_width=True)
                    
                    # Recent data
                    with st.expander("📋 Recent Candles"):
                        display_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
                        if 'RSI' in df_annotated.columns:
                            display_cols.append('RSI')
                        st.dataframe(df_annotated[display_cols].tail(20).round(2), use_container_width=True)

# ============================================
# TAB 3: TRADE LOG
# ============================================

with tab3:
    st.subheader("📋 VCB Trade Log")
    
    st.info("Trade logging coming soon. Will track executed VCB trades and their outcomes.")
    
    # Placeholder for trade history
    if 'vcb_options' in st.session_state:
        st.markdown("#### Today's Option Selections")
        st.dataframe(st.session_state['vcb_options'], use_container_width=True)

# ============================================
# FOOTER
# ============================================

st.divider()
st.markdown("""
<div style='text-align: center; color: gray; padding: 10px;'>
    <p>VCB Scanner v2.0 | Volatility Contraction Breakout Strategy</p>
    <p>⚠️ For educational purposes only. Not financial advice.</p>
</div>
""", unsafe_allow_html=True)