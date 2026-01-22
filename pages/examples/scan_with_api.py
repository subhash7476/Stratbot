# pages/examples/scan_with_api.py
"""
EXAMPLE: How to Use FastAPI Backend for Scans
=============================================

This example shows how to convert the "Rebuild & Scan" functionality
to use the FastAPI backend, which prevents tab navigation issues.

KEY BENEFITS:
1. No st.rerun() needed - backend runs scan in background
2. Tab stays on current position
3. Progress updates without blocking
4. Can cancel running scans

Copy this pattern to your pages to fix tab navigation issues.
"""

import streamlit as st
import time
from datetime import datetime
from typing import Optional

# Import the API client
from core.api_client import (
    BackendClient,
    is_backend_available,
    start_scan_async,
    get_scan_status,
    get_scan_results
)

st.set_page_config(page_title="Scan Example", layout="wide")


def main():
    st.title("Scan with API Backend - Example")

    # Check if backend is running
    backend_available = is_backend_available()

    if not backend_available:
        st.error("""
        ⚠️ **Backend not running!**

        Start it with: `python run_backend.py`

        Then refresh this page.
        """)
        st.stop()

    st.success("✅ Backend is running")

    # Create tabs - these should NOT reset when scan runs
    tab1, tab2, tab3 = st.tabs(["📊 Scanner", "📈 Results", "⚙️ Settings"])

    with tab1:
        render_scanner_tab()

    with tab2:
        render_results_tab()

    with tab3:
        render_settings_tab()


def render_scanner_tab():
    """Scanner tab with non-blocking scan"""

    st.header("Live Scanner")

    # Configuration in the same tab (IMPORTANT: widgets before button handler reads them)
    col1, col2 = st.columns(2)

    with col1:
        sl_mode = st.selectbox(
            "SL/TP Mode",
            ["ATR based", "Fixed %"],
            key="scan_sl_mode"
        )

    with col2:
        if sl_mode == "ATR based":
            atr_mult = st.number_input("ATR Multiplier", 0.5, 5.0, 2.0, 0.5, key="scan_atr")
            rr_ratio = st.number_input("Risk:Reward", 1.0, 5.0, 2.0, 0.5, key="scan_rr")
        else:
            sl_pct = st.number_input("SL %", 0.2, 10.0, 1.0, 0.2, key="scan_sl_pct")
            tp_pct = st.number_input("TP %", 0.5, 20.0, 2.0, 0.5, key="scan_tp_pct")

    st.markdown("---")

    # Action buttons
    btn_col1, btn_col2, btn_col3 = st.columns(3)

    with btn_col1:
        start_clicked = st.button(
            "🚀 Start Scan",
            type="primary",
            use_container_width=True,
            disabled="active_scan_id" in st.session_state
        )

    with btn_col2:
        cancel_clicked = st.button(
            "❌ Cancel",
            type="secondary",
            use_container_width=True,
            disabled="active_scan_id" not in st.session_state
        )

    with btn_col3:
        clear_clicked = st.button(
            "🗑️ Clear Results",
            type="secondary",
            use_container_width=True
        )

    # Handle START button
    if start_clicked:
        # Build config from widget values (widgets are already rendered above!)
        config = {
            "sl_mode": sl_mode,
            "atr_mult": st.session_state.get("scan_atr", 2.0),
            "rr_ratio": st.session_state.get("scan_rr", 2.0),
            "sl_pct": st.session_state.get("scan_sl_pct", 1.0),
            "tp_pct": st.session_state.get("scan_tp_pct", 2.0),
            "rebuild_resampled": True,
            "min_score": 4
        }

        # Start scan via API (non-blocking!)
        scan_id = start_scan_async(config)

        if scan_id:
            st.session_state["active_scan_id"] = scan_id
            st.session_state["scan_started_at"] = datetime.now()
            st.success(f"✅ Scan started! ID: {scan_id}")
            # NO st.rerun() needed! The progress display below will update
        else:
            st.error("Failed to start scan. Is the backend running?")

    # Handle CANCEL button
    if cancel_clicked and "active_scan_id" in st.session_state:
        client = BackendClient()
        if client.cancel_scan(st.session_state["active_scan_id"]):
            st.warning("Scan cancelled")
            del st.session_state["active_scan_id"]
        else:
            st.error("Failed to cancel scan")

    # Handle CLEAR button
    if clear_clicked:
        for key in ["scan_results", "active_scan_id", "scan_started_at"]:
            if key in st.session_state:
                del st.session_state[key]
        st.info("Results cleared")

    st.markdown("---")

    # Show progress if scan is running
    if "active_scan_id" in st.session_state:
        render_scan_progress()


