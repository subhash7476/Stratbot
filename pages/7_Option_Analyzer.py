# pages/13_Option_Analyzer.py
"""
🎯 CENTRAL OPTIONS ANALYZER
============================
Universal options trading hub for all strategy signals

Features:
- Reads signals from ALL strategies (Squeeze, EHMA, VCB, etc.)
- Greeks-based option recommendations using py_vollib
- Multi-strategy signal filtering
- Paper trading integration
- Position tracking and P&L

Architecture:
- Strategy pages (4, 3, 14, etc.) write to unified_signals table
- This page reads and analyzes options for ALL signals
- SignalManager provides centralized signal storage

Author: Trading Bot Pro
Version: 2.0 (Unified Multi-Strategy)
Date: 2026-01-17
"""

# from core.signal_manager import SignalManager, UnifiedSignal, generate_signal_id

# manager = SignalManager()

# signal = UnifiedSignal(
#     signal_id=generate_signal_id("MY_STRATEGY", symbol, timestamp),
#     strategy="MY_STRATEGY",
#     symbol=symbol,
#     instrument_key=instrument_key,
#     signal_type="LONG",
#     timeframe="15minute",
#     timestamp=datetime.now(),
#     entry_price=entry,
#     sl_price=sl,
#     tp_price=tp,
#     score=score,
#     confidence=confidence,
#     reasons="Why signal fired"
# )

# manager.write_signal(signal)


import pandas as pd
import streamlit as st
import numpy as np
from datetime import datetime, date, timedelta
from collections import defaultdict
import sys
import os
from core.signal_manager import SignalManager, UnifiedSignal
from core.signal_to_options import SqueezeToOptionAdapter
from core.database import get_db
from core.option_recommender import OptionRecommender
from core.paper_trading import PaperTradingManager

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def load_css():
    """Load professional CSS styling"""
    css_path = os.path.join(ROOT, "style.css")
    if os.path.exists(css_path):
        with open(css_path) as f:
            st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)


st.set_page_config(
    page_title="Options Analyzer",
    layout="wide",
    page_icon="🎯"
)
load_css()

# ====================================================================
# HEADER
# ====================================================================

st.title("🎯 Universal Options Analyzer")
st.markdown("""
**Central hub for all strategy signals → Options trading with Greeks analysis**

This page aggregates signals from:
- 📊 **Indian Market Squeeze (15m)** - Page 4
- 🌊 **EHMA MTF Strategy** - Page 3
- 📈 **Volatility Contraction Breakout** - Page 14
- ➕ **More strategies** (coming soon)
""")

# ====================================================================
# INITIALIZE
# ====================================================================


def get_signal_manager():
    """
    Get SignalManager instance.
    Not cached because it's lightweight and we need fresh method bindings.
    """
    return SignalManager()


@st.cache_resource
def get_option_recommender():
    """Get cached option recommender instance"""
    return OptionRecommender()


@st.cache_resource
def get_paper_trading_manager():
    """Get cached paper trading manager instance"""
    return PaperTradingManager()


# Initialize managers
# SignalManager is not cached to ensure we always have the latest methods
signal_manager = get_signal_manager()
adapter = SqueezeToOptionAdapter()
recommender = get_option_recommender()
paper_trading = get_paper_trading_manager()
db = get_db()

# ====================================================================
# SIDEBAR - FILTERS & SETTINGS
# ====================================================================

