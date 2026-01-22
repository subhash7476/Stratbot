# app.py
import sys
import os
import json
import warnings

warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=DeprecationWarning)

sys.path.append(os.path.dirname(__file__))   # ← keeps imports working
sys.path.append(os.path.join(os.path.dirname(__file__), "utils"))

import streamlit as st

# ← ONLY HERE we set page config — once for the entire app
st.set_page_config(
    page_title="Trading Bot Pro",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ──────────────────────────────────────────────────────────────
st.sidebar.image("https://i.imgur.com/8Q2X2.png", width=200)  # optional logo later
st.sidebar.title("Trading Bot Control Panel")
st.sidebar.markdown("**Portable • Profitable • Unstoppable**")

# Navigation menu with organized sections
st.sidebar.markdown("### 🔐 Setup")
pages = {
    "Login & Instruments": "pages/1_Login_&_Instruments.py",
}

st.sidebar.markdown("### 📊 Data Management")
pages.update({
    "Fetch & Manage Data": "pages/2_Fetch_&_Manage_Data.py",
    "Data Organizer": "pages/7_Data_Organizer_v2.py",
    "Data Inspector": "pages/8_Data_Inspector.py",
})

st.sidebar.markdown("### 📈 Backtesting")
pages.update({
    "Supertrend Backtester": "pages/3_Supertrend_Backtester.py",
    "Strategy Lab": "pages/6_New_Strategy_lab.py",
    "🔬 Batch Stock Analyzer": "pages/9_Batch_Stock_Analyzer.py",
})

st.sidebar.markdown("### 🤖 AI & Live Trading")
pages.update({
    "AI Loss Filter": "pages/4_AI_Loss_Filter.py",
    "Live Trading (AI Protected)": "pages/5_Live_Trading_With_AI.py",
})

selection = st.sidebar.radio("Navigate →", list(pages.keys()), label_visibility="collapsed")

# Load the selected page
try:
    with open(pages[selection], encoding="utf-8") as f:
        exec(f.read())
except FileNotFoundError:
    st.error(f"Page file not found: {pages[selection]}")
    st.info("This page may not be implemented yet. Please check the pages/ directory.")
except Exception as e:
    st.error(f"Error loading page: {e}")
    import traceback
    with st.expander("Show error details"):
        st.code(traceback.format_exc())