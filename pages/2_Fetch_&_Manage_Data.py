"""
Page 2: Fetch & Manage Data (DuckDB Version)
Modern data fetching interface using DuckDB backend
"""
# --- PROFESSIONAL UI BRIDGE ---
from core.config import get_access_token
import pandas as pd
import streamlit as st
from datetime import date, datetime, timedelta
from pathlib import Path
import os
import sys
from core.database import get_db
from data.data_manager_duckdb import DataManager
from data.resampler_duckdb import DuckDBResampler


def load_css():
    with open("style.css") as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)

# Call this immediately after st.set_page_config
# ------------------------------


# Add project root to path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# Page config
st.set_page_config(page_title="Fetch & Manage Data",
                   layout="wide", page_icon="📥")
load_css()

# Initialize session state
if 'db' not in st.session_state:
    st.session_state.db = get_db()
if 'dm' not in st.session_state:
    st.session_state.dm = DataManager(st.session_state.db)
if 'resampler' not in st.session_state:
    st.session_state.resampler = DuckDBResampler(st.session_state.db)

db = st.session_state.db
dm = st.session_state.dm
resampler = st.session_state.resampler

# Title
st.title("📥 Fetch & Manage Data")
st.markdown(
    "**DuckDB-powered data management** - Fetch historical data and manage your database")

# Create tabs
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📥 Fetch Data",
    "⏱️ Resample Data",
    "📊 Database Status",
    "🔍 Data Viewer",
    "🗑️ Delete Data",
    "🔧 Maintenance"
])