with st.sidebar:
    st.header("⚙️ Settings")

    # Strategy filter
    st.subheader("📊 Strategy Filter")
    all_strategies = st.checkbox("All Strategies", value=True)

    if not all_strategies:
        strategies = st.multiselect(
            "Select Strategies",
            options=["SQUEEZE_15M", "EHMA_MTF", "VCB", "SUPERTREND"],
            default=["SQUEEZE_15M"]
        )
    else:
        strategies = None

    # Signal quality filter
    st.subheader("🎯 Signal Quality")
    min_score = st.slider(
        "Minimum Score",
        min_value=0.0,
        max_value=10.0,
        value=4.0,
        step=0.5,
        help="Filter signals by minimum score (strategy-dependent)"
    )

    min_confidence = st.slider(
        "Minimum Confidence %",
        min_value=0,
        max_value=100,
        value=70,
        step=5
    )

    # Options settings
    st.subheader("💰 Options Settings")
    capital_per_trade = st.number_input(
        "Capital per Trade (₹)",
        min_value=10000,
        max_value=1000000,
        value=50000,
        step=10000
    )

    max_recommendations = st.slider(
        "Options per Signal",
        min_value=1,
        max_value=5,
        value=3
    )

    # Maintenance
    st.subheader("🔧 Maintenance")

    # TP/SL Check Button
    if st.button("🎯 Check TP/SL Hit", use_container_width=True, help="Check if any signals hit TP or SL"):
        with st.spinner("Checking live prices..."):
            result = signal_manager.check_tp_sl_hit()
            if result['updated'] > 0:
                st.success(f"✅ Updated {result['updated']} signals")
                if result['tp_hit']:
                    st.info(f"🎯 TP Hit: {', '.join([s['symbol'] for s in result['tp_hit']])}")
                if result['sl_hit']:
                    st.warning(f"🛑 SL Hit: {', '.join([s['symbol'] for s in result['sl_hit']])}")
                st.rerun()
            else:
                st.info("No signals hit TP/SL yet")

    st.divider()

    # Expire old signals
    expire_hours = st.selectbox(
        "Expire signals older than:",
        options=[6, 12, 24, 48],
        index=2,
        format_func=lambda x: f"{x} hours"
    )

    if st.button(f"⏰ Expire Old Signals (>{expire_hours}h)", use_container_width=True):
        count = signal_manager.expire_old_signals(hours=expire_hours)
        st.success(f"✅ Expired {count} old signals")
        st.rerun()

    st.divider()

    # Manual expiration by symbol
    st.markdown("**Expire by Symbol:**")
    expire_symbol = st.text_input("Symbol to expire", placeholder="e.g., RELIANCE")
    if st.button("❌ Expire Symbol", use_container_width=True, disabled=not expire_symbol):
        if expire_symbol:
            count = signal_manager.expire_signals_by_symbol(expire_symbol)
            if count > 0:
                st.success(f"✅ Expired {count} signals for {expire_symbol}")
                st.rerun()
            else:
                st.warning(f"No active signals found for {expire_symbol}")

    st.divider()

    # Clear all
    if st.button("🗑️ Clear ALL Signals", use_container_width=True, type="secondary"):
        if signal_manager.clear_all_signals():
            st.success("✅ All signals cleared!")
            st.rerun()

    st.divider()

    # Clear cache (for troubleshooting)
    if st.button("🔄 Clear App Cache", use_container_width=True, help="Clear cached data if things aren't working"):
        st.cache_resource.clear()
        st.cache_data.clear()
        st.success("✅ Cache cleared!")
        st.rerun()

    # Database reset (for fatal errors)
    if st.button("🔧 Reset DB Connection", use_container_width=True, type="secondary",
                 help="Force reset database connection after fatal errors"):
        from core.database import force_reset_database
        force_reset_database()
        st.cache_resource.clear()
        st.cache_data.clear()
        # Clear session state DB instance
        for key in list(st.session_state.keys()):
            if 'trading_db' in key.lower():
                del st.session_state[key]
        st.success("✅ Database connection reset! Refresh the page.")
        st.rerun()

# ====================================================================
# MAIN CONTENT
# ====================================================================

# Statistics
st.markdown("### 📊 Signal Statistics")

stats = signal_manager.get_signal_stats()
col1, col2, col3, col4 = st.columns(4)

with col1:
    total_active = stats.get('by_status', {}).get('ACTIVE', 0)
    st.metric("Active Signals", total_active)

with col2:
    total_filled = stats.get('by_status', {}).get('FILLED', 0)
    st.metric("Filled", total_filled)

with col3:
    total_expired = stats.get('by_status', {}).get('EXPIRED', 0)
    st.metric("Expired", total_expired)

with col4:
    total_cancelled = stats.get('by_status', {}).get('CANCELLED', 0)
    st.metric("Cancelled", total_cancelled)

# Strategy breakdown
if stats.get('active_by_strategy'):
    st.markdown("**Active Signals by Strategy:**")
    strategy_cols = st.columns(len(stats['active_by_strategy']))
    for i, (strategy, count) in enumerate(stats['active_by_strategy'].items()):
        strategy_cols[i].metric(strategy.replace('_', ' '), count)

st.divider()

# ====================================================================
# PAPER TRADING STATISTICS & ORDER BOOK
# ====================================================================

st.markdown("### 📈 Paper Trading Overview")

