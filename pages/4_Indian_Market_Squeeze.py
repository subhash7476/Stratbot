from core.strategies.indian_market_squeeze import (
    build_15m_signals_with_backtest,
    build_15m_signals_for_live_scan,
    BacktestResult,
    SqueezeSignal,
    get_60m_trend_map,  # V4: 60m trend filter
)
from core.option_selector import OptionSelector, OptionSelectorConfig, OptionSelection
from core.option_chain_provider import OptionChainProvider
import core.live_trading_manager as ltm
from core.config import get_access_token
from core.live_trading_manager import LiveTradingManager, is_market_hours, get_next_candle_time, seconds_until_next_candle
from core.database import get_db
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta, date, time as dt_time
import time
import sys
import os
import numpy as np
import pandas as pd
import streamlit as st
from typing import List, Dict
from core.strategies.indian_market_squeeze import build_15m_signals, SqueezeSignal, compute_squeeze_stack
# To ensure that only signals with an alignment score of 5 are considered for backtesting and displayed as tradable active signals, you need to add a filtering step after the signal generation in three key areas of your code:

# 1. ** Batch Scanner(`run_batch_scan_squeeze_15m_v2`)**: Filter both `active_signals` and `completed_trades` from the backtest result.
# 2. ** Live Scanner Tab**: Filter the signals generated for the live scan.
# 3. ** Backtest Tab**: Filter the signals before performing the backtest simulation.

# Here are the specific changes to implement:

#     # pages/3_Indian_Market_Squeeze.py

#     # NEW: Squeeze strategy dependency

st.set_page_config(page_title="Indian Market Squeeze 15m",
                   layout="wide", page_icon="🧨")

db = get_db()

# Verify database connection
if db is None or db.con is None:
    st.error("❌ Database connection failed. Please restart the application.")
    st.stop()


def run_batch_scan_squeeze_15m_v2_improved(
    fo_stocks: pd.DataFrame,
    lookback_days: int = 60,
    sl_mode: str = "ATR based",
    atr_mult: float = 2.2,          # V4-ROBUST: 2.2x ATR (avoid wick stop-outs)
    rr: float = 2.0,                # V4-ROBUST: 2.0 RR (compensate for wider SL)
    sl_pct: float = 0.01,
    tp_pct: float = 0.02,
    progress_bar=None,
    end_dt: Optional[datetime] = None,
    db=None,
    max_trade_bars: int = 40,       # V4-ROBUST: 40 bars (prevent expired trades)

    # NEW PARAMETERS
    max_trades_per_session: int = 1,
    use_breakeven: bool = True,
    breakeven_at_r: float = 1.5,
    use_quality_filter: bool = True,
    min_score: float = 5.0,
    use_trend_filter: bool = True,  # Dual EMA 60m trend filter

    # Data loader function (passed in)
    load_data_fn=None,
    compute_squeeze_fn=None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict]:
    """
    IMPROVED batch scanner V4-Robust with reduced SL hits.

    Key improvements (V4-Robust):
    - 2.2x ATR SL (avoids wick stop-outs in volatile F&O stocks)
    - 2.0 RR target (compensates for wider SL, better win rate)
    - 40 bar max hold (prevents expired trades turning into losses)
    - 10:00 AM start time (skips opening-bell manipulation)
    - Dual EMA 60m trend filter (filters choppy markets)

    Returns:
        Tuple of (active_df, completed_df, summary)
    """
    active_results: List[Dict] = []
    completed_results: List[Dict] = []

    total_stocks = len(fo_stocks)
    stocks_with_signals = 0
    total_completed = 0
    total_active = 0
    aggregate_pnl = 0.0
    aggregate_pnl_pct = 0.0
    total_wins = 0
    total_losses = 0
    total_breakevens = 0

    # Aggregate skip statistics
    total_skipped_session = 0
    total_skipped_cooldown = 0
    total_skipped_quality = 0
    total_skipped_trend = 0  # NEW: Counter-trend signals skipped

    for i, (_, row) in enumerate(fo_stocks.iterrows()):
        symbol = row["trading_symbol"]
        ikey = row["instrument_key"]

        if progress_bar:
            progress_bar.progress(
                (i + 1) / max(total_stocks, 1),
                text=f"Scanning {symbol}... ({i+1}/{total_stocks})",
            )

        try:
            # Load 15m data using provided function or default
            if load_data_fn:
                df_15m = load_data_fn(
                    ikey, "15minute", lookback_days, end_timestamp=end_dt)
            else:
                # Fallback - you'll need to replace this with your actual loader
                from core.api.historical import load_data_fast
                df_15m = load_data_fast(
                    ikey, "15minute", lookback_days, end_timestamp=end_dt)

            if df_15m is None or len(df_15m) < 100:
                continue

            # NEW V4: Load 60m data for trend filtering
            df_60m = None
            if use_trend_filter:
                if load_data_fn:
                    df_60m = load_data_fn(
                        ikey, "60minute", lookback_days, end_timestamp=end_dt)
                else:
                    from core.api.historical import load_data_fast
                    df_60m = load_data_fast(
                        ikey, "60minute", lookback_days, end_timestamp=end_dt)

            mode_str = "ATR" if sl_mode == "ATR based" else "PCT"

            # Get signals WITH improved backtest (V4: includes 60m trend filter)
            result: BacktestResult = build_15m_signals_with_backtest(
                df_15m,
                df_60m=df_60m,  # NEW V4: Pass 60m data for trend filtering
                sl_mode=mode_str,
                sl_atr_mult=atr_mult,
                tp_rr=rr,
                sl_pct=sl_pct,
                tp_pct=tp_pct,
                max_trade_bars=max_trade_bars,
                max_trades_per_session=max_trades_per_session,
                use_breakeven=use_breakeven,
                breakeven_at_r=breakeven_at_r,
                use_quality_filter=use_quality_filter,
                min_score=min_score,
                use_trend_filter=use_trend_filter,  # NEW V4
                compute_squeeze_fn=compute_squeeze_fn,
            )

            # Track skip statistics
            total_skipped_session += result.signals_skipped_session_limit
            total_skipped_cooldown += result.signals_skipped_cooldown
            total_skipped_quality += result.signals_skipped_low_quality
            total_skipped_trend += result.signals_skipped_trend_filter  # NEW V4

            if not result.active_signals and not result.completed_trades:
                continue

            stocks_with_signals += 1

            # Process ACTIVE signals (most recent only)
            if result.active_signals:
                latest_active = result.active_signals[-1]
                total_active += 1
                active_results.append({
                    "Symbol": symbol,
                    "Signal": latest_active.signal_type,
                    "Entry Time": latest_active.timestamp.strftime("%Y-%m-%d %H:%M"),
                    "Entry": round(latest_active.entry_price, 2),
                    "Current": round(df_15m["Close"].iloc[-1], 2),
                    "SL": round(latest_active.sl_price, 2),
                    "TP": round(latest_active.tp_price, 2),
                    "Score": round(latest_active.score, 1),
                    "Instrument Key": ikey,
                })

            # Process COMPLETED trades
            for trade in result.completed_trades:
                total_completed += 1
                aggregate_pnl += trade.pnl
                aggregate_pnl_pct += trade.pnl_pct

                if trade.pnl > 0:
                    total_wins += 1
                else:
                    total_losses += 1

                if trade.status == "BREAKEVEN":
                    total_breakevens += 1

                completed_results.append({
                    "Symbol": symbol,
                    "Signal": trade.signal_type,
                    "Entry Time": trade.timestamp.strftime("%Y-%m-%d %H:%M"),
                    "Exit Time": trade.exit_time.strftime("%Y-%m-%d %H:%M") if trade.exit_time else "-",
                    "Entry": round(trade.entry_price, 2),
                    "Exit": round(trade.exit_price, 2),
                    "SL": round(trade.sl_price, 2),
                    "TP": round(trade.tp_price, 2),
                    "Result": trade.exit_reason,
                    "P&L": round(trade.pnl, 2),
                    "P&L %": round(trade.pnl_pct, 2),
                    "Bars": trade.bars_held,
                    "Score": round(trade.score, 1),
                    "BE": "✓" if trade.breakeven_triggered else "",
                    "Instrument Key": ikey,
                })

        except Exception as e:
            # Uncomment for debugging:
            # print(f"Error scanning {symbol}: {e}")
            continue

    # Create DataFrames
    active_df = pd.DataFrame(active_results)
    completed_df = pd.DataFrame(completed_results)

    # Sort
    if not completed_df.empty:
        completed_df = completed_df.sort_values("P&L", ascending=False)
    if not active_df.empty:
        active_df = active_df.sort_values("Score", ascending=False)

    # Calculate summary statistics
    win_rate = (total_wins / total_completed *
                100) if total_completed > 0 else 0

    summary = {
        "total_stocks_scanned": total_stocks,
        "stocks_with_signals": stocks_with_signals,
        "total_active": total_active,
        "total_completed": total_completed,
        "total_wins": total_wins,
        "total_losses": total_losses,
        "total_breakevens": total_breakevens,
        "win_rate": round(win_rate, 1),
        "total_pnl": round(aggregate_pnl, 2),
        "total_pnl_pct": round(aggregate_pnl_pct, 2),

        # Skip statistics
        "signals_skipped_session_limit": total_skipped_session,
        "signals_skipped_cooldown": total_skipped_cooldown,
        "signals_skipped_quality": total_skipped_quality,
        "signals_skipped_trend_filter": total_skipped_trend,  # NEW V4
    }

    return active_df, completed_df, summary


@st.cache_data(ttl=300)
def get_fo_stocks():
    query = """
    SELECT DISTINCT f.trading_symbol, f.instrument_key, f.name, f.lot_size, f.is_active
    FROM fo_stocks_master f
    WHERE f.is_active = TRUE
    ORDER BY f.trading_symbol
    """
    try:
        return db.con.execute(query).df()
    except Exception as e:
        st.error(f"Error loading F&O stocks: {e}")
        return pd.DataFrame()


def load_data_fast(instrument_key: str, timeframe: str, lookback_days: int = 30, end_timestamp: Optional[datetime] = None) -> Optional[pd.DataFrame]:
    cutoff_date = (datetime.now() - timedelta(days=lookback_days)
                   ).strftime('%Y-%m-%d')
    # build query with optional end timestamp
    if end_timestamp is None:
        query = """
        SELECT timestamp, open as Open, high as High, low as Low, close as Close, volume as Volume
        FROM ohlcv_resampled
        WHERE instrument_key = ? AND timeframe = ? AND timestamp >= ?
        ORDER BY timestamp
        """
        params = [instrument_key, timeframe, cutoff_date]
    else:
        query = """
        SELECT timestamp, open as Open, high as High, low as Low, close as Close, volume as Volume
        FROM ohlcv_resampled
        WHERE instrument_key = ? AND timeframe = ? AND timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp
        """
        # format end timestamp to DB-friendly string
        end_str = end_timestamp.strftime('%Y-%m-%d %H:%M:%S')
        params = [instrument_key, timeframe, cutoff_date, end_str]
    try:
        df = db.con.execute(query, params).df()
        if df.empty:
            return None
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.set_index("timestamp", inplace=True)
        return df
    except Exception:
        return None


def save_signals_to_universe(scan_results: pd.DataFrame) -> int:
    # This function will now implicitly only save signals with score 5,
    # as scan_results will already be filtered by the calling functions.
    signals = scan_results[scan_results["Signal"].isin(
        ["LONG", "SHORT"])].copy()
    if signals.empty:
        st.warning("No LONG / SHORT signals to save")
        return 0

    today = date.today()
    saved_count = 0
    errors: List[str] = []

    for _, row in signals.iterrows():
        try:
            db.con.execute(
                """
                DELETE FROM ehma_universe
                WHERE signal_date = ? AND symbol = ? AND signal_type = ?
                """,
                [today, row["Symbol"], row["Signal"]],
            )
            db.con.execute(
                """
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
                """,
                [
                    today,
                    row["Symbol"],
                    row.get("Instrument Key"),
                    row["Signal"],  # signal_type is VARCHAR: "LONG" or "SHORT"
                    float(row.get("Alignment Score", 0)
                          or 0),  # signal_strength
                    int(row.get("Bars Ago", 0) or 0),
                    float(row["Price"]),
                    float(row["Entry"]),
                    float(row["SL"]),
                    float(row["TP"]),
                    None,  # RSI not computed here
                    row.get("Trend", "-"),
                    row["Reasons"],
                ],
            )
            saved_count += 1
        except Exception as e:
            errors.append(f"{row['Symbol']}: {e}")

    if errors:
        st.error("Some signals failed to save")
        st.code("\n".join(errors[:10]))
    return saved_count


st.title("🧨 Indian Market Squeeze – 15m Stack")

fo_stocks = get_fo_stocks()
if fo_stocks.empty:
    st.error("No F&O stocks found in database!")
    st.stop()

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs(
    [
        "🔍 15m Batch Scanner",
        "🔴 Live Scanner",
        "📈 Single Stock 15m",
        "💎 Squeeze Universe",
        "📈 Options Trading",
        "📊 Backtest",
        "📋 Trade Log",
        "🤖 ML Analysis",
    ]
)

