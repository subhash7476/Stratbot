"""
Page 8: Database Viewer (Fixed Version)
Works with actual database schema - no assumptions about column names
"""

import streamlit as st
import pandas as pd
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

# Path setup
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database import get_db

# Page config
st.set_page_config(layout="wide", page_title="Database Viewer", page_icon="🔍")

# Database connection

db = get_db()

# ========================================
# HELPER FUNCTIONS
# ========================================

def get_all_tables():
    """Get list of all tables in database"""
    query = """
    SELECT table_name 
    FROM information_schema.tables
    WHERE table_schema = 'main'
    ORDER BY table_name
    """
    result = db.con.execute(query).fetchall()
    return [row[0] for row in result]

def get_table_columns(table_name):
    """Get columns for a specific table"""
    try:
        schema_query = f"PRAGMA table_info({table_name})"
        schema_df = db.con.execute(schema_query).df()
        return schema_df['name'].tolist()
    except:
        return []

def safe_query(query, params=None, table_name="query"):
    """Execute query safely with error handling"""
    try:
        if params:
            return db.con.execute(query, params).df()
        else:
            return db.con.execute(query).df()
    except Exception as e:
        st.error(f"❌ Query error for {table_name}: {str(e)[:200]}")
        return pd.DataFrame()

# ========================================
# TITLE
# ========================================

st.title("🔍 Database Viewer")
st.markdown("**Browse all tables with smart column detection**")

# Get all tables
tables = get_all_tables()

st.info(f"📊 Found **{len(tables)}** tables in database")

# ========================================
# MAIN TABS
# ========================================

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📋 All Tables",
    "🎯 Instruments",
    "📊 OHLCV 1-min",
    "⏱️ OHLCV Resampled",
    "🚀 F&O Stocks",
    "📈 Other Tables",
    "🧪 SQL Console"
])

# ========================================
# TAB 1: ALL TABLES OVERVIEW
# ========================================

with tab1:
    st.header("📋 All Tables Overview")
    
    # Table statistics
    table_stats = []
    for table in tables:
        try:
            count = db.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            
            # Check if has instrument_key column
            columns = get_table_columns(table)
            has_instrument_key = 'instrument_key' in columns
            
            table_stats.append({
                'Table': table,
                'Rows': f"{count:,}",
                'Columns': len(columns),
                'Has Instruments': '✅' if has_instrument_key else '❌',
                'Status': '✅ Active' if count > 0 else '⚠️ Empty'
            })
        except Exception as e:
            table_stats.append({
                'Table': table,
                'Rows': 'Error',
                'Columns': 0,
                'Has Instruments': '❓',
                'Status': '❌ Error'
            })
    
    st.dataframe(
        pd.DataFrame(table_stats),
        use_container_width=True,
        hide_index=True
    )
    
    # Detailed table inspector
    st.divider()
    st.subheader("🔍 Detailed Table Inspector")
    
    selected_table = st.selectbox(
        "Select Table",
        tables,
        key="detailed_table"
    )
    
    if selected_table:
        try:
            # Get columns
            columns = get_table_columns(selected_table)
            
            # Get count
            count = db.con.execute(f"SELECT COUNT(*) FROM {selected_table}").fetchone()[0]
            
            # Stats
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Rows", f"{count:,}")
            with col2:
                st.metric("Columns", len(columns))
            with col3:
                has_inst = 'instrument_key' in columns
                st.metric("Instrument IDs", "✅ Yes" if has_inst else "❌ No")
            
            # Show columns
            st.subheader("📐 Columns")
            st.write(", ".join(columns))
            
            # Sample data
            st.subheader("📄 Sample Data (First 5 Rows)")
            
            if count > 0:
                # Build smart query with trading_symbol if possible
                if 'instrument_key' in columns and selected_table not in ['instruments', 'fo_stocks_master']:
                    query = f"""
                    SELECT 
                        i.trading_symbol,
                        t.*
                    FROM {selected_table} t
                    LEFT JOIN instruments i ON t.instrument_key = i.instrument_key
                    LIMIT 5
                    """
                else:
                    query = f"SELECT * FROM {selected_table} LIMIT 5"
                
                sample_df = safe_query(query, table_name=selected_table)
                
                if not sample_df.empty:
                    st.dataframe(sample_df, use_container_width=True)
                    
                    if 'instrument_key' in columns and selected_table not in ['instruments', 'fo_stocks_master']:
                        st.success("✅ Trading symbols shown for easy identification")
            else:
                st.info("Table is empty")
        
        except Exception as e:
            st.error(f"Error: {e}")