# Get paper trading stats
pt_stats = paper_trading.get_trade_stats()

# Display paper trading metrics
pt_col1, pt_col2, pt_col3, pt_col4, pt_col5 = st.columns(5)

with pt_col1:
    st.metric("Open Positions", pt_stats.get('open_count', 0))

with pt_col2:
    open_capital = pt_stats.get('open_capital', 0)
    st.metric("Capital Deployed", f"₹{open_capital:,.0f}")

with pt_col3:
    closed_count = pt_stats.get('closed_count', 0)
    st.metric("Closed Trades", closed_count)

with pt_col4:
    total_pnl = pt_stats.get('total_pnl', 0)
    delta_color = "normal" if total_pnl >= 0 else "inverse"
    st.metric("Total P&L", f"₹{total_pnl:,.0f}", delta_color=delta_color)

with pt_col5:
    win_rate = pt_stats.get('win_rate', 0)
    st.metric("Win Rate", f"{win_rate:.1f}%")

# Live Order Book with auto-refresh
st.markdown("#### 📊 Live Order Book (Open Positions)")

refresh_col1, refresh_col2 = st.columns([3, 1])
with refresh_col2:
    if st.button("🔄 Refresh P&L", use_container_width=True):
        st.rerun()

# Get open positions with live P&L
open_positions = paper_trading.get_open_positions_with_live_pnl()

if not open_positions.empty:
    # Header row
    hcol1, hcol2, hcol3, hcol4, hcol5, hcol6, hcol7, hcol8, hcol9, hcol10 = st.columns([2, 1.5, 1, 0.8, 1, 1, 0.8, 1.2, 1, 1.5])
    with hcol1:
        st.markdown("**Symbol**")
    with hcol2:
        st.markdown("**Strategy**")
    with hcol3:
        st.markdown("**Type**")
    with hcol4:
        st.markdown("**Strike**")
    with hcol5:
        st.markdown("**Entry**")
    with hcol6:
        st.markdown("**Live LTP**")
    with hcol7:
        st.markdown("**Lots**")
    with hcol8:
        st.markdown("**P&L**")
    with hcol9:
        st.markdown("**P&L %**")
    with hcol10:
        st.markdown("**Action**")

    st.markdown("<hr style='margin: 5px 0; border: none; border-top: 2px solid #ddd;'>", unsafe_allow_html=True)

    # Display each position as a row with inline square off button
    for idx, (i, row) in enumerate(open_positions.iterrows()):
        trade_id = row['trade_id']
        symbol = row['symbol']
        strategy = row['strategy']
        option_type = row['option_type']
        strike = row['strike_price']
        entry_price = float(row['entry_price'])
        live_ltp = float(row['live_ltp'])
        lot_size = int(row['lot_size']) if row['lot_size'] > 0 else 1
        pnl = float(row['unrealized_pnl'])
        pnl_pct = float(row['pnl_pct'])

        # Create columns for each row: data columns + action button
        col1, col2, col3, col4, col5, col6, col7, col8, col9, col10 = st.columns([2, 1.5, 1, 0.8, 1, 1, 0.8, 1.2, 1, 1.5])

        with col1:
            st.text(symbol)
        with col2:
            st.text(strategy[:12])
        with col3:
            st.text(option_type)
        with col4:
            st.text(f"{strike:.0f}")
        with col5:
            st.text(f"₹{entry_price:.2f}")
        with col6:
            st.text(f"₹{live_ltp:.2f}")
        with col7:
            st.text(f"{lot_size}")
        with col8:
            pnl_color = "green" if pnl >= 0 else "red"
            st.markdown(f"<span style='color:{pnl_color}'>₹{pnl:,.0f}</span>", unsafe_allow_html=True)
        with col9:
            pnl_pct_color = "green" if pnl_pct >= 0 else "red"
            st.markdown(f"<span style='color:{pnl_pct_color}'>{pnl_pct:+.1f}%</span>", unsafe_allow_html=True)
        with col10:
            if st.button("❌ Close", key=f"close_{trade_id}", type="secondary"):
                success = paper_trading.square_off_trade(trade_id)
                if success:
                    st.success(f"✅ {symbol} closed! P&L: ₹{pnl:,.2f}")
                    st.rerun()
                else:
                    st.error("❌ Failed to close")

        # Add subtle divider between rows
        if idx < len(open_positions) - 1:
            st.markdown("<hr style='margin: 2px 0; border: none; border-top: 1px solid #eee;'>", unsafe_allow_html=True)

    # Debug expander to show instrument keys
    with st.expander("🔧 Debug: Instrument Keys & Lot Sizes", expanded=False):
        debug_df = open_positions[['trade_id', 'symbol', 'option_instrument_key', 'lot_size', 'quantity']].copy()
        debug_df.columns = ['Trade ID', 'Symbol', 'Instrument Key', 'Lot Size', 'Total Qty']
        st.dataframe(debug_df, use_container_width=True)
        st.caption("If Instrument Key is empty, LTP cannot be fetched. Lot Size=1 means database needs update.")