# ============================
# TAB 1: 15m BATCH SCANNER
# ============================
with tab1:
    st.markdown("### 🔍 Scan All F&O Stocks with 15m Squeeze V2")
    st.caption("✨ Improved with session limits, breakeven stops & quality filters")

    # =========================================================================
    # ROW 1: Basic Settings
    # =========================================================================
    col1, col2, col3 = st.columns(3)
    with col1:
        scan_lookback = st.slider(
            "Lookback Days", 30, 120, 60, key="sq_scan_lb")
    with col2:
        sl_mode = st.selectbox(
            "SL / TP Mode",
            ["ATR based", "Fixed %"],
            key="sq_scan_slmode",
        )
    with col3:
        # OPTIMIZED V4 VALUES - Profit Ratio Optimization
        atr_mult = 1.6   # Wider stop (prevents stop hunting)
        rr = 2.0         # V4-Robust: 2.0 RR
        sl_pct = 1.00
        tp_pct = 2.00

    # =========================================================================
    # ROW 2: SL/TP Settings (V4-Robust Defaults)
    # =========================================================================
    col4, col5 = st.columns(2)
    with col4:
        if sl_mode == "ATR based":
            atr_mult = st.number_input(
                # V4-Robust: 2.2x ATR (avoids wick stop-outs)
                "ATR SL Multiplier", 0.5, 5.0, 2.2, 0.1, key="sq_scan_atr",
                help="2.2x ATR avoids 'wick stop-outs' in volatile F&O stocks"
            )
            rr = st.number_input(
                # V4-Robust: 2.0 RR (compensates for wider SL)
                "Reward : Risk (TP = RR×Risk)", 1.0, 5.0, 2.0, 0.5, key="sq_scan_rr",
                help="2.0 RR compensates for wider SL, improves win rate"
            )
        else:
            sl_pct = (
                st.number_input(
                    "SL %", 0.2, 10.0, 1.0, 0.2, key="sq_scan_slpct"
                )
                / 100.0
            )
    with col5:
        if sl_mode == "Fixed %":
            tp_pct = (
                st.number_input(
                    "TP %", 0.5, 20.0, 2.0, 0.5, key="sq_scan_tppct"
                )
                / 100.0
            )

    # =========================================================================
    # ROW 3: V4-Robust - Session, Quality & Trend Settings
    # =========================================================================
    st.markdown("#### ⚙️ Advanced Settings (V4-Robust)")
    adv_col1, adv_col2, adv_col3, adv_col4, adv_col5 = st.columns(5)

    with adv_col1:
        max_trades_per_session = st.number_input(
            "Max Trades/Day", 1, 5, 1, key="sq_max_session",
            help="Prevents 'revenge trading' on same stock"
        )
    with adv_col2:
        use_breakeven = st.checkbox(
            "Breakeven Stop", value=True, key="sq_use_be",
            help="Move SL to Entry+0.5R after 1.5R profit"
        )
    with adv_col3:
        use_quality_filter = st.checkbox(
            "Quality Filter", value=True, key="sq_quality",
            help="Skip signals before 10:00 AM + volume checks"
        )
    with adv_col4:
        use_trend_filter = st.checkbox(
            "60m Trend Filter", value=True, key="sq_trend_filter",
            help="Dual EMA (20/50) - only trade in clear trends"
        )
    with adv_col5:
        max_trade_bars = st.number_input(
            # V4-Robust: 40 bars (prevents expired trades)
            "Max Hold Bars", 10, 100, 40, key="sq_max_bars",
            help="40 bars prevents 'Expired' trades turning into losses"
        )

    # =========================================================================
    # ROW 4: Date/Time & Start Button
    # =========================================================================
    col_btn, col_date, col_time = st.columns([1, 1, 1])
    with col_date:
        scan_date = st.date_input(
            "Scan End Date", value=date.today(), key="sq_scan_date")
    with col_time:
        start_min = 9 * 60 + 15
        end_min = 15 * 60 + 15
        time_options = [
            f"{(m//60):02d}:{(m % 60):02d}" for m in range(start_min, end_min + 1, 15)]
        scan_time_str = st.selectbox(
            "Scan End Time", options=time_options, index=0, key="sq_scan_time")

    with col_btn:
        # Add this temporarily in your tab1 code before the scan:
        test_df = load_data_fast(
            fo_stocks.iloc[0]["instrument_key"], "15minute", 60)
        if test_df is not None:
            sig_df = compute_squeeze_stack(test_df)
            st.write("Columns:", list(sig_df.columns))
            st.write("Sample signals:", sig_df[["long_signal", "short_signal"]].sum(
            ) if "long_signal" in sig_df.columns else "NO long_signal column!")
        if st.button("🚀 Start 15m Squeeze Scan V2", type="primary", use_container_width=True):
            end_dt = datetime.combine(
                scan_date, datetime.strptime(scan_time_str, "%H:%M").time()
            )
            progress = st.progress(0, text="Initializing scan...")
            start_time = time.time()

# # DEBUG: Test single stock
#         if st.button("🧪 DEBUG: Test Single Stock"):
#             test_row = fo_stocks.iloc[0]
#             test_ikey = test_row["instrument_key"]
#             test_symbol = test_row["trading_symbol"]

#             st.write(f"Testing: {test_symbol}")

#             # Load data
#             test_df = load_data_fast(test_ikey, "15minute", 60)
#             st.write(f"Data loaded: {len(test_df)} rows" if test_df is not None else "NO DATA")

#             if test_df is not None and len(test_df) > 100:
#                 # Get signals
#                 sig_df = compute_squeeze_stack(test_df)
#                 st.write(f"Signal columns: {list(sig_df.columns)}")

#                 long_count = sig_df["long_signal"].sum() if "long_signal" in sig_df.columns else 0
#                 short_count = sig_df["short_signal"].sum() if "short_signal" in sig_df.columns else 0
#                 st.write(f"Signals found: {long_count} LONG, {short_count} SHORT")

#                 # Try the V2 backtest

#                 result = build_15m_signals_with_backtest(
#                     test_df,
#                     sl_mode="ATR",
#                     sl_atr_mult=1.5,
#                     tp_rr=2.5,
#                     max_trade_bars=30,
#                     max_trades_per_session=1,
#                     use_breakeven=True,
#                     use_quality_filter=False,
#                     min_score=0.0,
#                     compute_squeeze_fn=compute_squeeze_stack,
#                 )