# ============================================================================
# TAB 1: FETCH DATA
# ============================================================================
with tab1:
    st.header("📥 Fetch Historical Data")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("Select Symbols")

        # Get available segments
        segments = db.con.execute("""
            SELECT DISTINCT segment 
            FROM instruments 
            ORDER BY segment
        """).df()['segment'].tolist()

        if not segments:
            st.error(
                "❌ No instruments found in database. Please migrate instruments first.")
            st.stop()

        # Segment filter
        selected_segment = st.selectbox(
            "Segment",
            segments,
            index=segments.index('NSE_EQ') if 'NSE_EQ' in segments else 0,
            help="For F&O Stocks mode, this will be overridden to NSE_EQ",
            key="segment_selector"
        )

        # Get symbols for selected segment
        symbols_df = db.con.execute("""
            SELECT DISTINCT trading_symbol 
            FROM instruments 
            WHERE segment = ?
              AND trading_symbol IS NOT NULL
              AND trading_symbol != ''
            ORDER BY trading_symbol
        """, [selected_segment]).df()

        available_symbols = symbols_df['trading_symbol'].tolist()

        # Symbol selection mode
        selection_mode = st.radio(
            "Selection Mode",
            ["F&O Stocks (Auto)", "Select from list",
             "Enter manually", "Upload CSV"],
            horizontal=True,
            help="F&O Stocks: Auto-discover all stocks with futures contracts"
        )

        selected_symbols = []

        if selection_mode == "F&O Stocks (Auto)":
            # Use F&O master table instead of querying on-the-fly
            st.markdown("""
            <div style="background-color: #d1ecf1; padding: 10px; border-radius: 5px; margin: 10px 0;">
            <b>🎯 F&O Stocks from Master List</b><br>
            Uses pre-curated list of stocks with futures contracts.<br>
            Updated automatically when instruments are refreshed (Page 1).
            </div>
            """, unsafe_allow_html=True)

            # Get F&O stocks from master table
            with st.spinner("📋 Loading F&O stocks from master table..."):
                try:
                    fo_stocks_df = db.con.execute("""
                        SELECT 
                            trading_symbol,
                            instrument_key,
                            name,
                            lot_size,
                            last_updated
                        FROM fo_stocks_master
                        WHERE is_active = TRUE
                        ORDER BY trading_symbol
                    """).df()
                except:
                    # Table doesn't exist yet - fallback to query
                    st.warning(
                        "⚠️ F&O master table not found. Using fallback query...")

                    fo_query = """
                    WITH fo_names AS (
                        SELECT DISTINCT name
                        FROM instruments
                        WHERE segment = 'NSE_FO'
                          AND instrument_type = 'FUT'
                          AND name IS NOT NULL
                          AND name != ''
                    ),
                    ranked_instruments AS (
                        SELECT 
                            i.trading_symbol,
                            i.instrument_key,
                            i.name,
                            i.lot_size,
                            ROW_NUMBER() OVER (PARTITION BY i.trading_symbol ORDER BY i.instrument_key) as rn
                        FROM instruments i
                        JOIN fo_names f ON i.name = f.name
                        WHERE i.segment = 'NSE_EQ'
                          AND i.trading_symbol IS NOT NULL
                          AND i.trading_symbol != ''
                    )
                    SELECT 
                        trading_symbol,
                        instrument_key,
                        name,
                        lot_size
                    FROM ranked_instruments
                    WHERE rn = 1
                    ORDER BY trading_symbol
                    """

                    fo_stocks_df = db.con.execute(fo_query).df()

                    if not fo_stocks_df.empty:
                        st.info(
                            "💡 Tip: Refresh instruments (Page 1) to create F&O master table")

            if not fo_stocks_df.empty:
                st.success(f"✅ Found **{len(fo_stocks_df)}** F&O stocks")

                # Option to select all or subset
                col_a, col_b = st.columns([3, 1])

                with col_a:
                    load_option = st.radio(
                        "Load Options",
                        ["All F&O stocks", "Select specific stocks"],
                        horizontal=True,
                        key="fo_load_option"
                    )

                with col_b:
                    if st.button("📋 Preview List", key="preview_fo"):
                        st.session_state.show_fo_preview = True

                if load_option == "All F&O stocks":
                    selected_symbols = fo_stocks_df['trading_symbol'].tolist()

                    # Store metadata for later use
                    st.session_state.fo_metadata = fo_stocks_df

                else:  # Select specific stocks
                    # Show filterable list
                    st.markdown("**Filter F&O Stocks:**")

                    search_filter = st.text_input(
                        "Search by symbol or name",
                        placeholder="e.g., RELIANCE, TCS, HDFC",
                        key="fo_search"
                    )

                    if search_filter:
                        filtered_df = fo_stocks_df[
                            fo_stocks_df['trading_symbol'].str.contains(search_filter.upper(), case=False, na=False) |
                            fo_stocks_df['name'].str.contains(
                                search_filter.upper(), case=False, na=False)
                        ]
                    else:
                        filtered_df = fo_stocks_df

                    selected_symbols = st.multiselect(
                        "Choose F&O Stocks",
                        filtered_df['trading_symbol'].tolist(),
                        default=filtered_df['trading_symbol'].tolist()[:10],
                        key="fo_multiselect",
                        help="Select stocks to fetch data for"
                    )

                # Preview modal
                if st.session_state.get('show_fo_preview', False):
                    with st.expander("📋 F&O Stocks List", expanded=True):
                        st.dataframe(
                            fo_stocks_df[['trading_symbol',
                                          'name', 'lot_size']],
                            use_container_width=True,
                            height=400
                        )

                        csv = fo_stocks_df.to_csv(index=False)
                        st.download_button(
                            "📥 Download F&O List (CSV)",
                            csv,
                            f"fo_stocks_{datetime.now().strftime('%Y%m%d')}.csv",
                            "text/csv"
                        )

                        if st.button("✖️ Close Preview"):
                            st.session_state.show_fo_preview = False
                            st.rerun()

            else:
                st.warning(
                    "⚠️ No F&O stocks found. Make sure instruments are downloaded.")

        elif selection_mode == "Select from list":
            selected_symbols = st.multiselect(
                "Choose Symbols",
                available_symbols,
                default=available_symbols[:5] if len(
                    available_symbols) >= 5 else available_symbols
            )

        elif selection_mode == "Enter manually":
            manual_input = st.text_area(
                "Enter symbols (one per line or comma-separated)",
                placeholder="RELIANCE\nTCS\nINFY",
                height=100
            )
            if manual_input:
                # Split by newlines or commas
                selected_symbols = [s.strip().upper() for s in manual_input.replace(
                    ',', '\n').split('\n') if s.strip()]

        else:  # Upload CSV
            uploaded_file = st.file_uploader(
                "Upload CSV with symbols",
                type=['csv'],
                help="CSV should have a column named 'symbol' or 'name'"
            )
            if uploaded_file:
                csv_df = pd.read_csv(uploaded_file)
                # Try to find symbol column
                symbol_col = None
                for col in ['symbol', 'Symbol', 'name', 'Name', 'SYMBOL', 'NAME']:
                    if col in csv_df.columns:
                        symbol_col = col
                        break

                if symbol_col:
                    selected_symbols = csv_df[symbol_col].str.strip(
                    ).str.upper().tolist()
                    st.success(
                        f"✅ Loaded {len(selected_symbols)} symbols from CSV")
                else:
                    st.error("❌ CSV must have a column named 'symbol' or 'name'")

        # Override segment for F&O mode
        if selection_mode == "F&O Stocks (Auto)":
            selected_segment = "NSE_EQ"
            st.caption("ℹ️ Segment automatically set to NSE_EQ for F&O stocks")

        st.info(f"📊 Selected: **{len(selected_symbols)}** symbols")

    with col2:
        st.subheader("Date Range")

        # Quick date presets
        preset = st.selectbox(
            "Quick Presets",
            ["Custom", "Today", "Last 7 Days", "Last 30 Days",
                "Last 3 Months", "Last 6 Months", "Last 1 Year", "Year to Date"]
        )

        today = date.today()

        if preset == "Today":
            from_date = today
            to_date = today
        elif preset == "Last 7 Days":
            from_date = today - timedelta(days=7)
            to_date = today
        elif preset == "Last 30 Days":
            from_date = today - timedelta(days=30)
            to_date = today
        elif preset == "Last 3 Months":
            from_date = today - timedelta(days=90)
            to_date = today
        elif preset == "Last 6 Months":
            from_date = today - timedelta(days=180)
            to_date = today
        elif preset == "Last 1 Year":
            from_date = today - timedelta(days=365)
            to_date = today
        elif preset == "Year to Date":
            from_date = date(today.year, 1, 1)
            to_date = today
        else:  # Custom
            from_date = st.date_input(
                "From Date",
                value=today - timedelta(days=30),
                max_value=today
            )
            to_date = st.date_input(
                "To Date",
                value=today,
                max_value=today
            )

        st.info(f"📅 Range: **{(to_date - from_date).days + 1}** days")

        st.subheader("Options")

        interval = st.selectbox(
            "Interval",
            ["1minute", "5minute", "15minute", "30minute", "1hour", "1day"],
            index=0
        )

        force_refetch = st.checkbox(
            "Force Re-fetch",
            value=False,
            help="Re-download data even if it already exists"
        )

        auto_resample = st.checkbox(
            "Auto-resample after fetch",
            value=True,
            help="Automatically create 5m, 15m, 1d timeframes"
        )

    # Fetch button
    st.divider()

    col1, col2, col3 = st.columns([1, 1, 1])

    with col2:
        fetch_button = st.button(
            "🚀 Start Fetching Data",
            type="primary",
            use_container_width=True,
            disabled=len(selected_symbols) == 0
        )

    if fetch_button:
        if not selected_symbols:
            st.error("❌ Please select at least one symbol")
        else:
            st.info(f"🚀 Fetching data for {len(selected_symbols)} symbols...")

            # Progress tracking
            progress_bar = st.progress(0)
            status_text = st.empty()
            results_container = st.container()

            success_count = 0
            failed_symbols = []
            total_rows = 0

            for idx, symbol in enumerate(selected_symbols):
                status_text.text(
                    f"Processing {symbol} ({idx+1}/{len(selected_symbols)})...")

                try:
                    # Get instrument key - search by trading_symbol (not name!)
                    instrument_key = None

                    # Primary approach: Match on trading_symbol
                    instruments = db.con.execute("""
                        SELECT instrument_key, name, trading_symbol
                        FROM instruments
                        WHERE trading_symbol = ?
                          AND segment = ?
                        LIMIT 1
                    """, [symbol, selected_segment]).df()

                    if not instruments.empty:
                        instrument_key = instruments.iloc[0]['instrument_key']
                    else:
                        # Fallback: Try case-insensitive on trading_symbol
                        instruments = db.con.execute("""
                            SELECT instrument_key, name, trading_symbol
                            FROM instruments
                            WHERE UPPER(trading_symbol) = UPPER(?)
                              AND segment = ?
                            LIMIT 1
                        """, [symbol, selected_segment]).df()

                        if not instruments.empty:
                            instrument_key = instruments.iloc[0]['instrument_key']
                        else:
                            # Last resort: Try matching on name field
                            instruments = db.con.execute("""
                                SELECT instrument_key, name, trading_symbol
                                FROM instruments
                                WHERE UPPER(name) LIKE UPPER(?)
                                  AND segment = ?
                                LIMIT 1
                            """, [f"%{symbol}%", selected_segment]).df()

                            if not instruments.empty:
                                instrument_key = instruments.iloc[0]['instrument_key']

                    if not instrument_key:
                        failed_symbols.append(
                            f"{symbol} (not found in instruments)")
                        with results_container:
                            st.warning(
                                f"⚠️ {symbol}: Not found in instruments table")
                        continue

                    # Fetch data
                    result = dm.fetch_and_store(
                        instrument_key=instrument_key,
                        from_date=from_date,
                        to_date=to_date,
                        interval=interval,
                        force=force_refetch
                    )

                    if result['status'] in ['success', 'up_to_date']:
                        success_count += 1
                        total_rows += result['rows_added']

                        with results_container:
                            st.success(f"✅ {symbol}: {result['message']}")

                        # Auto-resample if enabled and we're fetching 1-minute data
                        if auto_resample and interval == "1minute" and result['rows_added'] > 0:
                            for tf in ['5minute', '15minute', '1day']:
                                resampler.resample_symbol(
                                    instrument_key, tf, incremental=True)
                    else:
                        failed_symbols.append(symbol)
                        with results_container:
                            st.warning(f"⚠️ {symbol}: {result['message']}")

                except Exception as e:
                    failed_symbols.append(f"{symbol} ({str(e)})")
                    with results_container:
                        st.error(f"❌ {symbol}: {str(e)}")

                progress_bar.progress((idx + 1) / len(selected_symbols))

            # Summary
            status_text.empty()
            progress_bar.empty()

            st.divider()
            st.subheader("📊 Fetch Summary")

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("✅ Success", success_count)
            with col2:
                st.metric("❌ Failed", len(failed_symbols))
            with col3:
                st.metric("📈 Total Rows", f"{total_rows:,}")

            if failed_symbols:
                with st.expander("⚠️ Failed Symbols"):
                    for sym in failed_symbols:
                        st.text(f"  - {sym}")