else:
    st.info(
        "📭 No open positions. Create paper trades from option recommendations below.")

st.divider()

# ====================================================================
# LOAD SIGNALS
# ====================================================================

st.markdown("### 🎯 Active Signals")

# Get signals from unified storage
signals_df = signal_manager.get_signals_for_options(
    min_score=min_score,
    strategies=strategies
)

# Filter by confidence
if not signals_df.empty:
    signals_df = signals_df[signals_df['confidence'] >= min_confidence]

if signals_df.empty:
    st.info("""
    📭 **No active signals found**

    **To generate signals:**
    1. Go to strategy pages (Indian Market Squeeze, EHMA, VCB)
    2. Run scans to generate signals
    3. Signals will automatically appear here

    **Or use test data:**
    """)

    if st.button("🧪 Insert Test Signals", type="secondary"):
        # Insert test signals
        from core.signal_manager import write_squeeze_signal

        test_data = [
            ("RELIANCE", "NSE_EQ|INE002A01018", "LONG", 2450,
             2430, 2490, 5, "SuperTrend bullish, WaveTrend cross"),
            ("TATASTEEL", "NSE_EQ|INE081A01020", "SHORT", 165, 167,
             161, 5, "SuperTrend bearish, WaveTrend cross down"),
            ("HDFCBANK", "NSE_EQ|INE040A01034", "LONG", 1650,
             1635, 1680, 4, "SuperTrend bullish, Momentum rising"),
        ]

        for symbol, key, sig_type, entry, sl, tp, score, reasons in test_data:
            write_squeeze_signal(symbol, key, sig_type,
                                 entry, sl, tp, score, reasons)

        st.success("✅ Test signals inserted!")
        st.rerun()