#                 st.write(f"V2 Result - Active: {len(result.active_signals)}, Completed: {len(result.completed_trades)}")
#                 st.write(f"Skipped - Session: {result.signals_skipped_session_limit}, Quality: {result.signals_skipped_low_quality}")
            # =====================================================
            # CALL THE NEW V2 FUNCTION
            # =====================================================

            active_df, completed_df, summary = run_batch_scan_squeeze_15m_v2_improved(
                fo_stocks,
                lookback_days=scan_lookback,
                sl_mode=sl_mode,
                atr_mult=atr_mult,
                rr=rr,
                sl_pct=sl_pct,
                tp_pct=tp_pct,
                progress_bar=progress,
                end_dt=end_dt,
                db=db,
                max_trade_bars=max_trade_bars,
                # V4 OPTIMIZED PARAMETERS
                max_trades_per_session=max_trades_per_session,
                use_breakeven=use_breakeven,  # V4: Use UI checkbox value
                breakeven_at_r=1.5,           # V4: Trigger at 1.5R (not 1.0R)
                use_quality_filter=use_quality_filter,
                min_score=5.0,
                use_trend_filter=use_trend_filter,  # V4: 60m trend filter
                # Pass your data loader
                load_data_fn=load_data_fast,
                compute_squeeze_fn=compute_squeeze_stack,
            )
            # Add this RIGHT AFTER the run_batch_scan call:
            st.write("DEBUG - Summary:", summary)
            st.write("DEBUG - Active DF shape:",
                     active_df.shape if not active_df.empty else "EMPTY")
            st.write("DEBUG - Completed DF shape:",
                     completed_df.shape if not completed_df.empty else "EMPTY")
            elapsed = time.time() - start_time
            progress.progress(1.0, text=f"Scan complete in {elapsed:.1f}s")

            # Store results in session state
            st.session_state["sq_active_signals"] = active_df
            st.session_state["sq_completed_trades"] = completed_df
            st.session_state["sq_scan_summary"] = summary
            st.session_state["sq_scan_run_time"] = datetime.now()

    # =========================================================================
    # DISPLAY RESULTS
    # =========================================================================
    if "sq_scan_summary" in st.session_state:
        summary = st.session_state["sq_scan_summary"]
        active_df = st.session_state["sq_active_signals"]
        completed_df = st.session_state["sq_completed_trades"]
        scan_time = st.session_state.get("sq_scan_run_time", datetime.now())

        st.markdown(
            f"**Last Scan:** {scan_time.strftime('%Y-%m-%d %H:%M:%S')}")
        st.divider()

        # =====================================================
        # SCAN SUMMARY
        # =====================================================
        st.markdown("### 📊 Scan Summary")
        m1, m2, m3, m4, m5 = st.columns([1, 1, 1, 1, 1], gap="large")

        m1.metric(label="Stocks Scanned",
                  value=f"{summary['total_stocks_scanned']}")
        m2.metric(label="With Signals",
                  value=f"{summary['stocks_with_signals']}")
        m3.metric(label="🟡 Active", value=f"{summary['total_active']}")
        m4.metric(label="✅ Completed", value=f"{summary['total_completed']:,}")
        m5.metric(label="Win Rate", value=f"{summary['win_rate']:.1f}%")

        # NEW: Show skip statistics
        if summary.get('signals_skipped_session_limit', 0) > 0:
            st.info(f"📊 Signals filtered: {summary.get('signals_skipped_session_limit', 0)} by session limit, "
                    f"{summary.get('signals_skipped_quality', 0)} by quality filter")

        st.divider()

        # =====================================================
        # P&L SUMMARY
        # =====================================================
        st.markdown("### 💰 P&L Summary (Completed Trades)")
        p1, p2, p3, p4, p5 = st.columns([1.5, 1, 1, 1, 1], gap="large")

        total_pnl = summary["total_pnl"]
        pnl_delta = f"{summary['total_pnl_pct']:.2f}%" if summary['total_pnl_pct'] != 0 else None

        p1.metric(label="Total P&L",
                  value=f"₹{total_pnl:,.2f}", delta=pnl_delta)
        p2.metric(label="🟢 Wins", value=f"{summary['total_wins']:,}")
        p3.metric(label="🔴 Losses", value=f"{summary['total_losses']:,}")

        # NEW: Show breakeven count
        breakevens = summary.get('total_breakevens', 0)
        p4.metric(label="⚪ Breakeven", value=f"{breakevens:,}")

        avg_pnl = summary['total_pnl_pct'] / max(summary['total_completed'], 1)
        p5.metric(label="Avg P&L %", value=f"{avg_pnl:.2f}%")

        st.divider()

        # Rest of your display code remains the same...
        # (Active signals table, completed trades table, charts, etc.)

        # ========================================
        # SCAN SUMMARY - Full width metrics
        # ========================================
        st.markdown("### 📊 Scan Summary")

        # Use wider columns with gaps
        m1, m2, m3, m4, m5 = st.columns([1, 1, 1, 1, 1], gap="large")

        m1.metric(
            label="Stocks Scanned",
            value=f"{summary['total_stocks_scanned']}"
        )
        m2.metric(
            label="With Signals",
            value=f"{summary['stocks_with_signals']}"
        )
        m3.metric(
            label="🟡 Active",
            value=f"{summary['total_active']}"
        )
        m4.metric(
            label="✅ Completed",
            value=f"{summary['total_completed']:,}"
        )
        m5.metric(
            label="Win Rate",
            value=f"{summary['win_rate']:.1f}%"
        )

        st.divider()

        # ========================================
        # P&L SUMMARY - Larger display
        # ========================================
        st.markdown("### 💰 P&L Summary (Completed Trades)")

        p1, p2, p3, p4 = st.columns([1.5, 1, 1, 1], gap="large")

        # Format P&L with proper sign
        total_pnl = summary["total_pnl"]
        pnl_delta = f"{summary['total_pnl_pct']:.2f}%" if summary['total_pnl_pct'] != 0 else None

        p1.metric(
            label="Total P&L",
            value=f"₹{total_pnl:,.2f}",
            delta=pnl_delta
        )
        p2.metric(
            label="🟢 Wins",
            value=f"{summary['total_wins']:,}"
        )
        p3.metric(
            label="🔴 Losses",
            value=f"{summary['total_losses']:,}"
        )

        avg_pnl = summary['total_pnl_pct'] / max(summary['total_completed'], 1)
        p4.metric(
            label="Avg P&L %",
            value=f"{avg_pnl:.2f}%"
        )

        st.divider()

        # ========================================
        # ACTIVE SIGNALS TABLE
        # ========================================
        st.markdown("### 🟡 Active Signals (Not Yet Hit SL/TP)")
        st.caption(
            f"Showing {len(active_df)} active signals that can still be traded")

        if not active_df.empty:
            active_df = active_df[active_df["Score"] >= 5]
            st.dataframe(
                active_df,
                use_container_width=True,
                height=min(400, len(active_df) * 38 + 50),
                hide_index=True,
                column_config={
                    "Symbol": st.column_config.TextColumn("Symbol", width="medium"),
                    "Signal": st.column_config.TextColumn("Signal", width="small"),
                    "Entry Time": st.column_config.TextColumn("Entry Time", width="medium"),
                    "Entry": st.column_config.NumberColumn("Entry", format="₹%.2f"),
                    "Current": st.column_config.NumberColumn("Current", format="₹%.2f"),
                    "SL": st.column_config.NumberColumn("SL", format="₹%.2f"),
                    "TP": st.column_config.NumberColumn("TP", format="₹%.2f"),
                    "Score": st.column_config.NumberColumn("Score", format="%.1f"),
                    "Instrument Key": st.column_config.TextColumn("Instrument Key", width="large"),
                }
            )
        else:
            st.info("No active signals found with Alignment Score 5. All signals have hit SL or TP, or did not meet the score criteria.")

        st.divider()

        # ========================================
        # COMPLETED TRADES TABLE
        # ========================================
        st.markdown("### ✅ Completed Trades (SL or TP Hit)")
        st.caption(f"Showing {len(completed_df)} completed trades with P&L")

        if not completed_df.empty:
            # Add color coding for Result column
            def color_result(val):
                if val == "Take Profit":
                    return "background-color: #d4edda; color: #155724"
                elif val == "Stop Loss":
                    return "background-color: #f8d7da; color: #721c24"
                return ""

            def color_pnl(val):
                try:
                    if float(val) > 0:
                        return "color: green; font-weight: bold"
                    elif float(val) < 0:
                        return "color: red; font-weight: bold"
                except:
                    pass
                return ""

            # Style the dataframe
            styled_df = completed_df.style.applymap(
                color_result, subset=["Result"]
            ).applymap(
                color_pnl, subset=["P&L", "P&L %"]
            )

            st.dataframe(
                styled_df,
                use_container_width=True,
                height=min(600, len(completed_df) * 38 + 50),
                hide_index=True,
                column_config={
                    "Symbol": st.column_config.TextColumn("Symbol", width="medium"),
                    "Signal": st.column_config.TextColumn("Side", width="small"),
                    "Entry Time": st.column_config.TextColumn("Entry Time", width="medium"),
                    "Exit Time": st.column_config.TextColumn("Exit Time", width="medium"),
                    "Entry": st.column_config.NumberColumn("Entry", format="₹%.2f"),
                    "Exit": st.column_config.NumberColumn("Exit", format="₹%.2f"),
                    "SL": st.column_config.NumberColumn("SL", format="₹%.2f"),
                    "TP": st.column_config.NumberColumn("TP", format="₹%.2f"),
                    "Result": st.column_config.TextColumn("Result", width="medium"),
                    "P&L": st.column_config.NumberColumn("P&L (₹)", format="₹%.2f"),
                    "P&L %": st.column_config.NumberColumn("P&L %", format="%.2f%%"),
                    "Bars": st.column_config.NumberColumn("Bars", format="%d"),
                    # FIX: added comma here from previous review
                    "Score": st.column_config.NumberColumn("Score", format="%.1f"),
                    "Instrument Key": None,  # Hide this column
                }
            )

            # Export buttons
            col_exp1, col_exp2, col_exp3 = st.columns([1, 1, 2])
            with col_exp1:
                csv_completed = completed_df.to_csv(index=False)
                st.download_button(
                    "📥 Export Completed Trades",
                    data=csv_completed,
                    file_name=f"squeeze_completed_{date.today()}.csv",
                    mime="text/csv",
                )
            with col_exp2:
                if not active_df.empty:
                    csv_active = active_df.to_csv(index=False)
                    st.download_button(
                        "📥 Export Active Signals",
                        data=csv_active,
                        file_name=f"squeeze_active_{date.today()}.csv",
                        mime="text/csv",
                    )

            st.divider()

            # ========================================
            # CHARTS
            # ========================================
            st.markdown("### 📈 Trade Analysis")

            chart_col1, chart_col2 = st.columns(2)

            with chart_col1:
                # Win/Loss Pie Chart
                import plotly.express as px

                win_loss_data = pd.DataFrame({
                    "Result": ["Wins (TP Hit)", "Losses (SL Hit)"],
                    "Count": [summary["total_wins"], summary["total_losses"]]
                })

                fig_pie = px.pie(
                    win_loss_data,
                    values="Count",
                    names="Result",
                    color="Result",
                    color_discrete_map={
                        "Wins (TP Hit)": "#28a745",
                        "Losses (SL Hit)": "#dc3545"
                    },
                    title="Win/Loss Distribution",
                    hole=0.4  # Donut chart
                )
                fig_pie.update_layout(
                    showlegend=True,
                    legend=dict(orientation="h", yanchor="bottom", y=-0.2),
                    margin=dict(t=50, b=50, l=20, r=20)
                )
                st.plotly_chart(fig_pie, use_container_width=True)

            with chart_col2:
                # P&L Distribution Histogram
                fig_hist = px.histogram(
                    completed_df,
                    x="P&L %",
                    nbins=30,
                    title="P&L % Distribution",
                    color_discrete_sequence=["steelblue"]
                )
                fig_hist.add_vline(x=0, line_dash="dash",
                                   line_color="red", line_width=2)
                fig_hist.update_layout(
                    xaxis_title="P&L %",
                    yaxis_title="Number of Trades",
                    margin=dict(t=50, b=50, l=20, r=20)
                )
                st.plotly_chart(fig_hist, use_container_width=True)

            # Daily P&L breakdown (if data spans multiple days)
            if "Entry Time" in completed_df.columns:
                try:
                    completed_df["Trade Date"] = pd.to_datetime(
                        completed_df["Entry Time"]).dt.date
                    daily_pnl = completed_df.groupby("Trade Date").agg({
                        "P&L": "sum",
                        "Symbol": "count"
                    }).reset_index()
                    daily_pnl.columns = ["Date", "P&L", "Trades"]

                    if len(daily_pnl) > 1:
                        st.markdown("### 📅 Daily P&L Breakdown")

                        fig_daily = px.bar(
                            daily_pnl,
                            x="Date",
                            y="P&L",
                            title="Daily P&L",
                            color="P&L",
                            color_continuous_scale=[
                                "red", "lightgray", "green"],
                            color_continuous_midpoint=0
                        )
                        fig_daily.update_layout(
                            xaxis_title="Date",
                            yaxis_title="P&L (₹)",
                            showlegend=False
                        )
                        st.plotly_chart(fig_daily, use_container_width=True)

                        # Daily summary table
                        st.dataframe(
                            daily_pnl,
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "Date": st.column_config.DateColumn("Date"),
                                "P&L": st.column_config.NumberColumn("P&L", format="₹%.2f"),
                                "Trades": st.column_config.NumberColumn("Trades", format="%d")
                            }
                        )
                except Exception:
                    pass

        else:
            st.info(
                "No completed trades found with Alignment Score 5 in the lookback period.")