# ============================================================================
# TAB 2: RESAMPLE DATA
# ============================================================================
# ============================================================================
# TAB 2: RESAMPLE DATA (ENHANCED)
# ============================================================================
with tab2:
    st.header("⏱️ Resample Data")
    st.markdown(
        "Create derived timeframes from 1-minute data with full control over date ranges")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("Select Symbols to Resample")

        # Get symbols that have 1m data
        symbols_with_data = db.con.execute("""
            SELECT DISTINCT 
                i.trading_symbol,
                i.name, 
                i.segment, 
                COUNT(*) as candles,
                MIN(CAST(o.timestamp AS DATE)) as first_date,
                MAX(CAST(o.timestamp AS DATE)) as last_date
            FROM ohlcv_1m o
            JOIN instruments i ON o.instrument_key = i.instrument_key
            GROUP BY i.trading_symbol, i.name, i.segment
            ORDER BY i.trading_symbol
        """).df()

        if symbols_with_data.empty:
            st.warning("⚠️ No 1-minute data found. Please fetch data first.")
        else:
            st.dataframe(
                symbols_with_data,
                use_container_width=True,
                height=200
            )

            resample_mode = st.radio(
                "Resample Mode",
                ["All symbols", "Select specific symbols"],
                horizontal=True,
                key="resample_mode"
            )

            symbols_to_resample = []

            if resample_mode == "All symbols":
                symbols_to_resample = symbols_with_data['trading_symbol'].tolist(
                )
                st.info(
                    f"📊 Will resample **all {len(symbols_to_resample)}** symbols")
            else:
                symbols_to_resample = st.multiselect(
                    "Choose Symbols (by trading_symbol)",
                    symbols_with_data['trading_symbol'].tolist(),
                    default=symbols_with_data['trading_symbol'].tolist()[:5],
                    key="symbol_multiselect"
                )

    with col2:
        st.subheader("Timeframes")

        timeframes = st.multiselect(
            "Select Timeframes",
            ["5minute", "15minute", "30minute", "60minute", "1day"],
            default=["5minute", "15minute", "1day"],
            key="timeframes_multiselect"
        )

        st.subheader("Options")

        # NEW: Date Range Selection
        st.markdown("**📅 Date Range**")

        date_mode = st.radio(
            "Date Selection",
            ["All available data", "Specific date range"],
            horizontal=True,
            key="date_mode"
        )

        from_date_resample = None
        to_date_resample = None

        if date_mode == "Specific date range":
            col_a, col_b = st.columns(2)

            with col_a:
                from_date_resample = st.date_input(
                    "From Date",
                    value=date.today() - timedelta(days=30),
                    key="from_date_resample"
                )

            with col_b:
                to_date_resample = st.date_input(
                    "To Date",
                    value=date.today(),
                    key="to_date_resample"
                )

            days = (to_date_resample - from_date_resample).days
            st.caption(f"📊 Range: {days} days")

        # Skip existing data option
        skip_existing = st.checkbox(
            "Skip Existing Data",
            value=True,
            help="Only insert new resampled candles, skip periods that already exist",
            key="skip_existing"
        )

        segment_filter = st.selectbox(
            "Segment Filter",
            ["All", "NSE_EQ", "NSE_FO", "BSE_EQ"],
            index=0,
            key="segment_filter_resample"
        )

    # Resample button
    st.divider()

    # Initialize session state for resampling
    if 'resampling_in_progress' not in st.session_state:
        st.session_state.resampling_in_progress = False

    col1, col2, col3 = st.columns([1, 1, 1])

    with col2:
        if 'resampling_in_progress' not in st.session_state:
            st.session_state.resampling_in_progress = False

        if st.session_state.resampling_in_progress:
            st.warning("⏳ Resampling in progress or stuck. Resetting state.")
            st.session_state.resampling_in_progress = False

        resample_button = st.button(
            "🔄 Start Resampling",
            type="primary",
            use_container_width=True,
            disabled=len(
                timeframes) == 0 or st.session_state.resampling_in_progress,
            key="resample_button"
        )

        if resample_button and not st.session_state.resampling_in_progress:
            st.session_state.resampling_in_progress = True

            if not timeframes:
                st.error("❌ Please select at least one timeframe")
                st.session_state.resampling_in_progress = False
            elif date_mode == "Specific date range" and from_date_resample >= to_date_resample:
                st.error("❌ 'From Date' must be before 'To Date'")
                st.session_state.resampling_in_progress = False
            else:
                # Determine symbols to process
                if resample_mode == "All symbols":
                    processing_symbols = symbols_with_data['trading_symbol'].tolist(
                    )
                else:
                    processing_symbols = symbols_to_resample

                st.info(
                    f"🔄 Resampling {len(processing_symbols)} symbols to {len(timeframes)} timeframes...")

                # Progress tracking
                progress_bar = st.progress(0)
                status_text = st.empty()
                results_container = st.container()

                total_ops = len(processing_symbols) * len(timeframes)
                current_op = 0
                success_count = 0
                skip_count = 0
                error_count = 0
                total_rows = 0

                try:
                    for symbol in processing_symbols:
                        # Get instrument key
                        instrument_info = db.con.execute("""
                            SELECT instrument_key, name
                            FROM instruments
                            WHERE trading_symbol = ?
                            AND segment = 'NSE_EQ'
                            LIMIT 1
                        """, [symbol]).fetchone()

                        if not instrument_info:
                            continue

                        instrument_key = instrument_info[0]
                        name = instrument_info[1]

                        for tf in timeframes:
                            current_op += 1
                            status_text.text(
                                f"[{current_op}/{total_ops}] Resampling {symbol} → {tf}...")

                            try:
                                # Determine bucket expression based on timeframe
                                if tf == "1day":
                                    bucket_expr = "DATE_TRUNC('day', timestamp)::TIMESTAMP"
                                else:
                                    interval_map = {
                                        "5minute": "5 minutes",
                                        "15minute": "15 minutes",
                                        "30minute": "30 minutes",
                                        "60minute": "60 minutes",
                                    }
                                    interval_str = interval_map.get(
                                        tf, "15 minutes")
                                    bucket_expr = f"time_bucket(INTERVAL '{interval_str}', timestamp)"

                                # Build date filter for source data
                                date_filter = ""
                                delete_date_filter = ""
                                if date_mode == "Specific date range":
                                    date_filter = f"AND timestamp >= '{from_date_resample}' AND timestamp < '{to_date_resample}'::date + INTERVAL '1 day'"
                                    delete_date_filter = f"AND timestamp >= '{from_date_resample}' AND timestamp < '{to_date_resample}'::date + INTERVAL '1 day'"

                                # Count existing rows before operation
                                rows_before = db.con.execute(f"""
                                    SELECT COUNT(*) FROM ohlcv_resampled 
                                    WHERE instrument_key = '{instrument_key}' 
                                    AND timeframe = '{tf}'
                                    {delete_date_filter}
                                """).fetchone()[0]

                                # Step 1: Create temp table with resampled data
                                skip_filter = ""
                                if skip_existing:
                                    skip_filter = f"""
                                        AND {bucket_expr} NOT IN (
                                            SELECT timestamp
                                            FROM ohlcv_resampled
                                            WHERE instrument_key = '{instrument_key}'
                                            AND timeframe = '{tf}'
                                        )"""
                                db.con.execute(f"""
                                    CREATE OR REPLACE TEMP TABLE temp_resample AS
                                    SELECT 
                                        instrument_key,
                                        '{tf}' AS timeframe,
                                        {bucket_expr} AS timestamp,
                                        arg_min(open, ohlcv_1m.timestamp) AS open,
                                        MAX(high) AS high,
                                        MIN(low) AS low,
                                        arg_max(close, ohlcv_1m.timestamp) AS close,
                                        SUM(volume) AS volume,
                                        COALESCE(arg_max(oi, ohlcv_1m.timestamp), 0) AS oi
                                    FROM ohlcv_1m
                                    WHERE instrument_key = '{instrument_key}'
                                    {date_filter}
                                    {skip_filter}
                                    GROUP BY instrument_key, {bucket_expr}
                                """)

                                # Step 2: Count how many rows we're about to insert
                                new_rows = db.con.execute(
                                    "SELECT COUNT(*) FROM temp_resample").fetchone()[0]

                                if new_rows == 0:
                                    with results_container:
                                        st.info(
                                            f"⏭️ {symbol} → {tf}: No source data found")
                                    skip_count += 1
                                    db.con.execute(
                                        "DROP TABLE IF EXISTS temp_resample")
                                    continue

                                # Step 3: Delete existing data that will be replaced
                                db.con.execute(f"""
                                    DELETE FROM ohlcv_resampled
                                    WHERE instrument_key = '{instrument_key}'
                                    AND timeframe = '{tf}'
                                    {delete_date_filter}
                                """)

                                # Step 4: Insert from temp table
                                db.con.execute("""
                                    INSERT OR IGNORE INTO ohlcv_resampled
                                (
                                    instrument_key, timeframe, timestamp,
                                    open, high, low, close, volume, oi
                                )
                                    SELECT
                                        instrument_key, timeframe, timestamp,
                                        open, high, low, close, volume, oi
                                    FROM temp_resample
                                """)

                                # Step 5: Cleanup temp table
                                db.con.execute(
                                    "DROP TABLE IF EXISTS temp_resample")

                                # Count rows after
                                rows_after = db.con.execute(f"""
                                    SELECT COUNT(*) FROM ohlcv_resampled 
                                    WHERE instrument_key = '{instrument_key}' 
                                    AND timeframe = '{tf}'
                                """).fetchone()[0]

                                total_rows += new_rows

                                with results_container:
                                    if rows_before > 0:
                                        st.success(
                                            f"✅ {symbol} → {tf}: Replaced {rows_before} → {new_rows} candles")
                                    else:
                                        st.success(
                                            f"✅ {symbol} → {tf}: +{new_rows} candles")
                                success_count += 1

                            except Exception as e:
                                error_count += 1
                                error_msg = str(e)

                                # Cleanup temp table on error
                                try:
                                    db.con.execute(
                                        "DROP TABLE IF EXISTS temp_resample")
                                except:
                                    pass

                                # Show full error in UI for debugging
                                with results_container:
                                    st.error(f"❌ {symbol} → {tf}: {error_msg}")

                            progress_bar.progress(current_op / total_ops)

                finally:
                    # Reset the lock
                    st.session_state.resampling_in_progress = False

                # Summary
                status_text.empty()
                progress_bar.empty()

                st.divider()
                st.subheader("📊 Resample Summary")

                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("✅ Success", success_count)
                with col2:
                    st.metric("⏭️ Skipped", skip_count)
                with col3:
                    st.metric("❌ Errors", error_count)
                with col4:
                    st.metric("📈 Total Rows", f"{total_rows:,}")

                if date_mode == "Specific date range":
                    st.info(
                        f"📅 Processed date range: {from_date_resample} to {to_date_resample}")

                if error_count == 0:
                    st.balloons()


