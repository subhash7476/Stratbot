"""
Page 1: Login & Instruments (FIXED VERSION)
Fixes:
1. Duplicate key error when updating instruments
2. F&O master table not being created
3. NaN/NULL handling for nullable fields
"""
# --- PROFESSIONAL UI BRIDGE ---
import numpy as np
import pandas as pd
import json
import gzip
import requests
from urllib.parse import urlparse, parse_qs
import webbrowser
from datetime import datetime
from pathlib import Path
import os
import sys
import streamlit as st
from core.config import get_access_token, save_access_token, load_config
from core.database import get_db


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
st.set_page_config(page_title="Login & Instruments",
                   layout="wide", page_icon="🔑")
load_css()
# Initialize database

db = get_db()

# Title
st.title("🔑 Login & Instruments")
st.markdown("**Authenticate with Upstox and download instrument master list**")

# Create tabs
tab1, tab2 = st.tabs(["🔐 Authentication", "📊 Instruments"])

# ============================================================================
# TAB 1: AUTHENTICATION
# ============================================================================
with tab1:
    cfg = load_config()

    # Token status
    if get_access_token():
        st.success("✅ Access token is valid and active")
    else:
        st.warning("⚠️ No access token found - please login below")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Option 1: Browser Login")

        if st.button("🌐 Get Fresh Access Token (Open Browser)", type="primary", use_container_width=True):
            api_key = cfg.get("api_key")
            redirect = cfg.get("redirect_uri", "http://localhost:8501")

            if not api_key:
                st.error("❌ API key missing in config/credentials.json")
            else:
                auth_url = f"https://api.upstox.com/v2/login/authorization/dialog?client_id={api_key}&redirect_uri={redirect}&response_type=code"
                st.info("🔄 Opening browser for Upstox login...")
                st.caption(f"Redirect URI: {redirect}")
                webbrowser.open(auth_url)

    with col2:
        st.subheader("Option 2: Manual URL")

        url_input = st.text_input(
            "Paste redirected URL",
            placeholder="http://localhost:8501?code=xxxxxx",
            help="After logging in via browser, copy the full URL from address bar"
        )

        if st.button("📝 Submit URL", use_container_width=True):
            if "code=" not in url_input:
                st.error("❌ URL missing ?code= parameter")
            else:
                try:
                    # Extract authorization code
                    parsed = urlparse(url_input)
                    code = parse_qs(parsed.query).get("code", [None])[0]

                    if not code:
                        st.error("❌ Could not extract code from URL")
                        st.stop()

                    # Load credentials
                    api_key = cfg.get("api_key")
                    client_secret = cfg.get("api_secret")
                    redirect_uri = cfg.get("redirect_uri")

                    if not api_key or not client_secret:
                        st.error(
                            "❌ API key or secret missing in config/credentials.json")
                        st.stop()

                    # Exchange code for access token
                    with st.spinner("🔄 Exchanging authorization code for access token..."):
                        token_url = "https://api.upstox.com/v2/login/authorization/token"
                        payload = {
                            "code": code,
                            "client_id": api_key,
                            "client_secret": client_secret,
                            "redirect_uri": redirect_uri,
                            "grant_type": "authorization_code"
                        }

                        headers = {
                            "Content-Type": "application/x-www-form-urlencoded"}

                        resp = requests.post(
                            token_url, data=payload, headers=headers, timeout=10)

                        if resp.status_code != 200:
                            st.error(f"❌ HTTP {resp.status_code}: {resp.text}")
                            st.stop()

                        data = resp.json()
                        token = data.get("access_token")

                        if not token:
                            st.error("❌ Response did not contain access_token")
                            st.stop()

                        save_access_token(token)
                        st.success("✅ Token saved successfully!")
                        st.balloons()

                except Exception as e:
                    st.error(f"❌ Failed to exchange code: {e}")