else:
    st.success(f"**{len(signals_df)} signals ready for options analysis**")

    # Display signals table with management options
    with st.expander("📋 View & Manage All Signals", expanded=False):
        display_df = signals_df[[
            'Symbol', 'Signal', 'Entry', 'SL', 'TP', 'Score',
            'strategy', 'confidence', 'Time'
        ]].copy()

        display_df['Risk'] = abs(display_df['Entry'] - display_df['SL'])
        display_df['Reward'] = abs(display_df['TP'] - display_df['Entry'])
        display_df['R:R'] = (display_df['Reward'] /
                             display_df['Risk']).round(2)

        st.dataframe(display_df, use_container_width=True, height=300)

        # Bulk signal management
        st.markdown("---")
        st.markdown("**Signal Management:**")

        mgmt_col1, mgmt_col2, mgmt_col3 = st.columns(3)

        with mgmt_col1:
            if st.button("🎯 Check TP/SL Now", use_container_width=True):
                with st.spinner("Checking live prices..."):
                    result = signal_manager.check_tp_sl_hit()
                    if result['updated'] > 0:
                        st.success(f"✅ {result['updated']} signals updated")
                        if result['tp_hit']:
                            for s in result['tp_hit']:
                                st.info(f"🎯 {s['symbol']}: TP hit at ₹{s['current']:.2f}")
                        if result['sl_hit']:
                            for s in result['sl_hit']:
                                st.warning(f"🛑 {s['symbol']}: SL hit at ₹{s['current']:.2f}")
                        st.rerun()
                    else:
                        st.info("No signals hit TP/SL")

        with mgmt_col2:
            if st.button("⏰ Expire >6h Old", use_container_width=True):
                count = signal_manager.expire_old_signals(hours=6)
                st.success(f"✅ Expired {count} signals")
                st.rerun()

        with mgmt_col3:
            if st.button("🗑️ Clear All", use_container_width=True, type="secondary"):
                if signal_manager.clear_all_signals():
                    st.success("✅ Cleared!")
                    st.rerun()

        # Individual signal expiration
        st.markdown("---")
        st.markdown("**Expire Individual Signals:**")

        # Get unique symbols
        unique_symbols = signals_df['Symbol'].unique().tolist()

        # Create columns for symbol buttons (max 5 per row)
        for i in range(0, len(unique_symbols), 5):
            symbol_batch = unique_symbols[i:i+5]
            cols = st.columns(len(symbol_batch))

            for j, symbol in enumerate(symbol_batch):
                symbol_count = len(signals_df[signals_df['Symbol'] == symbol])
                with cols[j]:
                    if st.button(f"❌ {symbol} ({symbol_count})", key=f"exp_{symbol}", use_container_width=True):
                        count = signal_manager.expire_signals_by_symbol(symbol)
                        st.success(f"Expired {count} {symbol} signals")
                        st.rerun()

    st.divider()

    # ====================================================================
    # ANALYZE OPTIONS
    # ====================================================================

    st.markdown("### 🎯 Options Analysis")

    if st.button("🚀 Analyze Options for All Signals", type="primary", use_container_width=True):
        with st.spinner("Analyzing options..."):
            all_recommendations = []
            progress = st.progress(0, text="Processing signals...")

            for idx, row in signals_df.iterrows():
                progress.progress((idx + 1) / len(signals_df),
                                  text=f"Analyzing {row['Symbol']}...")

                try:
                    # Convert to UnderlyingSignal
                    underlying_signal = adapter.convert_from_dataframe_row(row)

                    # Get recommendations
                    recommendations = recommender.recommend_for_signal(
                        signal=underlying_signal,
                        max_recommendations=max_recommendations,
                        capital_per_trade=capital_per_trade
                    )

                    # Store with signal info
                    for rec in recommendations:
                        all_recommendations.append({
                            'signal_row': row,
                            'recommendation': rec
                        })

                except Exception as e:
                    st.warning(
                        f"⚠️ Error analyzing {row.get('Symbol', 'Unknown')}: {str(e)}")
                    continue

            progress.empty()

            # Store in session state
            st.session_state["options_recommendations"] = all_recommendations

            if all_recommendations:
                st.success(
                    f"✅ Found {len(all_recommendations)} option recommendations!")
            else:
                st.warning(
                    "❌ No option recommendations found. Check if option chains are available.")

    # ====================================================================
    # DISPLAY RECOMMENDATIONS
    # ====================================================================

    if "options_recommendations" in st.session_state and st.session_state["options_recommendations"]:
        recommendations_list = st.session_state["options_recommendations"]

        st.markdown("---")
        st.markdown("### 🎯 Option Recommendations")

        # Group by signal
        grouped = defaultdict(list)
        for item in recommendations_list:
            symbol = item['signal_row']['Symbol']
            grouped[symbol].append(item)

        # Display each signal's options
        for symbol, items in grouped.items():
            signal_row = items[0]['signal_row']
            strategy_name = signal_row.get(
                'strategy', 'Unknown').replace('_', ' ')

            with st.expander(
                f"🎯 {symbol} - {signal_row['Signal']} | {strategy_name} | Score: {signal_row['Score']} | {signal_row['Time']}",
                expanded=True
            ):
                col_left, col_right = st.columns([1, 2])

                # Left: Signal Details
                with col_left:
                    st.markdown("##### 📊 Underlying Signal")
                    st.metric("Strategy", strategy_name)
                    st.metric("Entry", f"₹{signal_row['Entry']:,.0f}")
                    st.metric("Stop Loss", f"₹{signal_row['SL']:,.0f}")
                    st.metric("Target", f"₹{signal_row['TP']:,.0f}")

                    risk = abs(signal_row['Entry'] - signal_row['SL'])
                    reward = abs(signal_row['TP'] - signal_row['Entry'])
                    rr = reward / risk if risk > 0 else 0
                    st.metric("Risk:Reward", f"1:{rr:.1f}")
                    st.metric("Confidence",
                              f"{signal_row.get('confidence', 0):.0f}%")

                # Right: Options
                with col_right:
                    st.markdown("##### 🎯 Recommended Options")

                    for i, item in enumerate(items):
                        rec = item['recommendation']
                        rank_emoji = "✅" if i == 0 else "⭐" if i == 1 else "📌"

                        st.markdown(
                            f"**{rank_emoji} Option {i+1}: {rec.strike} {rec.option_type}** (Score: {rec.rank_score:.0f}/100)")

                        col_a, col_b, col_c, col_d = st.columns(4)
                        col_a.metric("Premium", f"₹{rec.premium:.2f}")
                        col_b.metric("Delta", f"{rec.delta:.3f}")
                        col_c.metric("IV", f"{rec.iv:.1f}%")
                        col_d.metric("OI", f"{rec.oi:,}")

                        col_e, col_f, col_g, col_h = st.columns(4)
                        col_e.metric(
                            "Capital", f"₹{rec.capital_required:,.0f}")
                        col_f.metric(
                            "Potential", f"₹{rec.potential_return:,.0f}")
                        col_g.metric("ROI", f"{rec.potential_return_pct:.0f}%")
                        col_h.metric("Moneyness", rec.moneyness)

                        st.caption(f"💡 {rec.rank_reason}")

                        # Action buttons
                        btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)

                        # Paper Trade Button
                        if btn_col1.button(f"📝 Paper Trade", key=f"paper_{symbol}_{i}", type="primary"):
                            try:
                                # Get signal_id from session state or signal_row
                                # Generate a temporary signal_id if not available
                                signal_id = f"TEMP_{signal_row.get('strategy', 'UNKNOWN')}_{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

                                # Create paper trade
                                trade = paper_trading.create_trade(
                                    signal_id=signal_id,
                                    symbol=symbol,
                                    strategy=signal_row.get(
                                        'strategy', 'UNKNOWN'),
                                    recommendation=rec
                                )

                                st.success(
                                    f"✅ Paper trade created: {trade.trade_id}")
                                st.info(
                                    f"Entry: ₹{trade.entry_price:.2f} | Qty: {trade.quantity}")
                                st.rerun()

                            except Exception as e:
                                st.error(
                                    f"❌ Error creating paper trade: {str(e)}")

                        if btn_col2.button(f"📈 Live Trade", key=f"trade_{symbol}_{i}"):
                            st.info("Live trade execution coming in next phase")
                        if btn_col3.button(f"📊 Chain", key=f"chain_{symbol}_{i}"):
                            st.info("Full option chain viewer coming soon")
                        if btn_col4.button(f"⏰ Alert", key=f"alert_{symbol}_{i}"):
                            st.info("Price alert feature coming soon")

                        if i < len(items) - 1:
                            st.divider()

        # ====================================================================
        # PORTFOLIO SUMMARY
        # ====================================================================

        st.markdown("---")
        st.markdown("### 📊 Portfolio Summary")

        total_signals = len(grouped)
        total_recommendations = len(recommendations_list)
        total_capital = sum(
            item['recommendation'].capital_required for item in recommendations_list)
        total_potential = sum(
            item['recommendation'].potential_return for item in recommendations_list)
        avg_roi = (total_potential / total_capital *
                   100) if total_capital > 0 else 0

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Signals Analyzed", total_signals)
        col2.metric("Total Options", total_recommendations)
        col3.metric("Total Capital", f"₹{total_capital:,.0f}")
        col4.metric("Avg ROI", f"{avg_roi:.1f}%")

        # Strategy breakdown
        strategy_counts = defaultdict(int)
        for item in recommendations_list:
            strategy = item['signal_row'].get('strategy', 'Unknown')
            strategy_counts[strategy] += 1

        st.markdown("**Options by Strategy:**")
        strat_cols = st.columns(len(strategy_counts))
        for i, (strategy, count) in enumerate(strategy_counts.items()):
            strat_cols[i].metric(strategy.replace('_', ' '), count)

        # Export
        st.divider()
        if st.button("📥 Export Recommendations to CSV", use_container_width=True):
            export_data = []
            for item in recommendations_list:
                rec = item['recommendation']
                sig = item['signal_row']
                export_data.append({
                    'Symbol': sig['Symbol'],
                    'Strategy': sig.get('strategy', ''),
                    'Signal_Type': sig['Signal'],
                    'Signal_Score': sig['Score'],
                    'Signal_Confidence': sig.get('confidence', 0),
                    'Signal_Time': sig['Time'],
                    'Underlying_Entry': sig['Entry'],
                    'Underlying_SL': sig['SL'],
                    'Underlying_TP': sig['TP'],
                    'Option_Strike': rec.strike,
                    'Option_Type': rec.option_type,
                    'Premium': rec.premium,
                    'Delta': rec.delta,
                    'IV': rec.iv,
                    'Theta': rec.theta,
                    'Gamma': rec.gamma,
                    'Vega': rec.vega,
                    'OI': rec.oi,
                    'Volume': rec.volume,
                    'Capital_Required': rec.capital_required,
                    'Potential_Return': rec.potential_return,
                    'ROI_Pct': rec.potential_return_pct,
                    'Rank_Score': rec.rank_score,
                    'Rank_Reason': rec.rank_reason,
                    'Moneyness': rec.moneyness,
                    'Expiry': rec.expiry_date
                })

            export_df = pd.DataFrame(export_data)
            csv = export_df.to_csv(index=False)

            st.download_button(
                "📥 Download CSV",
                data=csv,
                file_name=f"multi_strategy_options_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                use_container_width=True
            )