# ============================================================================
# TAB 3: DATABASE STATUS
# ============================================================================
with tab3:
    st.header("📊 Database Status")

    # Database file info
    db_path = db.db_path
    if db_path.exists():
        db_size = db_path.stat().st_size / (1024**3)  # GB
        st.metric("💾 Database Size", f"{db_size:.2f} GB")

    # Table statistics
    st.subheader("📋 Table Statistics")

    tables_info = []
    for table in ['instruments', 'ohlcv_1m', 'ohlcv_resampled', 'backtest_runs', 'trades']:
        count = db.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        tables_info.append({'Table': table, 'Rows': f"{count:,}"})

    st.dataframe(pd.DataFrame(tables_info),
                 use_container_width=True, hide_index=True)

    # Data coverage
    st.subheader("📅 Data Coverage (1-minute data)")

    coverage = db.con.execute("""
        SELECT 
            i.name as Symbol,
            i.segment as Segment,
            COUNT(DISTINCT DATE(o.timestamp)) as Days,
            MIN(DATE(o.timestamp)) as First_Date,
            MAX(DATE(o.timestamp)) as Last_Date,
            COUNT(*) as Total_Candles
        FROM ohlcv_1m o
        JOIN instruments i ON o.instrument_key = i.instrument_key
        GROUP BY i.name, i.segment
        ORDER BY Total_Candles DESC
        LIMIT 50
    """).df()

    st.dataframe(coverage, use_container_width=True,
                 hide_index=True, height=400)

    # Resampled data status
    st.subheader("⏱️ Resampled Data Status")

    resampled_status = db.con.execute("""
        SELECT 
            timeframe as Timeframe,
            COUNT(DISTINCT instrument_key) as Symbols,
            COUNT(*) as Total_Candles
        FROM ohlcv_resampled
        GROUP BY timeframe
        ORDER BY 
            CASE timeframe
                WHEN '5minute' THEN 1
                WHEN '15minute' THEN 2
                WHEN '30minute' THEN 3
                WHEN '1hour' THEN 4
                WHEN '1day' THEN 5
            END
    """).df()

    if not resampled_status.empty:
        st.dataframe(resampled_status,
                     use_container_width=True, hide_index=True)
    else:
        st.info(
            "No resampled data yet. Use the 'Resample Data' tab to create derived timeframes.")