def render_scan_progress():
    """Display scan progress with auto-refresh"""

    scan_id = st.session_state["active_scan_id"]
    status = get_scan_status(scan_id)

    if status is None:
        st.error(f"Could not get status for scan {scan_id}")
        return

    scan_status = status.get("status")
    progress = status.get("progress", {})

    # Progress bar
    percent = progress.get("percent", 0) / 100
    current_symbol = progress.get("current_symbol", "")
    phase = progress.get("phase", "")

    st.progress(percent, text=f"{phase.capitalize()}: {current_symbol} ({progress.get('current', 0)}/{progress.get('total', 0)})")

    # Status display
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Status", scan_status.upper())
    with col2:
        st.metric("Progress", f"{percent * 100:.1f}%")
    with col3:
        duration = status.get("duration_seconds", 0)
        st.metric("Duration", f"{duration:.1f}s" if duration else "-")

    # Handle completion
    if scan_status == "completed":
        results = get_scan_results(scan_id)
        if results:
            st.session_state["scan_results"] = results
            del st.session_state["active_scan_id"]
            st.success(f"✅ Scan complete! Found {results.get('signals_found', 0)} signals")
            st.balloons()

    elif scan_status == "failed":
        st.error(f"Scan failed: {status.get('error', 'Unknown error')}")
        del st.session_state["active_scan_id"]

    elif scan_status == "cancelled":
        st.warning("Scan was cancelled")
        if "active_scan_id" in st.session_state:
            del st.session_state["active_scan_id"]

    elif scan_status in ("pending", "running"):
        # Auto-refresh progress (use experimental_fragment in newer Streamlit)
        # For now, just show a refresh hint
        st.info("🔄 Progress updates automatically. Click 'Start Scan' again to refresh.")

        # Auto-refresh every 2 seconds while scan is running
        time.sleep(2)
        st.rerun()  # This is OK here because we're just refreshing progress, not triggering new work


def render_results_tab():
    """Results tab"""

    st.header("Scan Results")

    if "scan_results" not in st.session_state:
        st.info("No results yet. Run a scan first.")
        return

    results = st.session_state["scan_results"]

    # Summary
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Scanned", results.get("total_scanned", 0))
    with col2:
        st.metric("Signals Found", results.get("signals_found", 0))
    with col3:
        st.metric("Tradable (Score 5)", len(results.get("tradable_signals", [])))

    st.markdown("---")

    # Tradable signals (Score 5)
    st.subheader("🎯 Tradable Signals (Score 5)")
    tradable = results.get("tradable_signals", [])

    if tradable:
        import pandas as pd
        df = pd.DataFrame(tradable)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No tradable signals found")

    # Ready signals (Score 4)
    st.subheader("🔔 Ready Signals (Score 4)")
    ready = results.get("ready_signals", [])

    if ready:
        import pandas as pd
        df = pd.DataFrame(ready)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No ready signals found")


def render_settings_tab():
    """Settings tab"""

    st.header("Settings")

    st.markdown("""
    ### Backend Configuration

    The FastAPI backend handles scans in the background, which:
    - Prevents tab navigation issues
    - Allows progress tracking
    - Supports scan cancellation
    - Enables concurrent scans
    """)

    # Show backend status
    client = BackendClient()
    status = client._request("GET", "/api/market/status")

    if status:
        st.json(status)
    else:
        st.warning("Could not fetch backend status")

    # Active scans
    st.subheader("Active Scans")
    active = client.get_active_scans()
    if active:
        st.write(active)
    else:
        st.info("No active scans")


if __name__ == "__main__":
    main()