# ========================================
# TAB 2: INSTRUMENTS
# ========================================

with tab2:
    st.header("🎯 Instruments Table")
    
    # Get actual columns from instruments table
    inst_columns = get_table_columns('instruments')
    
    if not inst_columns:
        st.error("❌ Could not read instruments table structure")
    else:
        st.success(f"✅ Found {len(inst_columns)} columns")
        
        # Search and filter
        col1, col2 = st.columns(2)
        
        with col1:
            search_term = st.text_input("🔎 Search", placeholder="Symbol, name, or key")
        
        with col2:
            # Only show filters if columns exist
            if 'segment' in inst_columns:
                try:
                    segments = safe_query("SELECT DISTINCT segment FROM instruments ORDER BY segment")
                    segment_filter = st.selectbox(
                        "Segment",
                        ["All"] + segments['segment'].tolist() if not segments.empty else ["All"]
                    )
                except:
                    segment_filter = "All"
            else:
                segment_filter = "All"
        
        # Build query based on available columns
        where_clauses = []
        params = []
        
        if search_term:
            search_conditions = []
            if 'trading_symbol' in inst_columns:
                search_conditions.append("trading_symbol LIKE ?")
                params.append(f"%{search_term}%")
            if 'name' in inst_columns:
                search_conditions.append("name LIKE ?")
                params.append(f"%{search_term}%")
            if 'instrument_key' in inst_columns:
                search_conditions.append("instrument_key LIKE ?")
                params.append(f"%{search_term}%")
            
            if search_conditions:
                where_clauses.append(f"({' OR '.join(search_conditions)})")
        
        if segment_filter != "All" and 'segment' in inst_columns:
            where_clauses.append("segment = ?")
            params.append(segment_filter)
        
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        # Select only columns that exist
        select_cols = []
        for col in ['trading_symbol', 'name', 'segment', 'exchange', 'instrument_type', 'instrument_key']:
            if col in inst_columns:
                select_cols.append(col)
        
        if not select_cols:
            select_cols = ['*']
        
        query = f"""
        SELECT {', '.join(select_cols)}
        FROM instruments
        WHERE {where_sql}
        ORDER BY trading_symbol
        LIMIT 200
        """
        
        instruments_df = safe_query(query, params, "instruments")
        
        if not instruments_df.empty:
            st.metric("Results", len(instruments_df))
            st.dataframe(instruments_df, use_container_width=True, height=500)
        else:
            st.info("No results found")

# ========================================
# TAB 3: OHLCV 1-MINUTE
# ========================================