# ============================================================================
# TAB 4: DATA VIEWER
# ============================================================================
with tab4:
    st.header("🔍 Data Viewer")
    st.markdown("Inspect OHLCV data from DuckDB (like the old Parquet viewer)")

    col1, col2 = st.columns([2, 1])

    with col1:
        # Symbol selection
        viewer_symbols = db.con.execute("""
            SELECT DISTINCT i.trading_symbol, i.name
            FROM instruments i
            JOIN ohlcv_1m o ON i.instrument_key = o.instrument_key
            ORDER BY i.trading_symbol
            LIMIT 200
        """).df()

        if viewer_symbols.empty:
            st.warning("⚠️ No data in database yet. Please fetch data first.")
        else:
            selected_symbol = st.selectbox(
                "Select Symbol",
                viewer_symbols['trading_symbol'].tolist(),
                key="viewer_symbol"
            )

            # Get instrument key
            instruments = db.con.execute("""
                SELECT instrument_key
                FROM instruments
                WHERE trading_symbol = ?
                LIMIT 1
            """, [selected_symbol]).df()

            if not instruments.empty:
                instrument_key = instruments.iloc[0]['instrument_key']

    with col2:
        # Timeframe selection
        if not viewer_symbols.empty and 'instruments' in locals() and not instruments.empty:
            available_tfs = db.con.execute("""
                SELECT DISTINCT '1minute' as tf, 1 as sort_order
                FROM ohlcv_1m
                WHERE instrument_key = ?
                UNION
                SELECT DISTINCT timeframe as tf, 
                    CASE timeframe
                        WHEN '5minute' THEN 2
                        WHEN '15minute' THEN 3
                        WHEN '30minute' THEN 4
                        WHEN '1hour' THEN 5
                        WHEN '1day' THEN 6
                    END as sort_order
                FROM ohlcv_resampled
                WHERE instrument_key = ?
                ORDER BY sort_order
            """, [instrument_key, instrument_key]).df()
        else:
            available_tfs = pd.DataFrame()

        if not available_tfs.empty:
            timeframe = st.selectbox(
                "Timeframe",
                available_tfs['tf'].tolist(),
                key="viewer_tf"
            )
        else:
            timeframe = "1minute"
            st.info("No data available for this symbol")

        # Date range
        num_rows = st.number_input(
            "Number of rows",
            min_value=10,
            max_value=10000,
            value=100,
            step=10
        )

        view_mode = st.radio(
            "View",
            ["Latest", "Specific Date"],
            horizontal=True
        )

    if not viewer_symbols.empty and 'instruments' in locals() and not instruments.empty:
        # Fetch data based on selection
        if view_mode == "Latest":
            # Get latest N rows
            if timeframe == "1minute":
                df_view = db.con.execute("""
                    SELECT timestamp, open, high, low, close, volume, oi
                    FROM ohlcv_1m
                    WHERE instrument_key = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, [instrument_key, num_rows]).df()
            else:
                df_view = db.con.execute("""
                    SELECT timestamp, open, high, low, close, volume, oi
                    FROM ohlcv_resampled
                    WHERE instrument_key = ?
                      AND timeframe = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, [instrument_key, timeframe, num_rows]).df()

            # Reverse to show oldest first
            df_view = df_view.iloc[::-1].reset_index(drop=True)

        else:
            # Specific date
            specific_date = st.date_input(
                "Select Date",
                value=date.today(),
                key="viewer_date"
            )

            if timeframe == "1minute":
                df_view = db.con.execute("""
                    SELECT timestamp, open, high, low, close, volume, oi
                    FROM ohlcv_1m
                    WHERE instrument_key = ?
                      AND DATE(timestamp) = ?
                    ORDER BY timestamp
                """, [instrument_key, specific_date]).df()
            else:
                df_view = db.con.execute("""
                    SELECT timestamp, open, high, low, close, volume, oi
                    FROM ohlcv_resampled
                    WHERE instrument_key = ?
                      AND timeframe = ?
                      AND DATE(timestamp) = ?
                    ORDER BY timestamp
                """, [instrument_key, timeframe, specific_date]).df()

        # Display data
        if df_view.empty:
            st.warning(
                f"⚠️ No data found for {selected_symbol} on {timeframe}")
        else:
            # Stats
            st.subheader("📊 Data Overview")
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Rows", len(df_view))
            with col2:
                st.metric(
                    "Date Range", f"{df_view['timestamp'].min().date()} to {df_view['timestamp'].max().date()}")
            with col3:
                st.metric("High", f"{df_view['high'].max():.2f}")
            with col4:
                st.metric("Low", f"{df_view['low'].min():.2f}")

            # Data table
            st.subheader("📋 Data Table")

            # Format for display
            df_display = df_view.copy()
            df_display.columns = ['Timestamp', 'Open',
                                  'High', 'Low', 'Close', 'Volume', 'OI']

            # Format numbers
            for col in ['Open', 'High', 'Low', 'Close']:
                df_display[col] = df_display[col].apply(lambda x: f"{x:.2f}")
            df_display['Volume'] = df_display['Volume'].apply(
                lambda x: f"{int(x):,}")
            df_display['OI'] = df_display['OI'].apply(lambda x: f"{int(x):,}")

            st.dataframe(
                df_display,
                use_container_width=True,
                height=400
            )

            # Download button
            csv = df_view.to_csv(index=False)
            st.download_button(
                label="📥 Download as CSV",
                data=csv,
                file_name=f"{selected_symbol}_{timeframe}_{date.today()}.csv",
                mime="text/csv"
            )

            # Quick stats
            with st.expander("📈 Statistics"):
                st.write(
                    df_view[['open', 'high', 'low', 'close', 'volume']].describe())