# ====================================================================
# FOOTER
# ====================================================================

st.divider()
st.markdown("""
### 📚 How It Works

1. **Strategy Pages Generate Signals** → Pages 3, 4, 14, etc. write to unified database
2. **This Page Reads All Signals** → Aggregates from all strategies
3. **Options Analysis** → Greeks-based ranking using py_vollib
4. **Multi-Strategy Portfolio** → Diversified options recommendations

**Signal Flow:**
```
[Strategy Page] → [SignalManager] → [Unified DB] → [Options Analyzer] → [Recommendations]
```

**Next Steps:**
- ✅ Phase 2: Paper trading integration (COMPLETED)
- Phase 3: Live order execution
- Phase 4: Advanced P&L tracking & analytics
""")

# ====================================================================
# TRADE LOG (CLOSED POSITIONS)
# ====================================================================

st.divider()
st.markdown("### 📜 Trade Log (Closed Positions)")

with st.expander("📊 View Trade History", expanded=False):
    closed_trades = paper_trading.get_closed_positions(limit=50)

    if not closed_trades.empty:
        # Display closed trades
        display_closed = closed_trades[[
            'trade_id', 'symbol', 'strategy', 'option_type', 'strike_price',
            'entry_price', 'exit_price', 'quantity', 'realized_pnl',
            'entry_time', 'exit_time'
        ]].copy()

        display_closed.columns = [
            'Trade ID', 'Symbol', 'Strategy', 'Type', 'Strike',
            'Entry', 'Exit', 'Qty', 'Realized P&L',
            'Entry Time', 'Exit Time'
        ]

        # Format columns
        display_closed['Entry'] = display_closed['Entry'].apply(
            lambda x: f"₹{x:.2f}")
        display_closed['Exit'] = display_closed['Exit'].apply(
            lambda x: f"₹{x:.2f}")
        display_closed['Realized P&L'] = display_closed['Realized P&L'].apply(
            lambda x: f"₹{x:+,.2f}" if pd.notna(x) else "N/A"
        )
        display_closed['Entry Time'] = pd.to_datetime(
            display_closed['Entry Time']).dt.strftime('%Y-%m-%d %H:%M')
        display_closed['Exit Time'] = pd.to_datetime(
            display_closed['Exit Time']).dt.strftime('%Y-%m-%d %H:%M')

        st.dataframe(
            display_closed,
            use_container_width=True,
            height=min(400, 50 + len(display_closed) * 35)
        )

        # Trade log statistics
        st.markdown("**Trade Log Statistics:**")
        log_col1, log_col2, log_col3, log_col4 = st.columns(4)

        total_pnl = closed_trades['realized_pnl'].sum()
        wins = (closed_trades['realized_pnl'] > 0).sum()
        losses = (closed_trades['realized_pnl'] < 0).sum()
        win_rate = (wins / len(closed_trades) *
                    100) if len(closed_trades) > 0 else 0

        with log_col1:
            st.metric("Total Trades", len(closed_trades))
        with log_col2:
            st.metric("Total P&L", f"₹{total_pnl:,.2f}")
        with log_col3:
            st.metric("Wins / Losses", f"{wins} / {losses}")
        with log_col4:
            st.metric("Win Rate", f"{win_rate:.1f}%")

        # Export trade log
        if st.button("📥 Export Trade Log to CSV", use_container_width=True):
            csv = closed_trades.to_csv(index=False)
            st.download_button(
                "📥 Download CSV",
                data=csv,
                file_name=f"paper_trade_log_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                use_container_width=True
            )

    else:
        st.info("📭 No closed trades yet. Square off open positions to see them here.")