# ============================
# TAB 2: LIVE 15m SQUEEZE SCANNER (FIXED)
# ============================
with tab2:
    st.markdown("### 🔴 Live Intraday Scanner (15m Squeeze)")
    st.info("""
    **Workflow:**
    1. Click **'🔄 Refresh Live Data'** to fetch today's candles (fills gap from 9:15)
    2. Click **'📊 Rebuild & Scan'** to resample data and generate signals
    3. **Score = 5** signals are TRADABLE NOW
    4. **Score = 4** signals need one more 15m confirmation
    """)

    # ========================================
    # INITIALIZE LIVE MANAGER (Singleton)
    # ========================================
    if "live_manager" not in st.session_state or st.session_state["live_manager"] is None:
        try:
            st.session_state["live_manager"] = LiveTradingManager()
        except Exception as e:
            st.session_state["live_manager"] = None
            st.error(f"Failed to initialize LiveTradingManager: {e}")

    live_manager = st.session_state.get("live_manager")

    if not live_manager:
        st.error("Live manager not available. Cannot run live scan.")
        st.stop()

    # Try to start WebSocket
    access_token = get_access_token()
    if access_token:
        live_manager.start_websocket_if_needed(access_token)

    # ========================================
    # MARKET STATUS DISPLAY
    # ========================================
    market_open = is_market_hours()

    status_col1, status_col2, status_col3, status_col4 = st.columns(4)
    with status_col1:
        if market_open:
            st.success("🟢 Market OPEN")
        else:
            st.warning("🔴 Market CLOSED")
    with status_col2:
        if market_open:
            next_15m = get_next_candle_time("15minute")
            st.info(f"⏱️ Next 15m: **{next_15m.strftime('%H:%M')}**")
        else:
            st.info("⏱️ Next 15m: **Market Closed**")
    with status_col3:
        if market_open:
            secs_remaining = seconds_until_next_candle("15minute")
            st.info(f"⏳ In **{secs_remaining // 60}m {secs_remaining % 60}s**")
        else:
            st.info("⏳ In **Market Closed**")
    with status_col4:
        ws_builder = getattr(live_manager, "ws_builder", None)
        ws_connected = getattr(live_manager, "ws_connected", False)

        if ws_connected and ws_builder:
            ws_time = ws_builder.ws_started_at if hasattr(
                ws_builder, 'ws_started_at') else None
            st.success(
                f"🔌 WS: {ws_time.strftime('%H:%M') if ws_time else 'Connected'}")
        elif ws_builder and not ws_connected:
            st.warning("🔌 WS: Initialized but not connected")
        else:
            st.error("🔌 WS: Not started - live data will not work!")

    st.divider()

    # ========================================
    # LIVE DATA STATUS
    # ========================================
    st.markdown("#### 📊 Live Data Coverage")

    try:
        summary = live_manager.get_live_data_summary()

        cov_col1, cov_col2, cov_col3, cov_col4, cov_col5 = st.columns(5)
        cov_col1.metric("Instruments", summary.get("instruments_with_data", 0))

        total_candles = summary.get("total_candles_today", 0)
        instruments_count = summary.get("instruments_with_data", 0)
        avg_candles = total_candles / max(instruments_count, 1)

        # Warning if average candles per instrument is too low
        if avg_candles < 100:
            cov_col2.metric("Candles Today", total_candles,
                            delta=f"⚠️ Low: {avg_candles:.0f}/inst", delta_color="off")
        else:
            cov_col2.metric("Candles Today", total_candles)

        first_candle = summary.get("first_candle")
        if first_candle:
            first_time = pd.to_datetime(first_candle).strftime("%H:%M")
            # Check if gap exists
            if first_time != "09:15":
                cov_col3.metric("First Candle", first_time,
                                delta="⚠️ Gap from 9:15", delta_color="off")
            else:
                cov_col3.metric("First Candle", first_time, delta="✓ Complete")
        else:
            cov_col3.metric("First Candle", "N/A", delta="No data")

        last_candle = summary.get("last_candle")
        if last_candle:
            cov_col4.metric("Last Candle", pd.to_datetime(
                last_candle).strftime("%H:%M"))
        else:
            cov_col4.metric("Last Candle", "N/A")

        latest_fetch = summary.get("latest_fetch")
        if latest_fetch:
            cov_col5.metric("Last Fetch", pd.to_datetime(
                latest_fetch).strftime("%H:%M:%S"))
        else:
            cov_col5.metric("Last Fetch", "Never")

    except Exception as e:
        st.warning(f"Could not get live data summary: {e}")

    st.divider()

    # ========================================
    # CONTROL BUTTONS
    # ========================================
    st.markdown("#### 🎮 Live Data Controls")

    btn_col1, btn_col2, btn_col3, btn_col4, btn_col5 = st.columns(5)

    with btn_col1:
        refresh_clicked = st.button(
            "🔄 Refresh Live Data",
            type="primary",
            use_container_width=True,
            help="Fetch 1m candles from 9:15 to now (fills any gaps)"
        )

    with btn_col2:
        rebuild_clicked = st.button(
            "📊 Rebuild & Scan",
            type="primary",
            use_container_width=True,
            help="Resample to 5m/15m/60m and scan for signals"
        )

    with btn_col3:
        check_gaps_clicked = st.button(
            "🔎 Check Gaps",
            type="secondary",
            use_container_width=True,
            help="Find missing 1m candles from 9:15 to now"
        )

    with btn_col4:
        debug_clicked = st.button(
            "🔍 Debug Data",
            type="secondary",
            use_container_width=True,
            help="Show detailed data merging diagnostics"
        )

    with btn_col5:
        init_clicked = st.button(
            "🗑️ Initialize Day",
            type="secondary",
            use_container_width=True,
            help="Clear cache (only use before market open)"
        )

    # Auto-refresh checkbox in a separate row
    auto_refresh = st.checkbox(
        "Auto-refresh (60s)", value=False, key="sq_live_auto")

    # ========================================
    # REFRESH BUTTON HANDLER
    # ========================================
    if refresh_clicked:
        if not access_token:
            st.error("No access token! Please login first.")
        else:
            # Check WebSocket status before refresh
            ws_connected = getattr(live_manager, "ws_connected", False)
            if not ws_connected:
                st.warning("⚠️ WebSocket not connected - starting it now...")
                live_manager.start_websocket_if_needed(access_token)

            with st.spinner("Fetching live data (fills gap from 9:15)..."):
                progress = st.progress(0, text="Starting...")

                def update_progress(current, total, symbol):
                    progress.progress(
                        current / total, text=f"Fetching {symbol}... ({current}/{total})")

                status = live_manager.fill_gap_and_refresh(
                    access_token, update_progress)
                progress.progress(1.0, text="Complete!")

                if status.success:
                    if status.gap_filled:
                        st.success(
                            f"✅ Gap filled from {status.gap_from.strftime('%H:%M')} to {status.gap_to.strftime('%H:%M')}")
                    if status.candles_inserted > 0:
                        st.success(
                            f"✅ Inserted {status.candles_inserted:,} candles from {status.instruments_updated} instruments")

                        # Verify data was actually inserted (use safe_query)
                        result = db.safe_query(
                            "SELECT COUNT(*) FROM live_ohlcv_cache", fetch='one')
                        cache_count = result[0] if result else 0
                        st.info(f"📊 Total candles in cache: {cache_count:,}")
                    else:
                        st.info("Data already up to date.")

                        # Show what's in cache (use safe_query)
                        result = db.safe_query(
                            "SELECT COUNT(*) FROM live_ohlcv_cache", fetch='one')
                        cache_count = result[0] if result else 0
                        if cache_count == 0:
                            st.error(
                                "❌ No data in live_ohlcv_cache - WebSocket may not be running!")
                        else:
                            st.info(f"📊 Cache has {cache_count:,} candles")
                else:
                    st.warning(
                        f"Partial success. Errors: {len(status.errors)}")
                    if status.errors:
                        with st.expander("View errors"):
                            st.code("\n".join(status.errors[:20]))

            st.session_state["sq_live_refreshed"] = datetime.now()

    # ========================================
    # CHECK GAPS BUTTON HANDLER & DISPLAY
    # ========================================
    # Show gap analysis if: 1) Just ran check, OR 2) Results exist in session state
    show_gap_analysis = check_gaps_clicked or "gap_analysis_results" in st.session_state

    if check_gaps_clicked:
        st.markdown("---")
        st.markdown("### 🔎 Checking 1-Minute Candle Gaps")

        # Get current time
        now = datetime.now()
        current_time = now.time()

        # Market hours: 9:15 AM to 3:30 PM
        market_start = dt_time(9, 15)
        market_end = dt_time(15, 30)

        # If before market start, show message
        if current_time < market_start:
            st.info("Market hasn't opened yet. Check back after 9:15 AM.")
        else:
            # Determine end time (current time or market close, whichever is earlier)
            if current_time > market_end:
                end_time = market_end
            else:
                end_time = current_time

            # Create expected timestamps from 9:15 to current time
            today = date.today()
            start_dt = datetime.combine(today, market_start)
            end_dt = datetime.combine(today, end_time)

            # Generate all expected 1-minute timestamps
            expected_timestamps = pd.date_range(
                start=start_dt, end=end_dt, freq='1min')
            total_expected = len(expected_timestamps)

            st.info(
                f"Expected candles from 9:15 to {end_time.strftime('%H:%M')}: **{total_expected}**")

            with st.spinner("Analyzing live_ohlcv_cache for gaps..."):
                try:
                    # Get all unique instruments
                    instruments = live_manager.get_active_instruments()

                    if not instruments:
                        st.warning("No active instruments found.")
                    else:
                        st.write(f"Checking {len(instruments)} instruments...")

                        # Prepare gap analysis
                        gap_results = []
                        progress_bar = st.progress(0, text="Analyzing...")

                        for idx, (instrument_key, symbol) in enumerate(instruments):
                            progress_bar.progress(
                                (idx + 1) / len(instruments),
                                text=f"Checking {symbol}... ({idx+1}/{len(instruments)})"
                            )

                            # Query candles for this instrument
                            query = """
                                SELECT timestamp
                                FROM live_ohlcv_cache
                                WHERE instrument_key = ?
                                AND DATE(timestamp) = CURRENT_DATE
                                AND timestamp BETWEEN ? AND ?
                                ORDER BY timestamp
                            """
                            result = db.con.execute(
                                query, [instrument_key, start_dt, end_dt]).fetchall()

                            if not result:
                                # No data at all for this instrument
                                gap_results.append({
                                    "Symbol": symbol,
                                    "Instrument Key": instrument_key,
                                    "Total Candles": 0,
                                    "Expected": total_expected,
                                    "Missing": total_expected,
                                    "Coverage %": 0.0,
                                    "First Candle": None,
                                    "Last Candle": None,
                                    "Gap Details": f"NO DATA (missing all {total_expected} candles)"
                                })
                            else:
                                # Get timestamps
                                actual_timestamps = pd.DatetimeIndex(
                                    [r[0] for r in result])
                                actual_count = len(actual_timestamps)
                                missing_count = total_expected - actual_count
                                coverage_pct = (
                                    actual_count / total_expected) * 100

                                # Find gaps (missing timestamps)
                                missing_timestamps = expected_timestamps.difference(
                                    actual_timestamps)

                                # Format gap details
                                if missing_count == 0:
                                    gap_details = "✅ COMPLETE (no gaps)"
                                else:
                                    # Group consecutive missing timestamps into ranges
                                    if len(missing_timestamps) > 0:
                                        gaps = []
                                        gap_start = missing_timestamps[0]
                                        gap_end = missing_timestamps[0]

                                        for i in range(1, len(missing_timestamps)):
                                            if (missing_timestamps[i] - gap_end).seconds == 60:
                                                # Consecutive gap
                                                gap_end = missing_timestamps[i]
                                            else:
                                                # New gap
                                                if gap_start == gap_end:
                                                    gaps.append(
                                                        gap_start.strftime('%H:%M'))
                                                else:
                                                    gaps.append(
                                                        f"{gap_start.strftime('%H:%M')}-{gap_end.strftime('%H:%M')}")
                                                gap_start = missing_timestamps[i]
                                                gap_end = missing_timestamps[i]

                                        # Add last gap
                                        if gap_start == gap_end:
                                            gaps.append(
                                                gap_start.strftime('%H:%M'))
                                        else:
                                            gaps.append(
                                                f"{gap_start.strftime('%H:%M')}-{gap_end.strftime('%H:%M')}")

                                        # Limit display to first 10 gaps
                                        if len(gaps) > 10:
                                            gap_details = ", ".join(
                                                gaps[:10]) + f" ... (+{len(gaps)-10} more)"
                                        else:
                                            gap_details = ", ".join(gaps)
                                    else:
                                        gap_details = "Unknown gaps"

                                gap_results.append({
                                    "Symbol": symbol,
                                    "Instrument Key": instrument_key,
                                    "Total Candles": actual_count,
                                    "Expected": total_expected,
                                    "Missing": missing_count,
                                    "Coverage %": round(coverage_pct, 2),
                                    "First Candle": actual_timestamps[0].strftime('%H:%M') if len(actual_timestamps) > 0 else None,
                                    "Last Candle": actual_timestamps[-1].strftime('%H:%M') if len(actual_timestamps) > 0 else None,
                                    "Gap Details": gap_details
                                })

                        progress_bar.progress(1.0, text="Analysis complete!")

                        # Create DataFrame
                        gaps_df = pd.DataFrame(gap_results)

                        # Summary metrics
                        st.markdown("#### 📊 Gap Analysis Summary")
                        sum_col1, sum_col2, sum_col3, sum_col4 = st.columns(4)

                        complete_count = len(gaps_df[gaps_df["Missing"] == 0])
                        partial_count = len(gaps_df[(gaps_df["Missing"] > 0) & (
                            gaps_df["Missing"] < total_expected)])
                        no_data_count = len(
                            gaps_df[gaps_df["Missing"] == total_expected])
                        avg_coverage = gaps_df["Coverage %"].mean()

                        sum_col1.metric(
                            "✅ Complete", complete_count, delta=f"{(complete_count/len(gaps_df)*100):.1f}%")
                        sum_col2.metric(
                            "⚠️ Partial", partial_count, delta=f"{(partial_count/len(gaps_df)*100):.1f}%")
                        sum_col3.metric(
                            "❌ No Data", no_data_count, delta=f"{(no_data_count/len(gaps_df)*100):.1f}%")
                        sum_col4.metric("Avg Coverage", f"{avg_coverage:.1f}%")

                        st.divider()

                        # Display results
                        st.markdown("#### 📋 Detailed Gap Report")

                        # Filter options
                        filter_col1, filter_col2 = st.columns(2)
                        with filter_col1:
                            show_filter = st.selectbox(
                                "Show:",
                                ["All", "With Gaps Only",
                                    "Complete Only", "No Data Only"],
                                key="gap_filter"
                            )

                        # Apply filter
                        if show_filter == "With Gaps Only":
                            display_df = gaps_df[(gaps_df["Missing"] > 0) & (
                                gaps_df["Missing"] < total_expected)]
                        elif show_filter == "Complete Only":
                            display_df = gaps_df[gaps_df["Missing"] == 0]
                        elif show_filter == "No Data Only":
                            display_df = gaps_df[gaps_df["Missing"]
                                                 == total_expected]
                        else:
                            display_df = gaps_df

                        # Sort by missing count (descending)
                        display_df = display_df.sort_values(
                            "Missing", ascending=False)

                        # Display table
                        st.dataframe(
                            display_df,
                            use_container_width=True,
                            height=min(600, len(display_df) * 38 + 50),
                            hide_index=True,
                            column_config={
                                "Symbol": st.column_config.TextColumn("Symbol", width="small"),
                                "Total Candles": st.column_config.NumberColumn("Got", format="%d"),
                                "Expected": st.column_config.NumberColumn("Expected", format="%d"),
                                "Missing": st.column_config.NumberColumn("Missing", format="%d"),
                                "Coverage %": st.column_config.ProgressColumn("Coverage", min_value=0, max_value=100, format="%.1f%%"),
                                "First Candle": st.column_config.TextColumn("First", width="small"),
                                "Last Candle": st.column_config.TextColumn("Last", width="small"),
                                "Gap Details": st.column_config.TextColumn("Gap Periods", width="large"),
                                "Instrument Key": None  # Hide
                            }
                        )

                        # Store gap analysis results in session state
                        st.session_state["gap_analysis_results"] = gaps_df
                        st.session_state["gap_analysis_time"] = now
                        st.session_state["gap_start_dt"] = start_dt
                        st.session_state["gap_end_dt"] = end_dt
                        st.session_state["gap_total_expected"] = total_expected

                        # Export and Fill Gaps buttons
                        btn_exp_col1, btn_exp_col2 = st.columns(2)

                        with btn_exp_col1:
                            csv_data = gaps_df.to_csv(index=False)
                            st.download_button(
                                "📥 Export Gap Report",
                                data=csv_data,
                                file_name=f"gap_report_{date.today()}_{now.strftime('%H%M')}.csv",
                                mime="text/csv",
                                use_container_width=True
                            )

                        with btn_exp_col2:
                            # Only show fill button if there are gaps
                            instruments_with_gaps = gaps_df[
                                (gaps_df["Missing"] > 0) & (
                                    gaps_df["Missing"] < total_expected)
                            ]

                            if len(instruments_with_gaps) > 0:
                                if st.button(
                                    f"🔧 Fill Gaps ({len(instruments_with_gaps)} instruments)",
                                    type="primary",
                                    use_container_width=True,
                                    help="Fetch missing candles from Upstox API for instruments with gaps",
                                    key="fill_gaps_btn"
                                ):
                                    st.session_state["trigger_fill_gaps"] = True
                                    st.rerun()
                            else:
                                st.success("✅ No gaps to fill!")

                except Exception as e:
                    st.error(f"Error analyzing gaps: {e}")
                    import traceback
                    st.code(traceback.format_exc())

    # ========================================
    # DISPLAY GAP RESULTS FROM SESSION STATE (if not just checked)
    # ========================================
    elif "gap_analysis_results" in st.session_state:
        st.markdown("---")
        st.markdown("### 🔎 Gap Analysis Results (Last Check)")

        gaps_df = st.session_state["gap_analysis_results"]
        check_time = st.session_state.get("gap_analysis_time", datetime.now())
        start_dt = st.session_state["gap_start_dt"]
        end_dt = st.session_state["gap_end_dt"]
        total_expected = st.session_state["gap_total_expected"]

        st.caption(f"Last checked: {check_time.strftime('%Y-%m-%d %H:%M:%S')}")
        st.info(
            f"Expected candles from 9:15 to {end_dt.strftime('%H:%M')}: **{total_expected}**")

        # Summary metrics
        st.markdown("#### 📊 Gap Analysis Summary")
        sum_col1, sum_col2, sum_col3, sum_col4 = st.columns(4)

        complete_count = len(gaps_df[gaps_df["Missing"] == 0])
        partial_count = len(gaps_df[(gaps_df["Missing"] > 0) & (
            gaps_df["Missing"] < total_expected)])
        no_data_count = len(gaps_df[gaps_df["Missing"] == total_expected])
        avg_coverage = gaps_df["Coverage %"].mean()

        sum_col1.metric("✅ Complete", complete_count,
                        delta=f"{(complete_count/len(gaps_df)*100):.1f}%")
        sum_col2.metric("⚠️ Partial", partial_count,
                        delta=f"{(partial_count/len(gaps_df)*100):.1f}%")
        sum_col3.metric("❌ No Data", no_data_count,
                        delta=f"{(no_data_count/len(gaps_df)*100):.1f}%")
        sum_col4.metric("Avg Coverage", f"{avg_coverage:.1f}%")

        st.divider()

        # Display table
        st.markdown("#### 📋 Detailed Gap Report")

        filter_col1, filter_col2 = st.columns(2)
        with filter_col1:
            show_filter = st.selectbox(
                "Show:",
                ["All", "With Gaps Only", "Complete Only", "No Data Only"],
                key="gap_filter_display"
            )

        # Apply filter
        if show_filter == "With Gaps Only":
            display_df = gaps_df[(gaps_df["Missing"] > 0) & (
                gaps_df["Missing"] < total_expected)]
        elif show_filter == "Complete Only":
            display_df = gaps_df[gaps_df["Missing"] == 0]
        elif show_filter == "No Data Only":
            display_df = gaps_df[gaps_df["Missing"] == total_expected]
        else:
            display_df = gaps_df

        display_df = display_df.sort_values("Missing", ascending=False)

        st.dataframe(
            display_df,
            use_container_width=True,
            height=min(600, len(display_df) * 38 + 50),
            hide_index=True,
            column_config={
                "Symbol": st.column_config.TextColumn("Symbol", width="small"),
                "Total Candles": st.column_config.NumberColumn("Got", format="%d"),
                "Expected": st.column_config.NumberColumn("Expected", format="%d"),
                "Missing": st.column_config.NumberColumn("Missing", format="%d"),
                "Coverage %": st.column_config.ProgressColumn("Coverage", min_value=0, max_value=100, format="%.1f%%"),
                "First Candle": st.column_config.TextColumn("First", width="small"),
                "Last Candle": st.column_config.TextColumn("Last", width="small"),
                "Gap Details": st.column_config.TextColumn("Gap Periods", width="large"),
                "Instrument Key": None
            }
        )

        # Buttons
        btn_exp_col1, btn_exp_col2 = st.columns(2)

        with btn_exp_col1:
            csv_data = gaps_df.to_csv(index=False)
            st.download_button(
                "📥 Export Gap Report",
                data=csv_data,
                file_name=f"gap_report_{date.today()}_{check_time.strftime('%H%M')}.csv",
                mime="text/csv",
                use_container_width=True
            )

        with btn_exp_col2:
            instruments_with_gaps = gaps_df[
                (gaps_df["Missing"] > 0) & (
                    gaps_df["Missing"] < total_expected)
            ]

            if len(instruments_with_gaps) > 0:
                if st.button(
                    f"🔧 Fill Gaps ({len(instruments_with_gaps)} instruments)",
                    type="primary",
                    use_container_width=True,
                    help="Fetch missing candles from Upstox API for instruments with gaps",
                    key="fill_gaps_btn_display"
                ):
                    st.session_state["trigger_fill_gaps"] = True
                    st.rerun()
            else:
                st.success("✅ No gaps to fill!")

    # ========================================
    # FILL GAPS HANDLER (triggered by session state)
    # ========================================
    if st.session_state.get("trigger_fill_gaps", False):
        # Clear trigger immediately
        st.session_state["trigger_fill_gaps"] = False

        st.markdown("---")
        st.markdown("### 🔧 Filling Detected Gaps")
        st.info("🔧 Fill gaps handler is now executing...")

        # Retrieve gap data from session state
        if "gap_analysis_results" in st.session_state:
            gaps_df = st.session_state["gap_analysis_results"]
            start_dt = st.session_state["gap_start_dt"]
            end_dt = st.session_state["gap_end_dt"]
            total_expected = st.session_state["gap_total_expected"]

            st.write(f"📊 Retrieved gap data: {len(gaps_df)} instruments")
            st.write(
                f"📅 Time range: {start_dt.strftime('%H:%M')} to {end_dt.strftime('%H:%M')}")

            instruments_with_gaps = gaps_df[
                (gaps_df["Missing"] > 0) & (
                    gaps_df["Missing"] < total_expected)
            ]

            st.write(
                f"🔍 Instruments with gaps to fill: {len(instruments_with_gaps)}")

            if not access_token:
                st.error("❌ No access token! Please login first.")
            else:
                st.success(
                    f"✅ Access token available, starting fill process...")
                with st.spinner("Fetching missing candles..."):
                    fill_progress = st.progress(0, text="Starting gap fill...")

                    total_instruments = len(instruments_with_gaps)
                    total_filled = 0
                    errors_list = []
                    debug_logs = []

                    for counter, (idx, row) in enumerate(instruments_with_gaps.iterrows(), start=1):
                        instrument_key = row["Instrument Key"]
                        symbol = row["Symbol"]
                        missing_count = row["Missing"]

                        fill_progress.progress(
                            # Ensure it never exceeds 1.0
                            min(counter / total_instruments, 1.0),
                            text=f"Filling gaps for {symbol}... ({counter}/{total_instruments})"
                        )

                        debug_logs.append(
                            f"[{symbol}] Attempting to fetch {missing_count} missing candles")

                        try:
                            # Use the live manager's fetch function
                            df_fetched = live_manager._fetch_intraday_1m_range(
                                instrument_key,
                                start_dt,
                                end_dt,
                                access_token
                            )

                            debug_logs.append(
                                f"[{symbol}] Fetched {len(df_fetched)} candles from API")

                            if not df_fetched.empty:
                                candles_inserted = 0
                                # Insert into live_ohlcv_cache
                                for _, candle_row in df_fetched.iterrows():
                                    try:
                                        db.con.execute("""
                                            INSERT OR REPLACE INTO live_ohlcv_cache
                                            (instrument_key, timestamp, open, high, low, close, volume)
                                            VALUES (?, ?, ?, ?, ?, ?, ?)
                                        """, [
                                            instrument_key,
                                            candle_row['timestamp'],
                                            float(candle_row['open']),
                                            float(candle_row['high']),
                                            float(candle_row['low']),
                                            float(candle_row['close']),
                                            int(candle_row['volume'])
                                        ])
                                        candles_inserted += 1
                                    except Exception as insert_err:
                                        errors_list.append(
                                            f"{symbol} ({candle_row['timestamp']}): {str(insert_err)}")

                                total_filled += candles_inserted
                                debug_logs.append(
                                    f"[{symbol}] Inserted {candles_inserted} candles into DB")
                            else:
                                debug_logs.append(
                                    f"[{symbol}] ⚠️ API returned empty dataframe")

                        except Exception as e:
                            errors_list.append(f"{symbol}: {str(e)}")
                            debug_logs.append(f"[{symbol}] ❌ Error: {str(e)}")
                            continue

                    fill_progress.progress(1.0, text="Gap fill complete!")

                    # Show results
                    st.success(
                        f"✅ Filled {total_filled:,} candles across {total_instruments} instruments")

                    # Show debug logs
                    with st.expander(f"📋 Debug Log ({len(debug_logs)} events)", expanded=True):
                        st.code("\n".join(debug_logs))

                    if errors_list:
                        with st.expander(f"⚠️ Errors ({len(errors_list)})", expanded=False):
                            st.code("\n".join(errors_list[:50]))

                    # Update status
                    try:
                        for idx, row in instruments_with_gaps.iterrows():
                            instrument_key = row["Instrument Key"]
                            db.con.execute("""
                                INSERT OR REPLACE INTO live_data_status
                                (instrument_key, last_fetch, last_candle_time, candle_count, status)
                                VALUES (?, CURRENT_TIMESTAMP, ?, ?, 'GAP_FILLED')
                            """, [instrument_key, end_dt, total_filled])
                    except Exception:
                        pass

                    st.info(
                        "💡 Click '🔎 Check Gaps' again to verify the gaps were filled")
        else:
            st.error("No gap data found. Please run 'Check Gaps' first.")

    # ========================================
    # REBUILD & SCAN BUTTON HANDLER
    # ========================================
    if rebuild_clicked:
        with st.spinner("Rebuilding resampled data..."):
            live_manager.rebuild_today_resampled()

            # Check if resampling actually worked
            # Don't filter by CURRENT_DATE - this allows testing with yesterday's data
            # Use safe_query for robust database access
            result = db.safe_query("""
                SELECT COUNT(*) FROM ohlcv_resampled_live
            """, fetch='one')
            resampled_count = result[0] if result else 0

            if resampled_count > 0:
                st.success(
                    f"✅ Resampled 1m → 5m/15m/60m ({resampled_count:,} candles)")
            else:
                st.error(
                    "❌ Resampling failed - no data in ohlcv_resampled_live. Click 'Refresh Live Data' first!")
                st.stop()

        st.session_state["sq_resampled_built"] = datetime.now()

        # Now scan for signals
        st.markdown("---")
        st.markdown("### 🔍 Scanning for Signals...")

        # Debug container for signal counts
        debug_container = st.empty()
        signal_debug_info = []

        # Capture stdout for debug messages
        import io
        import sys
        debug_buffer = io.StringIO()

        progress = st.progress(0, text="Starting signal scan...")
        instruments = live_manager.get_active_instruments()

        tradable_results = []  # Score = 5
        ready_results = []     # Score = 4
        skipped_no_data = 0
        skipped_few_bars = 0
        errors = 0

        total = len(instruments) if instruments else 0

        # Get SL/TP parameters from session state or use defaults
        live_sl_mode = st.session_state.get("sq_live_slmode", "ATR based")
        live_atr_mult = st.session_state.get("sq_live_atr", 2.0)
        live_rr = st.session_state.get("sq_live_rr", 2.0)
        live_sl_pct = st.session_state.get("sq_live_slpct", 1.0) / 100.0
        live_tp_pct = st.session_state.get("sq_live_tppct", 2.0) / 100.0

        for i, (instrument_key, symbol) in enumerate(instruments or []):
            progress.progress((i + 1) / max(total, 1),
                              text=f"Scanning {symbol}... ({i+1}/{total})")

            try:
                # Get combined MTF data (historical + today's live)
                df_60m, df_15m, df_5m = live_manager.get_live_mtf_data(
                    instrument_key, lookback_days=60)

                if df_15m is None:
                    skipped_no_data += 1
                    continue

                if len(df_15m) < 100:
                    skipped_few_bars += 1
                    continue

                mode_str = "ATR" if live_sl_mode == "ATR based" else "PCT"

                # Debug ALL instruments to see all completed signals
                # Changed from (i < 3) to show all instruments
                enable_debug = True
                if enable_debug:
                    msg = f"\n=== DEBUG: {symbol} ===\n  MTF data loaded: 60m={len(df_60m)}, 15m={len(df_15m)}, 5m={len(df_5m)}"
                    print(msg)
                    debug_buffer.write(msg + "\n")

                # Redirect stdout to capture debug prints from strategy
                import sys
                old_stdout = sys.stdout
                sys.stdout = debug_buffer

                try:
                    # Get BOTH score=5 and score=4 signals
                    # V4: Pass 60m data for trend filtering
                    score_5_signals, score_4_signals = build_15m_signals_for_live_scan(
                        df_15m,
                        sl_mode=mode_str,
                        sl_atr_mult=live_atr_mult,
                        tp_rr=live_rr,
                        sl_pct=live_sl_pct,
                        tp_pct=live_tp_pct,
                        lookback_bars=10,
                        include_score_4=True,
                        debug=enable_debug,
                        df_60m=df_60m,  # V4: Pass 60m data for trend filtering
                        use_trend_filter=True,  # V4: Enable trend filter
                    )
                except Exception as ex:
                    if enable_debug:
                        debug_buffer.write(
                            f"  ERROR in signal generation: {str(ex)}\n")
                        import traceback
                        debug_buffer.write(
                            f"  Traceback: {traceback.format_exc()}\n")
                    score_5_signals, score_4_signals = [], []
                finally:
                    sys.stdout = old_stdout

                # Debug: Log signal counts BEFORE age filtering
                if len(score_5_signals) > 0 or len(score_4_signals) > 0:
                    debug_msg = f"[{symbol}] Found {len(score_5_signals)} score=5, {len(score_4_signals)} score=4 signals (before age filter)"
                    print(debug_msg)
                    signal_debug_info.append(debug_msg)

                # Process score=5 signals (TRADABLE NOW)
                score_5_recent = 0
                for sig in score_5_signals:
                    signal_age_bars = (
                        df_15m.index[-1] - sig.timestamp).total_seconds() / 900
                    if signal_age_bars <= 3:  # Recent signal (within 45 min)
                        score_5_recent += 1
                        tradable_results.append({
                            "Symbol": symbol,
                            "Signal": sig.signal_type,
                            "Price": round(df_15m["Close"].iloc[-1], 2),
                            "Entry": round(sig.entry_price, 2),
                            "SL": round(sig.sl_price, 2),
                            "TP": round(sig.tp_price, 2),
                            "Time": sig.timestamp.strftime("%H:%M"),
                            "Score": 5,
                            "Status": "🟢 TRADABLE",
                            "Bars Ago": int(signal_age_bars),
                            "Instrument Key": instrument_key,
                            "Reasons": ", ".join(sig.reasons),
                        })

                # Process score=4 signals (READY SOON)
                score_4_recent = 0
                for sig in score_4_signals:
                    signal_age_bars = (
                        df_15m.index[-1] - sig.timestamp).total_seconds() / 900
                    if signal_age_bars <= 3:
                        score_4_recent += 1
                        ready_results.append({
                            "Symbol": symbol,
                            "Signal": sig.signal_type,
                            "Price": round(df_15m["Close"].iloc[-1], 2),
                            "Entry": round(sig.entry_price, 2),
                            "SL": round(sig.sl_price, 2),
                            "TP": round(sig.tp_price, 2),
                            "Time": sig.timestamp.strftime("%H:%M"),
                            "Score": 4,
                            "Status": "🟡 READY SOON",
                            "Bars Ago": int(signal_age_bars),
                            "Instrument Key": instrument_key,
                            "Reasons": ", ".join(sig.reasons),
                        })

                # Debug: Log signals AFTER age filtering
                if len(score_5_signals) > 0 or len(score_4_signals) > 0:
                    debug_msg = f"[{symbol}] After age filter: {score_5_recent}/{len(score_5_signals)} score=5, {score_4_recent}/{len(score_4_signals)} score=4"
                    print(debug_msg)
                    signal_debug_info.append(debug_msg)

            except Exception as e:
                errors += 1
                continue

        progress.progress(1.0, text="Scan complete!")

        # Show detailed debug output for ALL instruments
        debug_output = debug_buffer.getvalue()
        if debug_output.strip():
            with st.expander("🔬 Detailed Debug (All Instruments - Shows Completed Signals)", expanded=True):
                st.code(debug_output)

        # COMMENTED OUT: Show debug info if signals were found
        # if signal_debug_info:
        #     with st.expander(f"🔍 Signal Discovery Debug ({len(signal_debug_info)} events)", expanded=True):
        #         st.code("\n".join(signal_debug_info))
        # else:
        #     st.info("ℹ️ No signals found in any instrument (before or after age filtering)")

        # Show scan statistics
        st.markdown("##### 📈 Scan Statistics")
        stat_cols = st.columns(5)
        stat_cols[0].metric("Total Scanned", total)
        stat_cols[1].metric("No Data", skipped_no_data)
        stat_cols[2].metric("Few Bars", skipped_few_bars)
        stat_cols[3].metric("🟢 Tradable (Score 5)", len(tradable_results))
        stat_cols[4].metric("🟡 Ready Soon (Score 4)", len(ready_results))

        # Store results
        st.session_state["sq_live_tradable"] = pd.DataFrame(tradable_results)
        st.session_state["sq_live_ready"] = pd.DataFrame(ready_results)
        st.session_state["sq_live_scan_time"] = datetime.now()

        # ===== WRITE SIGNALS TO UNIFIED STORAGE =====
        # This makes signals available to Page 13 (Options Analyzer)
        from core.signal_manager import write_squeeze_signal

        signals_written = 0
        all_signals = tradable_results

        for sig in all_signals:
            try:
                success = write_squeeze_signal(
                    symbol=sig["Symbol"],
                    instrument_key=sig["Instrument Key"],
                    signal_type=sig["Signal"],
                    entry=sig["Entry"],
                    sl=sig["SL"],
                    tp=sig["TP"],
                    score=sig["Score"],
                    reasons=sig["Reasons"],
                    timestamp=datetime.now().replace(
                        hour=int(sig["Time"].split(":")[0]),
                        minute=int(sig["Time"].split(":")[1]),
                        second=0,
                        microsecond=0
                    )
                )
                if success:
                    signals_written += 1
            except Exception as e:
                logger.warning(
                    f"Error writing signal for {sig['Symbol']}: {e}")

        if signals_written > 0:
            st.success(
                f"✅ {signals_written} signals written to unified storage → Available in Page 13 (Options Analyzer)")

    # ========================================
    # INITIALIZE DAY HANDLER
    # ========================================
    # ========================================
    # DEBUG BUTTON HANDLER
    # ========================================
    if debug_clicked:
        st.markdown("---")
        st.markdown("### 🔍 Data Merging Diagnostics")

        # Cache stats
        st.markdown("#### 1️⃣ Live Cache (1m data)")
        cache_stats = db.con.execute("""
            SELECT
                COUNT(*) as total_candles,
                COUNT(DISTINCT instrument_key) as instruments,
                MIN(timestamp) as first_candle,
                MAX(timestamp) as last_candle
            FROM live_ohlcv_cache
            WHERE DATE(timestamp) = CURRENT_DATE
        """).fetchone()

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Candles", f"{cache_stats[0]:,}")
        col2.metric("Instruments", cache_stats[1])
        col3.metric("Avg/Instrument",
                    f"{cache_stats[0] / max(cache_stats[1], 1):.1f}")
        col4.metric(
            "Expected", f"{cache_stats[1] * 140:.0f}" if cache_stats[1] > 0 else "N/A")

        st.caption(f"Range: {cache_stats[2]} → {cache_stats[3]}")

        # Resampled stats
        st.markdown("#### 2️⃣ Resampled Live (5m/15m/60m)")
        resampled_stats = db.con.execute("""
            SELECT
                timeframe,
                COUNT(*) as candles,
                COUNT(DISTINCT instrument_key) as instruments,
                MIN(timestamp) as first,
                MAX(timestamp) as last
            FROM ohlcv_resampled_live
            WHERE DATE(timestamp) = CURRENT_DATE
            GROUP BY timeframe
            ORDER BY timeframe
        """).fetchall()

        for row in resampled_stats:
            tf, candles, instruments, first, last = row
            st.write(
                f"**{tf}**: {candles:,} candles ({instruments} instruments, avg: {candles/max(instruments, 1):.1f}/inst) | {first} → {last}")

        # Sample instrument
        st.markdown("#### 3️⃣ Sample: TATACOMM (NSE_EQ|INE669E01016)")
        inst_key = "NSE_EQ|INE669E01016"
        today_str = date.today().strftime('%Y-%m-%d')

        # Cache
        cache_sample = db.con.execute("""
            SELECT COUNT(*), MIN(timestamp), MAX(timestamp)
            FROM live_ohlcv_cache
            WHERE instrument_key = ?
            AND DATE(timestamp) = CURRENT_DATE
        """, [inst_key]).fetchone()

        st.write(
            f"**Cache (1m)**: {cache_sample[0]} candles | {cache_sample[1]} → {cache_sample[2]}")

        # Resampled
        for tf in ['5minute', '15minute', '60minute']:
            resampled_sample = db.con.execute("""
                SELECT COUNT(*), MIN(timestamp), MAX(timestamp)
                FROM ohlcv_resampled_live
                WHERE instrument_key = ?
                AND timeframe = ?
                AND DATE(timestamp) = CURRENT_DATE
            """, [inst_key, tf]).fetchone()

            st.write(
                f"**Resampled ({tf})**: {resampled_sample[0]} candles | {resampled_sample[1]} → {resampled_sample[2]}")

        # Check instrument coverage distribution
        st.markdown("#### 4️⃣ Instrument Coverage Distribution")
        coverage_stats = db.con.execute("""
            SELECT
                COUNT(*) as candle_count,
                COUNT(DISTINCT instrument_key) as instruments
            FROM live_ohlcv_cache
            WHERE DATE(timestamp) = CURRENT_DATE
            GROUP BY instrument_key
            ORDER BY candle_count
        """).df()

        if not coverage_stats.empty:
            st.write(
                f"**Coverage Range**: {coverage_stats['candle_count'].min()} to {coverage_stats['candle_count'].max()} candles/instrument")
            st.write(
                f"**Median**: {coverage_stats['candle_count'].median():.0f} candles")

            # Show instruments with low coverage
            low_coverage = db.con.execute("""
                SELECT
                    instrument_key,
                    COUNT(*) as candles,
                    MIN(timestamp) as first_candle,
                    MAX(timestamp) as last_candle
                FROM live_ohlcv_cache
                WHERE DATE(timestamp) = CURRENT_DATE
                GROUP BY instrument_key
                HAVING COUNT(*) < 200
                ORDER BY candles
                LIMIT 10
            """).df()

            if not low_coverage.empty:
                st.warning(
                    f"⚠️ {len(low_coverage)} instruments with < 200 candles (should be ~370)")
                with st.expander("View low coverage instruments"):
                    st.dataframe(low_coverage)

        # Historical + Combined
        st.markdown("#### 5️⃣ Historical + Live Combined")
        cutoff = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')

        for tf in ['5minute', '15minute', '60minute']:
            # Historical
            hist_query = """
                SELECT COUNT(*), MIN(timestamp), MAX(timestamp)
                FROM ohlcv_resampled
                WHERE instrument_key = ?
                  AND timeframe = ?
                  AND timestamp >= ?
                  AND timestamp < ?
            """
            hist_sample = db.con.execute(
                hist_query, [inst_key, tf, cutoff, today_str]).fetchone()

            # Live
            live_query = """
                SELECT COUNT(*), MIN(timestamp), MAX(timestamp)
                FROM ohlcv_resampled_live
                WHERE instrument_key = ?
                  AND timeframe = ?
                  AND timestamp >= ?
            """
            live_sample = db.con.execute(
                live_query, [inst_key, tf, today_str]).fetchone()

            combined_count = hist_sample[0] + live_sample[0]
            st.write(
                f"**{tf}**: Historical={hist_sample[0]}, Live={live_sample[0]}, **Combined={combined_count}**")

            if live_sample[0] == 0:
                st.error(f"❌ No live data for {tf} - signals will not work!")

    if init_clicked:
        if market_open:
            st.warning(
                "⚠️ Market is open - only resetting flags, not clearing data")
        live_manager.initialize_day()
        st.success("✅ Day initialized")

    # ========================================
    # DISPLAY SCAN RESULTS
    # ========================================
    st.divider()
    st.markdown("### 📊 Live Scan Results")

    # SL/TP Configuration
    cfg_col1, cfg_col2, cfg_col3 = st.columns(3)
    with cfg_col1:
        live_sl_mode = st.selectbox(
            "SL/TP Mode", ["ATR based", "Fixed %"], key="sq_live_slmode")
    with cfg_col2:
        if live_sl_mode == "ATR based":
            st.number_input("ATR Mult", 0.5, 5.0, 2.0, 0.5, key="sq_live_atr")
            st.number_input("RR Ratio", 1.0, 5.0, 2.0, 0.5, key="sq_live_rr")
        else:
            st.number_input("SL %", 0.2, 10.0, 1.0, 0.2, key="sq_live_slpct")
            st.number_input("TP %", 0.5, 20.0, 2.0, 0.5, key="sq_live_tppct")
    with cfg_col3:
        if st.button("💾 Save Tradable Signals", use_container_width=True):
            tradable_df = st.session_state.get("sq_live_tradable")
            if tradable_df is not None and not tradable_df.empty:
                saved = save_signals_to_universe(tradable_df)
                st.success(f"Saved {saved} signals to universe")
            else:
                st.warning("No tradable signals to save")

    # Show tradable signals (Score = 5)
    tradable_df = st.session_state.get("sq_live_tradable")
    if tradable_df is not None and not tradable_df.empty:
        st.markdown("#### 🟢 TRADABLE NOW (Score = 5)")
        st.dataframe(
            tradable_df,
            use_container_width=True,
            height=min(400, len(tradable_df) * 38 + 50),
            hide_index=True,
            column_config={
                "Status": st.column_config.TextColumn("Status", width="small"),
                "Symbol": st.column_config.TextColumn("Symbol", width="medium"),
                "Signal": st.column_config.TextColumn("Side", width="small"),
                "Price": st.column_config.NumberColumn("Price", format="₹%.2f"),
                "Entry": st.column_config.NumberColumn("Entry", format="₹%.2f"),
                "SL": st.column_config.NumberColumn("SL", format="₹%.2f"),
                "TP": st.column_config.NumberColumn("TP", format="₹%.2f"),
                "Score": st.column_config.NumberColumn("Score", format="%d"),
                "Instrument Key": None,
            }
        )
    else:
        st.info(
            "No tradable signals (Score = 5) found. Click 'Rebuild & Scan' after refreshing data.")

    # Show ready signals (Score = 4)
    ready_df = st.session_state.get("sq_live_ready")
    if ready_df is not None and not ready_df.empty:
        with st.expander(f"🟡 READY SOON (Score = 4) - {len(ready_df)} signals"):
            st.markdown(
                "*These signals need one more confirmation. Wait for next 15m candle.*")
            st.dataframe(
                ready_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Status": st.column_config.TextColumn("Status", width="small"),
                    "Instrument Key": None,
                }
            )

    # Show last scan time
    scan_time = st.session_state.get("sq_live_scan_time")
    if scan_time:
        age = int((datetime.now() - scan_time).total_seconds())
        st.caption(f"Last scan: {scan_time.strftime('%H:%M:%S')} ({age}s ago)")

    # Auto-refresh
    if auto_refresh:
        st.info("🔄 Auto-refresh enabled. Page will reload in ~60 seconds.")
        time.sleep(1)
        st.rerun()