# ============================================================================
# TAB 5: DELETE DATA (NEW)
# ============================================================================
with tab5:
    st.header("🗑️ Delete Data")
    st.warning("⚠️ **Warning:** Data deletion is permanent and cannot be undone!")

    delete_type = st.selectbox(
        "What would you like to delete?",
        [
            "Select deletion type...",
            "Specific instrument (all data)",
            "Specific timeframe (all instruments)",
            "Specific period (date range)",
            "Instrument + Timeframe",
            "Instrument + Date Range",
            "Timeframe + Date Range",
            "Specific combination"
        ],
        key="delete_type"
    )

    if delete_type != "Select deletion type...":
        st.divider()

        # Deletion parameters
        col1, col2 = st.columns(2)

        with col1:
            # Instrument selection (if needed)
            if "instrument" in delete_type.lower() or "combination" in delete_type:
                st.subheader("🎯 Select Instrument")

                # Get list of instruments with data
                instruments_with_data = db.con.execute("""
                    SELECT DISTINCT i.trading_symbol, i.name
                    FROM instruments i
                    WHERE EXISTS (
                        SELECT 1 FROM ohlcv_1m o WHERE o.instrument_key = i.instrument_key
                    )
                    OR EXISTS (
                        SELECT 1 FROM ohlcv_resampled r WHERE r.instrument_key = i.instrument_key
                    )
                    ORDER BY i.trading_symbol
                """).df()

                if not instruments_with_data.empty:
                    selected_instrument = st.selectbox(
                        "Trading Symbol",
                        instruments_with_data['trading_symbol'].tolist(),
                        key="delete_instrument"
                    )
                else:
                    st.warning("No instruments with data found")
                    selected_instrument = None
            else:
                selected_instrument = None

            # Timeframe selection (if needed)
            if "timeframe" in delete_type.lower() or "combination" in delete_type:
                st.subheader("⏱️ Select Timeframe")

                data_source = st.radio(
                    "Data Source",
                    ["1-minute data", "Resampled data"],
                    key="delete_source"
                )

                if data_source == "Resampled data":
                    # Get available timeframes
                    available_tfs = db.con.execute("""
                        SELECT DISTINCT timeframe
                        FROM ohlcv_resampled
                        ORDER BY timeframe
                    """).df()

                    if not available_tfs.empty:
                        selected_timeframe = st.selectbox(
                            "Timeframe",
                            available_tfs['timeframe'].tolist(),
                            key="delete_timeframe"
                        )
                    else:
                        st.warning("No resampled data found")
                        selected_timeframe = None
                else:
                    selected_timeframe = "1minute"
            else:
                data_source = None
                selected_timeframe = None

        with col2:
            # Date range selection (if needed)
            if "period" in delete_type.lower() or "date range" in delete_type.lower() or "combination" in delete_type:
                st.subheader("📅 Select Date Range")

                col_a, col_b = st.columns(2)

                with col_a:
                    from_date_delete = st.date_input(
                        "From Date",
                        value=date.today() - timedelta(days=30),
                        key="from_date_delete"
                    )

                with col_b:
                    to_date_delete = st.date_input(
                        "To Date",
                        value=date.today(),
                        key="to_date_delete"
                    )

                days_to_delete = (to_date_delete - from_date_delete).days
                st.caption(f"📊 Will delete {days_to_delete} days of data")
            else:
                from_date_delete = None
                to_date_delete = None

        # Preview what will be deleted
        st.divider()
        st.subheader("🔍 Preview Deletion")

        try:
            # Build preview query
            preview_conditions = []
            params = []

            if selected_instrument:
                inst_key = db.con.execute("""
                    SELECT instrument_key FROM instruments 
                    WHERE trading_symbol = ?
                    LIMIT 1
                """, [selected_instrument]).fetchone()[0]
                preview_conditions.append("instrument_key = ?")
                params.append(inst_key)

            if selected_timeframe and data_source == "Resampled data":
                preview_conditions.append("timeframe = ?")
                params.append(selected_timeframe)

            if from_date_delete and to_date_delete:
                preview_conditions.append(f"CAST(timestamp AS DATE) >= ?")
                preview_conditions.append(f"CAST(timestamp AS DATE) <= ?")
                params.append(from_date_delete)
                params.append(to_date_delete)

            where_clause = " AND ".join(
                preview_conditions) if preview_conditions else "1=1"

            # Determine table
            if data_source == "Resampled data" or selected_timeframe in ["5minute", "15minute", "30minute", "60minute", "1day"]:
                table = "ohlcv_resampled"
            else:
                table = "ohlcv_1m"

            # Count rows to delete
            count_query = f"SELECT COUNT(*) FROM {table} WHERE {where_clause}"
            rows_to_delete = db.con.execute(count_query, params).fetchone()[0]

            st.metric("🗑️ Rows to Delete", f"{rows_to_delete:,}")

            if rows_to_delete > 0:
                # Show sample
                sample_query = f"SELECT * FROM {table} WHERE {where_clause} LIMIT 5"
                sample_df = db.con.execute(sample_query, params).df()

                st.caption("Sample of data to be deleted:")
                st.dataframe(sample_df, use_container_width=True)

        except Exception as e:
            st.error(f"Error previewing deletion: {e}")
            rows_to_delete = 0

        # Confirmation and execute
        if rows_to_delete > 0:
            st.divider()

            confirm_text = st.text_input(
                f"Type 'DELETE {rows_to_delete}' to confirm",
                key="confirm_delete"
            )

            col1, col2, col3 = st.columns([1, 1, 1])

            with col2:
                if st.button("🗑️ DELETE DATA", type="primary", use_container_width=True, key="execute_delete"):
                    if confirm_text == f"DELETE {rows_to_delete}":
                        try:
                            # Execute deletion
                            delete_query = f"DELETE FROM {table} WHERE {where_clause}"
                            db.con.execute(delete_query, params)

                            st.success(
                                f"✅ Successfully deleted {rows_to_delete:,} rows!")
                            st.balloons()

                            # Clear confirmation
                            st.rerun()

                        except Exception as e:
                            st.error(f"❌ Deletion failed: {e}")
                    else:
                        st.error(
                            f"⚠️ Confirmation text doesn't match. Please type exactly: DELETE {rows_to_delete}")