with tab3:
    st.header("📊 OHLCV 1-Minute Data")
    
    # Check if table exists
    ohlcv_1m_cols = get_table_columns('ohlcv_1m')
    
    if not ohlcv_1m_cols:
        st.info("⚠️ No ohlcv_1m table found. Fetch data first (Page 2).")
    else:
        col1, col2, col3 = st.columns(3)
        
        with col1:
            # Get list of symbols with data
            symbols_query = """
            SELECT DISTINCT i.trading_symbol
            FROM ohlcv_1m o
            JOIN instruments i ON o.instrument_key = i.instrument_key
            ORDER BY i.trading_symbol
            LIMIT 500
            """
            symbols_df = safe_query(symbols_query)
            
            if not symbols_df.empty:
                symbols_with_data = symbols_df['trading_symbol'].tolist()
                symbol_filter = st.selectbox(
                    "Trading Symbol",
                    ["All"] + symbols_with_data,
                    key="ohlcv_1m_symbol"
                )
            else:
                symbol_filter = "All"
                st.info("No data in ohlcv_1m table yet")
        
        with col2:
            date_filter = st.date_input(
                "Date",
                value=None,
                key="ohlcv_1m_date"
            )
        
        with col3:
            limit = st.slider("Rows", 10, 500, 100, key="ohlcv_1m_limit")
        
        # Load button
        if st.button("📊 Load Data", type="primary", key="load_ohlcv_1m"):
            where_clauses = []
            params = []
            
            if symbol_filter != "All":
                where_clauses.append("i.trading_symbol = ?")
                params.append(symbol_filter)
            
            if date_filter:
                where_clauses.append("CAST(o.timestamp AS DATE) = ?")
                params.append(date_filter)
            
            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
            
            query = f"""
            SELECT 
                i.trading_symbol,
                i.name as instrument_name,
                o.*
            FROM ohlcv_1m o
            JOIN instruments i ON o.instrument_key = i.instrument_key
            WHERE {where_sql}
            ORDER BY o.timestamp DESC
            LIMIT {limit}
            """
            
            data = safe_query(query, params, "ohlcv_1m")
            
            if not data.empty:
                st.success(f"✅ Loaded {len(data)} rows")
                st.dataframe(data, use_container_width=True, height=500)
                
                # Download
                csv = data.to_csv(index=False)
                st.download_button(
                    "📥 Download CSV",
                    csv,
                    f"ohlcv_1m_{datetime.now().strftime('%Y%m%d')}.csv",
                    "text/csv"
                )
            else:
                st.warning("No data found")

# ========================================
# TAB 4: OHLCV RESAMPLED
# ========================================

with tab4:
    st.header("⏱️ OHLCV Resampled Data")
    
    ohlcv_r_cols = get_table_columns('ohlcv_resampled')
    
    if not ohlcv_r_cols:
        st.info("⚠️ No ohlcv_resampled table found. Resample data first (Page 2).")
    else:
        col1, col2, col3 = st.columns(3)
        
        with col1:
            # Get symbols
            symbols_query = """
            SELECT DISTINCT i.trading_symbol
            FROM ohlcv_resampled r
            JOIN instruments i ON r.instrument_key = i.instrument_key
            ORDER BY i.trading_symbol
            LIMIT 500
            """
            symbols_df = safe_query(symbols_query)
            
            if not symbols_df.empty:
                symbol_filter_r = st.selectbox(
                    "Trading Symbol",
                    ["All"] + symbols_df['trading_symbol'].tolist(),
                    key="ohlcv_r_symbol"
                )
            else:
                symbol_filter_r = "All"
        
        with col2:
            # Get timeframes
            tf_query = "SELECT DISTINCT timeframe FROM ohlcv_resampled ORDER BY timeframe"
            tf_df = safe_query(tf_query)
            
            if not tf_df.empty:
                tf_filter = st.selectbox(
                    "Timeframe",
                    ["All"] + tf_df['timeframe'].tolist(),
                    key="ohlcv_r_tf"
                )
            else:
                tf_filter = "All"
        
        with col3:
            limit_r = st.slider("Rows", 10, 500, 100, key="limit_r")
        
        if st.button("📊 Load Data", type="primary", key="load_ohlcv_r"):
            where_clauses = []
            params = []
            
            if symbol_filter_r != "All":
                where_clauses.append("i.trading_symbol = ?")
                params.append(symbol_filter_r)
            
            if tf_filter != "All":
                where_clauses.append("r.timeframe = ?")
                params.append(tf_filter)
            
            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
            
            query = f"""
            SELECT 
                i.trading_symbol,
                i.name as instrument_name,
                r.*
            FROM ohlcv_resampled r
            JOIN instruments i ON r.instrument_key = i.instrument_key
            WHERE {where_sql}
            ORDER BY r.timestamp DESC, r.timeframe
            LIMIT {limit_r}
            """
            
            data = safe_query(query, params, "ohlcv_resampled")
            
            if not data.empty:
                st.success(f"✅ Loaded {len(data)} rows")
                st.dataframe(data, use_container_width=True, height=500)
            else:
                st.warning("No data found")