# ============================
# TAB 3: SINGLE STOCK 15m SQUEEZE
# ============================
with tab3:
    st.markdown("### 📈 Single Stock 15m Squeeze Analysis")

    fo_df = fo_stocks  # from top-level
    symbol_map = {
        row["trading_symbol"]: row["instrument_key"]
        for _, row in fo_df.iterrows()
    }

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        sq_symbol = st.selectbox(
            "Select Stock", list(symbol_map.keys()), key="sq_single_sym"
        )
        sq_instrument_key = symbol_map[sq_symbol]
    with col2:
        sq_lookback = st.slider(
            "Lookback Days", 30, 180, 90, key="sq_single_lb"
        )
    with col3:
        sq_sl_mode = st.selectbox(
            "SL / TP Mode",
            ["ATR based", "Fixed %"],
            key="sq_single_slmode",
        )

    sq_atr_mult = 2.0
    sq_rr = 2.0
    sq_sl_pct = 0.01
    sq_tp_pct = 0.02

    col4, col5 = st.columns(2)
    with col4:
        if sq_sl_mode == "ATR based":
            sq_atr_mult = st.number_input(
                "ATR SL Mult",
                0.5,
                5.0,
                2.0,
                0.5,
                key="sq_single_atr",
            )
            sq_rr = st.number_input(
                "RR (TP = RR×Risk)",
                1.0,
                5.0,
                2.0,
                0.5,
                key="sq_single_rr",
            )
        else:
            sq_sl_pct = (
                st.number_input(
                    "SL %", 0.2, 10.0, 1.0, 0.2, key="sq_single_slpct"
                )
                / 100.0
            )
    with col5:
        if sq_sl_mode == "Fixed %":
            sq_tp_pct = (
                st.number_input(
                    "TP %", 0.5, 20.0, 2.0, 0.5, key="sq_single_tppct"
                )
                / 100.0
            )

    if st.button("🔍 Analyze 15m Squeeze", type="primary"):
        with st.spinner(f"Analyzing {sq_symbol} on 15m..."):
            df_15m = load_data_fast(
                sq_instrument_key, "15minute", sq_lookback
            )
            if df_15m is None or len(df_15m) < 100:
                st.warning("Insufficient 15m data for this symbol / lookback.")
            else:
                mode_str = "ATR" if sq_sl_mode == "ATR based" else "PCT"
                signals = build_15m_signals(
                    df_15m,
                    sl_mode=mode_str,
                    sl_atr_mult=sq_atr_mult,
                    tp_rr=sq_rr,
                    sl_pct=sq_sl_pct,
                    tp_pct=sq_tp_pct,
                )

                # --- START MODIFICATION for single stock analysis ---
                # Filter for signals with an alignment score of 5
                signals = [s for s in signals if s.score == 5]
                # --- END MODIFICATION ---

                current_price = df_15m["Close"].iloc[-1]
                st.markdown("#### 📍 Current State")
                col1, col2 = st.columns(2)
                col1.metric("Current Price", f"{current_price:.2f}")
                col2.metric("Bars in sample", len(df_15m))

                st.markdown("#### 🎯 Tradeable Signals (15m)")
                if signals:
                    latest = signals[-1]
                    st.markdown(
                        f"**Latest Signal:** {latest.signal_type} at {latest.timestamp.strftime('%Y-%m-%d %H:%M')} (Score: {latest.score})"
                    )
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Direction", latest.signal_type)
                    c2.metric("Entry", f"{latest.entry_price:.2f}")
                    c3.metric("SL", f"{latest.sl_price:.2f}")
                    c4.metric("TP", f"{latest.tp_price:.2f}")

                    st.markdown("**Reasons**")
                    st.write(", ".join(latest.reasons))

                    rows = []
                    for s in signals:
                        rows.append(
                            {
                                "Time": s.timestamp,
                                "Signal": s.signal_type,
                                "Entry": round(s.entry_price, 2),
                                "SL": round(s.sl_price, 2),
                                "TP": round(s.tp_price, 2),
                                "Reasons": ", ".join(s.reasons),
                                "Score": round(s.score, 1),  # This will be 5.0
                            }
                        )
                    st.markdown("#### 📋 Signal History (15m)")
                    st.dataframe(
                        pd.DataFrame(rows),
                        use_container_width=True,
                        height=400,
                    )
                else:
                    st.info(
                        "No valid 15m Squeeze entries with Alignment Score 5 in the selected lookback window."
                    )