# ============================================================================
# TAB 6: MAINTENANCE
# ============================================================================
with tab6:
    st.header("🔧 Database Maintenance")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("⚡ Optimize Database")
        st.markdown("Run VACUUM to reclaim space and optimize performance")

        if st.button("🔧 Optimize Database", type="secondary", key="optimize_db"):
            with st.spinner("Optimizing..."):
                db.vacuum()
            st.success("✅ Database optimized!")

    with col2:
        st.subheader("🔍 Check Data Quality")
        st.markdown("Find symbols with missing or incomplete data")

        if st.button("🔍 Check Data Quality", type="secondary", key="check_quality"):
            # Find gaps in data
            quality_check = db.con.execute("""
                SELECT 
                    i.trading_symbol as Symbol,
                    i.name as Name,
                    COUNT(DISTINCT CAST(o.timestamp AS DATE)) as Days_Available,
                    MIN(CAST(o.timestamp AS DATE)) as First_Date,
                    MAX(CAST(o.timestamp AS DATE)) as Last_Date
                FROM ohlcv_1m o
                JOIN instruments i ON o.instrument_key = i.instrument_key
                WHERE i.segment = 'NSE_EQ'
                GROUP BY i.trading_symbol, i.name
                HAVING Days_Available < 200
                ORDER BY Days_Available ASC
                LIMIT 20
            """).df()

            if not quality_check.empty:
                st.warning(
                    f"⚠️ Found {len(quality_check)} symbols with <200 days coverage:")
                st.dataframe(
                    quality_check, use_container_width=True, hide_index=True)
            else:
                st.success("✅ All symbols have good data coverage!")


st.divider()
st.caption(
    "💡 Tip: Use incremental mode for daily updates - it only processes new data!")