# ========================================
# TAB 5: F&O STOCKS MASTER
# ========================================

with tab5:
    st.header("🚀 F&O Stocks Master List")
    
    fo_cols = get_table_columns('fo_stocks_master')
    
    if not fo_cols:
        st.info("⚠️ No fo_stocks_master table found. Run Page 1 to create.")
    else:
        query = "SELECT * FROM fo_stocks_master ORDER BY trading_symbol"
        fo_stocks = safe_query(query)
        
        if not fo_stocks.empty:
            st.metric("Total F&O Stocks", len(fo_stocks))
            
            # Filter active/inactive if column exists
            if 'is_active' in fo_cols:
                active_filter = st.radio(
                    "Status",
                    ["All", "Active Only", "Inactive Only"],
                    horizontal=True
                )
                
                if active_filter == "Active Only":
                    fo_stocks = fo_stocks[fo_stocks['is_active'] == True]
                elif active_filter == "Inactive Only":
                    fo_stocks = fo_stocks[fo_stocks['is_active'] == False]
            
            st.dataframe(fo_stocks, use_container_width=True, height=500)
        else:
            st.info("No F&O stocks found")

# ========================================
# TAB 6: OTHER TABLES
# ========================================

with tab6:
    st.header("📈 Other Tables")
    
    other_tables = [t for t in tables if t not in ['instruments', 'ohlcv_1m', 'ohlcv_resampled', 'fo_stocks_master']]
    
    if not other_tables:
        st.info("No other tables found")
    else:
        selected_other = st.selectbox("Select Table", other_tables, key="other_table")
        
        if selected_other:
            columns = get_table_columns(selected_other)
            count = db.con.execute(f"SELECT COUNT(*) FROM {selected_other}").fetchone()[0]
            
            st.metric("Rows", f"{count:,}")
            
            if count > 0:
                limit_other = st.slider("Rows to display", 10, 500, 100, key="other_limit")
                
                # Smart query with trading_symbol if possible
                if 'instrument_key' in columns:
                    query = f"""
                    SELECT 
                        i.trading_symbol,
                        t.*
                    FROM {selected_other} t
                    LEFT JOIN instruments i ON t.instrument_key = i.instrument_key
                    LIMIT {limit_other}
                    """
                else:
                    query = f"SELECT * FROM {selected_other} LIMIT {limit_other}"
                
                data = safe_query(query, table_name=selected_other)
                
                if not data.empty:
                    st.dataframe(data, use_container_width=True, height=500)
                    
                    if 'instrument_key' in columns:
                        st.success("✅ Trading symbols added for identification")
            else:
                st.info("Table is empty")


# ========================================
# TAB 7: SQL CONSOLE
# ========================================

with tab7:
    st.header("🧪 SQL Console")
    st.markdown("Run **direct SQL commands** against DuckDB. Results are shown below.")

    sql_query = st.text_area(
        "SQL Command",
        height=220,
        placeholder="SELECT COUNT(DISTINCT instrument_key) FROM ohlcv_1m;"
    )

    col1, col2 = st.columns([1, 4])
    with col1:
        run_sql = st.button("▶ Run SQL", type="primary")
    with col2:
        st.caption("⚠️ Tip: Use LIMIT for large tables")

    if run_sql and sql_query.strip():
        try:
            result = db.con.execute(sql_query)

            # If query returns rows
            try:
                df = result.df()
                st.success(f"✅ Query executed. Rows returned: {len(df)}")
                st.dataframe(df, use_container_width=True)
            except:
                st.success("✅ Query executed successfully (no result set)")

        except Exception as e:
            st.error(f"❌ SQL Error: {str(e)}")


# Footer
st.divider()
st.caption("💡 Queries adapt to your database schema automatically!")