# ============================
# TAB 4: SQUEEZE UNIVERSE
# ============================
with tab4:
    st.markdown("### 💎 Squeeze Universe - Saved 15m Signals")

    def load_universe(signal_date: date = None, status: str = None) -> pd.DataFrame:
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
        except Exception:
            return pd.DataFrame()

    universe_df = load_universe()
    if not universe_df.empty:
        st.dataframe(universe_df, use_container_width=True, height=400)
    else:
        st.info("No signals saved today. Run a scan and save signals first!")

# ============================
# TAB 5: OPTIONS TRADING (SQUEEZE → OPTIONS)
# ============================
with tab5:
    st.markdown("### 🎯 Squeeze Signals → Options Trading")
    st.markdown(
        "**Convert 15m Squeeze signals into options recommendations with Greeks analysis**")

    # Check if we have active signals from Live Scanner
    tradable_signals = st.session_state.get("sq_live_tradable")
    ready_signals = st.session_state.get("sq_live_ready")

    # Combine both Score 5 and Score 4 signals
    all_signals = pd.DataFrame()
    if tradable_signals is not None and not tradable_signals.empty:
        all_signals = pd.concat(
            [all_signals, tradable_signals], ignore_index=True)
    if ready_signals is not None and not ready_signals.empty:
        all_signals = pd.concat(
            [all_signals, ready_signals], ignore_index=True)

    if all_signals.empty:
        st.info(
            "📊 No active signals from Live Scanner. Go to **Live Scanner** tab and click '📊 Rebuild & Scan' to generate signals.")

        # ===== TEST DATA SECTION =====
        st.markdown("---")
        st.markdown("#### 🧪 Testing Mode")
        st.markdown("Click below to insert test signals for pipeline testing")

        if st.button("🧪 Insert Test Signals", type="secondary"):
            # Create test signals
            test_signals = pd.DataFrame([
                {
                    'Symbol': 'RELIANCE',
                    'Signal': 'LONG',
                    'Entry': 2450.0,
                    'SL': 2430.0,
                    'TP': 2490.0,
                    'Score': 5,
                    'Time': '13:15',
                    'Instrument Key': 'NSE_EQ|INE002A01018',
                    'Reasons': 'SuperTrend bullish, WaveTrend cross, Recent squeeze'
                },
                {
                    'Symbol': 'TATASTEEL',
                    'Signal': 'SHORT',
                    'Entry': 165.0,
                    'SL': 167.0,
                    'TP': 161.0,
                    'Score': 5,
                    'Time': '13:30',
                    'Instrument Key': 'NSE_EQ|INE081A01020',
                    'Reasons': 'SuperTrend bearish, WaveTrend cross down'
                },
                {
                    'Symbol': 'HDFCBANK',
                    'Signal': 'LONG',
                    'Entry': 1650.0,
                    'SL': 1635.0,
                    'TP': 1680.0,
                    'Score': 4,
                    'Time': '13:45',
                    'Instrument Key': 'NSE_EQ|INE040A01034',
                    'Reasons': 'SuperTrend bullish, Momentum rising'
                }
            ])

            st.session_state["sq_live_tradable"] = test_signals[test_signals['Score'] == 5]
            st.session_state["sq_live_ready"] = test_signals[test_signals['Score'] == 4]

            st.success("✅ Test signals inserted! Refresh the page to see them.")
            st.rerun()

    else:
        st.success(
            f"**{len(all_signals)} active squeeze signals** ready for options analysis")

        # Clear test signals button
        if st.button("🗑️ Clear All Signals", type="secondary"):
            if "sq_live_tradable" in st.session_state:
                del st.session_state["sq_live_tradable"]
            if "sq_live_ready" in st.session_state:
                del st.session_state["sq_live_ready"]
            if "sq_options_recommendations" in st.session_state:
                del st.session_state["sq_options_recommendations"]
            st.success("✅ All signals cleared!")
            st.rerun()

        # ===== SETTINGS =====
        st.markdown("#### ⚙️ Settings")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            capital_per_trade = st.number_input(
                "Capital per Trade (₹)", 10000, 500000, 50000, step=10000, key="sq_opt_cap"
            )
        with col2:
            max_recommendations = st.slider(
                "Options per Signal", 1, 5, 3, key="sq_opt_max_recs"
            )
        with col3:
            show_score_4 = st.checkbox(
                "Include Score 4 Signals", value=True, key="sq_opt_score4"
            )
        with col4:
            auto_refresh = st.checkbox(
                "Auto-refresh (60s)", value=False, key="sq_opt_autorefresh"
            )

        st.divider()

        # Import our new modules
        from core.signal_to_options import SqueezeToOptionAdapter
        from core.option_recommender import OptionRecommender

        # Initialize
        adapter = SqueezeToOptionAdapter()
        recommender = OptionRecommender()

        # ===== ANALYZE SIGNALS BUTTON =====
        if st.button("🎯 Analyze Options for All Signals", type="primary", use_container_width=True):
            with st.spinner("Analyzing options..."):
                # Process each signal
                all_recommendations = []

                progress = st.progress(0, text="Processing signals...")

                for idx, row in all_signals.iterrows():
                    progress.progress((idx + 1) / len(all_signals),
                                      text=f"Analyzing {row['Symbol']}...")

                    try:
                        # Convert to UnderlyingSignal
                        underlying_signal = adapter.convert_from_dataframe_row(
                            row)

                        # Get recommendations
                        recommendations = recommender.recommend_for_signal(
                            signal=underlying_signal,
                            max_recommendations=max_recommendations,
                            capital_per_trade=capital_per_trade
                        )

                        # Store recommendations with signal info
                        for rec in recommendations:
                            all_recommendations.append({
                                'signal_row': row,
                                'recommendation': rec
                            })

                    except Exception as e:
                        st.warning(
                            f"Error analyzing {row.get('Symbol', 'Unknown')}: {str(e)}")
                        continue

                progress.empty()

                # Store in session state
                st.session_state["sq_options_recommendations"] = all_recommendations

                if all_recommendations:
                    st.success(
                        f"✅ Found {len(all_recommendations)} option recommendations for {len(all_signals)} signals!")
                else:
                    st.warning(
                        "❌ No option recommendations found. Check if option chains are available.")

        # ===== DISPLAY RECOMMENDATIONS =====
        if "sq_options_recommendations" in st.session_state and st.session_state["sq_options_recommendations"]:
            recommendations_list = st.session_state["sq_options_recommendations"]

            st.markdown("---")
            st.markdown("### 🎯 Option Recommendations")

            # Group by signal
            from collections import defaultdict
            grouped = defaultdict(list)
            for item in recommendations_list:
                symbol = item['signal_row']['Symbol']
                grouped[symbol].append(item)

            # Display each signal's options
            for symbol, items in grouped.items():
                signal_row = items[0]['signal_row']

                with st.expander(
                    f"🎯 {symbol} - {signal_row['Signal']} (Score: {signal_row['Score']}, Time: {signal_row['Time']})",
                    expanded=True
                ):
                    col_left, col_right = st.columns([1, 2])

                    # Left: Signal Details
                    with col_left:
                        st.markdown("##### 📊 Underlying Signal")
                        st.metric("Entry", f"₹{signal_row['Entry']:,.0f}")
                        st.metric("Stop Loss", f"₹{signal_row['SL']:,.0f}")
                        st.metric("Target", f"₹{signal_row['TP']:,.0f}")

                        risk = abs(signal_row['Entry'] - signal_row['SL'])
                        reward = abs(signal_row['TP'] - signal_row['Entry'])
                        rr = reward / risk if risk > 0 else 0
                        st.metric("Risk:Reward", f"1:{rr:.1f}")

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
                            col_g.metric(
                                "ROI", f"{rec.potential_return_pct:.0f}%")
                            col_h.metric("Moneyness", rec.moneyness)

                            st.caption(f"💡 {rec.rank_reason}")

                            # Action buttons
                            btn_col1, btn_col2, btn_col3 = st.columns(3)
                            if btn_col1.button(f"📈 Trade {rec.strike} {rec.option_type}", key=f"trade_{symbol}_{i}"):
                                st.info(
                                    f"Trade execution will be implemented in next phase")
                            if btn_col2.button(f"📊 Full Chain", key=f"chain_{symbol}_{i}"):
                                st.info("Full option chain viewer coming soon")
                            if btn_col3.button(f"⏰ Alert", key=f"alert_{symbol}_{i}"):
                                st.info("Price alert feature coming soon")

                            if i < len(items) - 1:
                                st.divider()

            # Summary section
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
            col1.metric("Signals", total_signals)
            col2.metric("Total Options", total_recommendations)
            col3.metric("Total Capital", f"₹{total_capital:,.0f}")
            col4.metric("Avg ROI", f"{avg_roi:.1f}%")

            # Export button
            if st.button("📥 Export Recommendations to CSV"):
                export_data = []
                for item in recommendations_list:
                    rec = item['recommendation']
                    sig = item['signal_row']
                    export_data.append({
                        'Symbol': sig['Symbol'],
                        'Signal_Type': sig['Signal'],
                        'Signal_Score': sig['Score'],
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
                    file_name=f"squeeze_options_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv"
                )

# ============================
# TAB 6: 15m SQUEEZE BACKTEST
# ============================
with tab6:
    st.markdown("### 📊 Single Stock Backtester (15m Squeeze)")

    bt_symbol_map = {
        row["trading_symbol"]: row["instrument_key"]
        for _, row in fo_stocks.iterrows()
    }

    col1, col2 = st.columns([1, 3])
    with col1:
        bt_symbol = st.selectbox(
            "Select Stock", list(bt_symbol_map.keys()), key="sq_bt_sym"
        )
        bt_instrument_key = bt_symbol_map[bt_symbol]
        bt_lookback = st.slider(
            "Lookback Days", 30, 365, 180, key="sq_bt_lb"
        )
        bt_sl_mode = st.selectbox(
            "SL / TP Mode", ["ATR based", "Fixed %"], key="sq_bt_slmode"
        )
        bt_capital = st.number_input(
            "Initial Capital", 10000, 10000000, 100000, key="sq_bt_cap"
        )

        bt_atr_mult = 2.0
        bt_rr = 2.0
        bt_sl_pct = 0.01
        bt_tp_pct = 0.02

        if bt_sl_mode == "ATR based":
            bt_atr_mult = st.number_input(
                "ATR SL Mult", 0.5, 5.0, 2.0, 0.5, key="sq_bt_atr"
            )
            bt_rr = st.number_input(
                "RR (TP = RR×Risk)", 1.0, 5.0, 2.0, 0.5, key="sq_bt_rr"
            )
        else:
            bt_sl_pct = (
                st.number_input(
                    "SL %", 0.2, 10.0, 1.0, 0.2, key="sq_bt_slpct"
                )
                / 100.0
            )
            bt_tp_pct = (
                st.number_input(
                    "TP %", 0.5, 20.0, 2.0, 0.5, key="sq_bt_tppct"
                )
                / 100.0
            )

        run_bt = st.button(
            "🚀 Run 15m Squeeze Backtest", type="primary", key="sq_run_bt"
        )

    with col2:
        if run_bt:
            df = load_data_fast(bt_instrument_key, "15minute", bt_lookback)
            if df is None or df.empty:
                st.warning("No data available for backtest.")
            else:
                mode_str = "ATR" if bt_sl_mode == "ATR based" else "PCT"
                signals = build_15m_signals(
                    df,
                    sl_mode=mode_str,
                    sl_atr_mult=bt_atr_mult,
                    tp_rr=bt_rr,
                    sl_pct=bt_sl_pct,
                    tp_pct=bt_tp_pct,
                )
                # --- START MODIFICATION for backtest signals ---
                # Filter for signals with an alignment score of 5
                signals = [s for s in signals if s.score == 5]
                # --- END MODIFICATION ---

                if not signals:
                    # Updated text
                    st.info(
                        "No Squeeze signals with Alignment Score 5 in backtest window.")
                else:
                    # Simple sequential backtest: 1 trade at a time, risk 1R per trade
                    equity = bt_capital
                    eq_curve = []
                    trades = []

                    for sig in signals:
                        direction = 1 if sig.signal_type == "LONG" else -1
                        entry = sig.entry_price
                        sl = sig.sl_price
                        tp = sig.tp_price

                        risk_per_share = abs(entry - sl)
                        if risk_per_share <= 0:
                            continue
                        size = equity * 0.01 / risk_per_share  # 1% risk
                        pnl = (tp - entry) * direction * size

                        equity += pnl
                        eq_curve.append(
                            {"time": sig.timestamp, "equity": equity})
                        trades.append(
                            {
                                "Time": sig.timestamp,
                                "Signal": sig.signal_type,
                                "Entry": round(entry, 2),
                                "SL": round(sl, 2),
                                "TP": round(tp, 2),
                                "PnL": round(pnl, 2),
                                "Equity": round(equity, 2),
                                # This will be 5.0
                                "Score": round(sig.score, 1),
                            }
                        )

                    if trades:
                        trades_df = pd.DataFrame(trades)
                        eq_df = pd.DataFrame(eq_curve)

                        total_return = (equity / bt_capital - 1) * 100
                        wins = trades_df[trades_df["PnL"] > 0]
                        win_rate = len(wins) / len(trades_df) * 100

                        col_a, col_b, col_c = st.columns(3)
                        col_a.metric("Trades", len(trades_df))
                        col_b.metric("Win Rate", f"{win_rate:.1f}%")
                        col_c.metric("Total Return", f"{total_return:.1f}%")

                        st.markdown("#### 📋 Trades")
                        st.dataframe(
                            trades_df, use_container_width=True, height=300)

                        st.markdown("#### 📈 Equity Curve (discrete)")
                        st.line_chart(
                            eq_df.set_index("time")["equity"],
                            use_container_width=True,
                        )
                    else:
                        # Updated text
                        st.info(
                            "No valid trades executed in backtest with Alignment Score 5.")

# ============================
# TAB 7: TRADE LOG
# ============================
with tab7:
    st.markdown("### 📋 Trade Log History (Universe)")

    try:
        history_df = db.con.execute(
            """
            SELECT signal_date, symbol, signal_type, signal_strength,
                   entry_price, stop_loss, target_price, status
            FROM ehma_universe
            ORDER BY signal_date DESC, signal_strength DESC
            LIMIT 200
            """
        ).df()
        if not history_df.empty:
            st.dataframe(history_df, use_container_width=True, height=500)
            csv = history_df.to_csv(index=False)
            st.download_button(
                "📥 Export History",
                data=csv,
                file_name="squeeze_trade_history.csv",
                mime="text/csv",
            )
        else:
            st.info("No trade history yet.")
    except Exception as e:
        st.error(f"Error loading history: {e}")

# ============================
# TAB 8: ML ANALYSIS
# ============================
with tab8:
    st.markdown("### 🤖 Machine Learning Analysis")
    st.markdown("Analyze failed trades and understand failure patterns")

    # Check if we have completed trades
    if "sq_completed_trades" in st.session_state:
        completed_df = st.session_state["sq_completed_trades"]

        if not completed_df.empty:
            st.success(f"📊 {len(completed_df)} completed trades available for analysis")

            # 1. Failure Analysis Dashboard
            st.markdown("#### 📈 Failure Analysis Dashboard")

            # Extract failure category from exit_reason
            def extract_category(result, exit_reason):
                if result == "Take Profit":
                    return "success"
                if "Wick Trap" in str(exit_reason):
                    return "wick_trap"
                elif "Trend Reversal" in str(exit_reason):
                    return "trend_reversal"
                elif "Volatility Spike" in str(exit_reason):
                    return "volatility_spike"
                elif "Momentum Exhaustion" in str(exit_reason):
                    return "momentum_exhaustion"
                elif "Opening Bell" in str(exit_reason):
                    return "opening_bell_noise"
                elif "Range Bound" in str(exit_reason):
                    return "range_bound"
                elif "Breakeven" in str(exit_reason):
                    return "breakeven"
                else:
                    return "other_sl"

            completed_df["failure_category"] = completed_df.apply(
                lambda row: extract_category(row.get("Result", ""), row.get("exit_reason", "")),
                axis=1
            )

            # Show failure distribution
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**Trade Outcome Distribution**")
                outcome_counts = completed_df["failure_category"].value_counts()
                st.dataframe(outcome_counts)

            with col2:
                # Plot failure distribution
                try:
                    import plotly.express as px
                    fig = px.pie(
                        values=outcome_counts.values,
                        names=outcome_counts.index,
                        title="Trade Outcome Categories"
                    )
                    st.plotly_chart(fig, use_container_width=True)
                except Exception as e:
                    st.warning(f"Could not create chart: {e}")

            # 2. Win Rate by Hour
            st.markdown("#### ⏰ Win Rate by Hour of Day")

            try:
                # Extract hour from Entry Time
                completed_df["entry_hour"] = completed_df["Entry Time"].apply(
                    lambda x: int(str(x).split(" ")[1].split(":")[0]) if " " in str(x) else 0
                )
                completed_df["is_win"] = completed_df["Result"].apply(
                    lambda x: 1 if x == "Take Profit" else 0
                )

                hourly_stats = completed_df.groupby("entry_hour").agg({
                    "is_win": ["sum", "count", "mean"]
                }).round(2)
                hourly_stats.columns = ["Wins", "Total", "Win Rate"]
                hourly_stats["Win Rate"] = (hourly_stats["Win Rate"] * 100).round(1).astype(str) + "%"

                st.dataframe(hourly_stats)

                # Best and worst hours
                best_hour = completed_df.groupby("entry_hour")["is_win"].mean().idxmax()
                worst_hour = completed_df.groupby("entry_hour")["is_win"].mean().idxmin()
                st.info(f"📈 Best hour: {best_hour}:00 | 📉 Worst hour: {worst_hour}:00")

            except Exception as e:
                st.warning(f"Could not analyze hourly data: {e}")

            # 3. Strategy Recommendations
            st.markdown("#### 💡 Strategy Improvement Recommendations")

            failure_counts = completed_df[completed_df["failure_category"] != "success"]["failure_category"].value_counts()

            if not failure_counts.empty:
                total_failures = failure_counts.sum()

                for category, count in failure_counts.items():
                    pct = count / total_failures * 100
                    if category == "wick_trap" and pct > 25:
                        st.warning(f"""
                        **Wick Trap Issue ({pct:.1f}% of losses)**
                        - Consider increasing ATR multiplier from 2.2x to 2.5-3.0x
                        - This gives SL more room to avoid stop hunts
                        """)
                    elif category == "trend_reversal" and pct > 20:
                        st.warning(f"""
                        **Trend Reversal Issue ({pct:.1f}% of losses)**
                        - Strengthen 60m trend filter requirements
                        - Consider adding EMA slope requirement
                        """)
                    elif category == "opening_bell_noise" and pct > 15:
                        st.warning(f"""
                        **Opening Bell Issue ({pct:.1f}% of losses)**
                        - Consider delaying entries until 10:15 AM
                        - Morning volatility is causing false breakouts
                        """)
                    elif category == "momentum_exhaustion" and pct > 20:
                        st.warning(f"""
                        **Momentum Exhaustion ({pct:.1f}% of losses)**
                        - Trades went profitable but then reversed
                        - Consider tighter trailing stops or partial profit taking
                        """)

            # 4. Export ML Dataset
            st.markdown("#### 📤 Export Dataset for Advanced Analysis")

            if st.button("📥 Export ML Dataset"):
                # Create export with all available features
                export_cols = ["Symbol", "Signal", "Entry Time", "Exit Time", "Entry", "Exit",
                              "SL", "TP", "Result", "P&L", "P&L %", "Bars", "Score", "failure_category"]
                export_df = completed_df[[c for c in export_cols if c in completed_df.columns]]

                csv = export_df.to_csv(index=False)
                st.download_button(
                    "📥 Download ML Dataset CSV",
                    data=csv,
                    file_name=f"squeeze_ml_dataset_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )
        else:
            st.info("No completed trades available. Run a batch scan first.")
    else:
        st.info("Run a batch scan to generate ML analysis data.")


# if __name__ == "__main__":
#     print("=" * 60)
#     print("Squeeze Backtest V2 - Session Limits & Breakeven Stops")
#     print("=" * 60)
#     print("\nKey improvements over V1:")
#     print("  1. Max 1 trade per stock per SESSION (day)")
#     print("  2. Breakeven stop at 1R profit")
#     print("  3. Quality filters (volume, volatility)")
#     print("  4. Optimized parameters (ATR 1.5x, RR 2.5)")
#     print("  5. Shorter max hold (30 bars vs 50)")
#     print("\nExpected improvements:")
#     print("  - Fewer trades (quality over quantity)")
#     print("  - Higher win rate (45%+ vs 33%)")
#     print("  - Better profit factor (1.5+ vs <1.0)")
#     print("  - Protected profits via breakeven stops")
#     print("\nTo use, replace your current functions with:")
#     print("  - build_15m_signals_with_backtest_v2")
#     print("  - run_batch_scan_squeeze_15m_v2_improved")