# ============================================================================
# TAB 2: INSTRUMENTS
# ============================================================================
with tab2:
    st.header("📊 Instrument Master Data")

    st.caption(f"🛠️ System ID: `{id(db)}` | Object: `{id(db)}`")
    st.caption(f"🛠️ System ID: `{id(db)}` | Connection: `{id(db.con)}`")

    # Check current status
    try:
        current_count = db.con.execute(
            "SELECT COUNT(*) FROM instruments").fetchone()[0]

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Instruments in Database", f"{current_count:,}")
        with col2:
            if current_count > 0:
                last_update = db.con.execute("""
                    SELECT MAX(last_updated) FROM instruments
                """).fetchone()[0]
                st.metric("Last Updated", str(last_update)[
                          :10] if last_update else "Never")
        with col3:
            # Check F&O table
            try:
                fo_count = db.con.execute(
                    "SELECT COUNT(*) FROM fo_stocks_master").fetchone()[0]
                st.metric("F&O Stocks", f"{fo_count:,}")
            except:
                st.metric("F&O Stocks", "Not created")

    except Exception as e:
        st.error(f"❌ Error checking database: {e}")
        current_count = 0

    st.divider()

    # Download button
    if current_count == 0:
        st.info("📋 No instruments found in database. Download to populate.")
    else:
        st.warning(
            f"⚠️ Database has {current_count:,} instruments. Re-downloading will replace all data.")

    if st.button("📥 Download & Update Instruments", type="primary", use_container_width=True):

        # Create progress indicators
        progress_bar = st.progress(0)
        status_text = st.empty()

        try:
            # Step 1: Download from Upstox
            status_text.text("📡 Downloading instruments from Upstox API...")
            progress_bar.progress(10)

            url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
            headers = {"Accept": "application/json"}

            response = requests.get(url, headers=headers, timeout=30)

            if response.status_code != 200:
                st.error(f"❌ Download failed: HTTP {response.status_code}")
                st.stop()

            st.info(f"📦 Downloaded {len(response.content) / (1024**2):.1f} MB")
            progress_bar.progress(30)

            # Step 2: Decompress
            status_text.text("📦 Decompressing data...")
            decompressed = gzip.decompress(response.content)
            data = decompressed.decode('utf-8')

            progress_bar.progress(40)

            # Step 3: Parse JSON
            status_text.text("🔍 Parsing instruments...")
            instruments = json.loads(data)

            st.info(f"📊 Found {len(instruments):,} instruments from API")
            progress_bar.progress(50)

            # Step 4: Convert to DataFrame with proper NaN handling
            status_text.text("🔄 Converting to database format...")

            df = pd.DataFrame(instruments)

            # Map columns
            column_mapping = {
                'instrument_key': 'instrument_key',
                'trading_symbol': 'trading_symbol',
                'name': 'name',
                'instrument_type': 'instrument_type',
                'exchange': 'exchange',
                'segment': 'segment',
                'lot_size': 'lot_size',
                'tick_size': 'tick_size',
                'expiry': 'expiry',
                'strike_price': 'strike_price'
            }

            df_clean = pd.DataFrame()

            for api_col, db_col in column_mapping.items():
                if api_col in df.columns:
                    df_clean[db_col] = df[api_col]
                else:
                    df_clean[db_col] = None

            # CRITICAL: Replace NaN/inf with None for nullable fields
            st.caption("🔧 Cleaning NaN/NULL values...")

            # Numeric fields that can be NULL
            nullable_numeric = ['lot_size', 'tick_size', 'strike_price']
            for col in nullable_numeric:
                if col in df_clean.columns:
                    # Replace NaN and inf with None
                    df_clean[col] = df_clean[col].replace(
                        [np.nan, np.inf, -np.inf], None)

            # String fields - replace NaN with None
            string_fields = ['name', 'instrument_type', 'exchange', 'segment']
            for col in string_fields:
                if col in df_clean.columns:
                    df_clean[col] = df_clean[col].replace([np.nan], None)

            # Convert expiry
            if 'expiry' in df_clean.columns and df_clean['expiry'].notna().any():
                try:
                    # Replace NaN first
                    df_clean['expiry'] = df_clean['expiry'].replace(
                        [np.nan], None)
                    # Convert non-null values
                    mask = df_clean['expiry'].notna()
                    if mask.any():
                        df_clean.loc[mask, 'expiry'] = pd.to_datetime(
                            df_clean.loc[mask, 'expiry'],
                            unit='ms',
                            errors='coerce'
                        ).dt.date
                except Exception as e:
                    st.warning(f"⚠️ Expiry conversion warning: {e}")
                    df_clean['expiry'] = None

            # Add timestamp
            df_clean['last_updated'] = datetime.now()

            # Remove duplicates
            st.caption("🔍 Removing duplicates...")
            before_dedup = len(df_clean)
            df_clean = df_clean.drop_duplicates(
                subset=['instrument_key'], keep='first')
            after_dedup = len(df_clean)

            if before_dedup != after_dedup:
                st.info(
                    f"Removed {before_dedup - after_dedup:,} duplicate instrument_keys")

            # Filter NULL trading symbols
            st.caption("🔍 Filtering invalid records...")
            before_filter = len(df_clean)
            df_clean = df_clean[df_clean['trading_symbol'].notna() & (
                df_clean['trading_symbol'] != '')]
            after_filter = len(df_clean)

            if before_filter != after_filter:
                st.info(
                    f"Filtered out {before_filter - after_filter:,} instruments with missing trading_symbol")

            # Ensure column order
            final_columns = [
                'instrument_key',
                'trading_symbol',
                'name',
                'instrument_type',
                'exchange',
                'segment',
                'lot_size',
                'tick_size',
                'expiry',
                'strike_price',
                'last_updated'
            ]

            df_clean = df_clean[final_columns]

            st.success(f"✅ Prepared {len(df_clean):,} valid instruments")
            progress_bar.progress(70)

            # Step 5: Save to DuckDB
            status_text.text("💾 Bulk inserting to DuckDB...")
            st.caption("This should take ~10-30 seconds...")

            # CRITICAL FIX: Delete existing data BEFORE insert
            db.con.execute("DELETE FROM instruments")

            # Use DuckDB's efficient DataFrame insert
            db.con.execute("""
                INSERT INTO instruments 
                SELECT * FROM df_clean
            """)

            progress_bar.progress(85)

            # Verify insertion
            actual_count = db.con.execute(
                "SELECT COUNT(*) FROM instruments").fetchone()[0]

            st.success(
                f"✅ Successfully saved {actual_count:,} instruments to database!")

            # ========================================
            # STEP 6: CREATE F&O STOCKS MASTER TABLE
            # ========================================

            status_text.text("🚀 Creating F&O Stocks Master List...")
            progress_bar.progress(90)

            try:
                # First, create table if not exists
                db.con.execute("""
                    CREATE TABLE IF NOT EXISTS fo_stocks_master (
                        trading_symbol VARCHAR PRIMARY KEY,
                        instrument_key VARCHAR NOT NULL,
                        name VARCHAR,
                        lot_size INTEGER,
                        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_active BOOLEAN DEFAULT TRUE
                    )
                """)

                # Query to get F&O stocks
                fo_query = """
                WITH fo_instruments AS (
                    SELECT DISTINCT 
                        name,
                        MAX(lot_size) as fo_lot_size
                    FROM instruments
                    WHERE segment = 'NSE_FO'
                      AND instrument_type = 'FUT'
                      AND name IS NOT NULL
                      AND name != ''
                    GROUP BY name
                ),
                ranked_instruments AS (
                    SELECT 
                        i.trading_symbol,
                        i.instrument_key,
                        i.name,
                        f.fo_lot_size as lot_size,
                        ROW_NUMBER() OVER (PARTITION BY i.trading_symbol ORDER BY i.instrument_key) as rn
                    FROM instruments i
                    JOIN fo_instruments f ON i.name = f.name
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

                fo_stocks = db.con.execute(fo_query).df()

                if not fo_stocks.empty:
                    # EFFICIENT METHOD: Delete all and bulk insert
                    db.con.execute("DELETE FROM fo_stocks_master")

                    # Add metadata columns
                    fo_stocks['last_updated'] = datetime.now()
                    fo_stocks['is_active'] = True

                    # Bulk insert
                    db.con.execute("""
                        INSERT INTO fo_stocks_master
                        SELECT * FROM fo_stocks
                    """)

                    final_fo_count = db.con.execute(
                        "SELECT COUNT(*) FROM fo_stocks_master").fetchone()[0]

                    st.success(
                        f"✅ Created F&O master list with {final_fo_count:,} stocks")
                else:
                    st.warning("⚠️ No F&O stocks found")

            except Exception as e:
                st.error(f"❌ F&O table creation failed: {e}")
                import traceback
                st.code(traceback.format_exc())

            progress_bar.progress(100)
            status_text.empty()
            progress_bar.empty()

            # Show summary
            st.divider()
            st.subheader("📊 Download Summary")

            col1, col2, col3 = st.columns(3)

            with col1:
                st.metric("Total Instruments", f"{actual_count:,}")

            with col2:
                try:
                    fo_count = db.con.execute(
                        "SELECT COUNT(*) FROM fo_stocks_master").fetchone()[0]
                    st.metric("F&O Stocks", f"{fo_count:,}")
                except:
                    st.metric("F&O Stocks", "Error")

            with col3:
                segments = db.con.execute("""
                    SELECT COUNT(DISTINCT segment) FROM instruments
                """).fetchone()[0]
                st.metric("Segments", segments)

            # Show breakdown
            with st.expander("📊 Segment Breakdown"):
                segment_df = db.con.execute("""
                    SELECT segment, COUNT(*) as count
                    FROM instruments
                    GROUP BY segment
                    ORDER BY count DESC
                    LIMIT 10
                """).df()

                st.dataframe(segment_df, use_container_width=True,
                             hide_index=True)

            st.balloons()

        except Exception as e:
            st.error(f"❌ Error during download: {e}")
            import traceback
            st.code(traceback.format_exc())

        finally:
            status_text.empty()
            progress_bar.empty()

# Footer
st.divider()
st.caption(
    "💡 Tip: Instruments auto-update daily. F&O master list is created automatically!")
