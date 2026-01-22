"""
metrics_utils.py
Reusable metric components for professional trading dashboards
"""

import streamlit as st
import pandas as pd
import numpy as np
from typing import Optional, Union, List, Dict, Any
import plotly.graph_objects as go

# ============================================================================
# CORE CSS STYLING
# ============================================================================


def load_metrics_css():
    """
    Load professional CSS styling for metrics
    """
    st.markdown("""
    <style>
        /* Metric Container Base */
        .metric-container {
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            transition: all 0.2s ease;
            height: 120px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }
        
        .metric-container:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 12px rgba(0, 0, 0, 0.15);
            border-color: #475569;
        }
        
        /* Metric Label */
        .metric-label {
            color: #94a3b8;
            font-size: 0.875rem;
            font-weight: 500;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        
        /* Metric Value */
        .metric-value {
            color: #ffffff;
            font-size: 1.875rem;
            font-weight: 700;
            font-variant-numeric: tabular-nums;
            line-height: 1.2;
        }
        
        /* Metric Delta */
        .metric-delta {
            font-size: 0.875rem;
            font-weight: 500;
            margin-top: 4px;
        }
        
        .metric-delta-positive {
            color: #22c55e;
        }
        
        .metric-delta-negative {
            color: #ef4444;
        }
        
        .metric-delta-neutral {
            color: #94a3b8;
        }
        
        /* Sparkline */
        .metric-sparkline {
            position: absolute;
            bottom: 10px;
            right: 10px;
            opacity: 0.7;
        }
        
        /* Status Badges */
        .status-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        
        .status-active {
            background-color: rgba(34, 197, 94, 0.1);
            color: #22c55e;
            border: 1px solid rgba(34, 197, 94, 0.3);
        }
        
        .status-paused {
            background-color: rgba(245, 158, 11, 0.1);
            color: #f59e0b;
            border: 1px solid rgba(245, 158, 11, 0.3);
        }
        
        .status-closed {
            background-color: rgba(239, 68, 68, 0.1);
            color: #ef4444;
            border: 1px solid rgba(239, 68, 68, 0.3);
        }
    </style>
    """, unsafe_allow_html=True)

# ============================================================================
# CORE METRIC COMPONENTS
# ============================================================================


def metric_card(
    label: str,
    value: Union[str, int, float],
    delta: Optional[str] = None,
    delta_color: str = "auto",
    format_as_currency: bool = False,
    help_text: Optional[str] = None,
    key: Optional[str] = None
) -> None:
    """
    Professional metric card component

    Args:
        label: Metric label/title
        value: Metric value (can be string or number)
        delta: Delta value (e.g., "+12.5%", "-$250")
        delta_color: "auto", "positive", "negative", or "neutral"
        format_as_currency: Format numbers as currency
        help_text: Optional help tooltip text
        key: Optional unique key for Streamlit
    """
    # Format value
    if isinstance(value, (int, float)):
        if format_as_currency:
            if abs(value) >= 1_000_000:
                formatted_value = f"${value/1_000_000:.2f}M"
            elif abs(value) >= 1_000:
                formatted_value = f"${value/1_000:.1f}K"
            else:
                formatted_value = f"${value:,.2f}"
        else:
            formatted_value = f"{value:,.2f}" if isinstance(
                value, float) else f"{value:,}"
    else:
        formatted_value = str(value)

    # Determine delta color
    delta_class = "metric-delta-neutral"
    if delta:
        if delta_color == "auto":
            if isinstance(delta, str):
                if delta.startswith("+"):
                    delta_class = "metric-delta-positive"
                elif delta.startswith("-"):
                    delta_class = "metric-delta-negative"
                else:
                    delta_class = "metric-delta-neutral"
            elif isinstance(delta, (int, float)):
                if delta > 0:
                    delta_class = "metric-delta-positive"
                elif delta < 0:
                    delta_class = "metric-delta-negative"
        else:
            delta_class = f"metric-delta-{delta_color}"

    # Delta HTML
    delta_html = f'<div class="metric-delta {delta_class}">{delta}</div>' if delta else ""

    # Help icon
    help_icon = f'<span title="{help_text}" style="cursor: help; margin-left: 4px;">ⓘ</span>' if help_text else ""

    # Generate HTML
    html = f"""
    <div class="metric-container" {'id="' + key + '"' if key else ''}>
        <div class="metric-label">
            {label}{help_icon}
        </div>
        <div class="metric-value">
            {formatted_value}
        </div>
        {delta_html}
    </div>
    """

    st.markdown(html, unsafe_allow_html=True)


def trading_metric_card(
    label: str,
    value: Union[str, int, float],
    change: Optional[str] = None,
    trend: Optional[str] = None,
    sparkline_data: Optional[List[float]] = None,
    status: Optional[str] = None,
    format_type: str = "auto"
) -> None:
    """
    Advanced trading metric card with sparkline and status

    Args:
        label: Metric label
        value: Metric value
        change: Change value (e.g., "+12.5%")
        trend: "up", "down", or "neutral"
        sparkline_data: List of values for sparkline
        status: "active", "paused", or "closed"
        format_type: "currency", "percentage", "number", or "auto"
    """
    # Format value based on type
    if format_type == "currency":
        if isinstance(value, (int, float)):
            if abs(value) >= 1_000_000:
                formatted_value = f"${value/1_000_000:.2f}M"
            elif abs(value) >= 1_000:
                formatted_value = f"${value/1_000:.1f}K"
            else:
                formatted_value = f"${value:,.2f}"
        else:
            formatted_value = str(value)
    elif format_type == "percentage":
        formatted_value = f"{value}%" if isinstance(
            value, (int, float)) else str(value)
    elif format_type == "number":
        formatted_value = f"{value:,.0f}" if isinstance(
            value, (int, float)) else str(value)
    else:  # auto
        formatted_value = str(value)

    # Determine colors based on trend
    if trend == "up":
        primary_color = "#22c55e"
        arrow = "↗"
    elif trend == "down":
        primary_color = "#ef4444"
        arrow = "↘"
    else:
        primary_color = "#94a3b8"
        arrow = "→"

    # Generate sparkline SVG
    sparkline_svg = ""
    if sparkline_data and len(sparkline_data) > 1:
        points = []
        max_val = max(sparkline_data)
        min_val = min(sparkline_data)
        range_val = max_val - min_val if max_val != min_val else 1

        for i, val in enumerate(sparkline_data):
            x = i * (60 / (len(sparkline_data) - 1))
            y = 25 - ((val - min_val) / range_val * 20)
            points.append(f"{x},{y}")

        points_str = " ".join(points)
        sparkline_svg = f"""
        <svg width="60" height="25" class="metric-sparkline">
            <polyline
                points="{points_str}"
                fill="none"
                stroke="{primary_color}"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
            />
        </svg>
        """

    # Change indicator
    change_html = ""
    if change:
        change_html = f"""
        <div style="display: flex; align-items: center; margin-top: 4px; color: {primary_color}; font-size: 0.875rem;">
            {arrow} {change}
        </div>
        """

    # Status badge
    status_html = ""
    if status:
        status_html = f'<div class="status-badge status-{status}" style="margin-top: 8px;">{status.upper()}</div>'

    # Generate HTML
    html = f"""
    <div class="metric-container" style="position: relative;">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{formatted_value}</div>
        {change_html}
        {status_html}
        {sparkline_svg}
    </div>
    """

    st.markdown(html, unsafe_allow_html=True)


def metric_grid(
    metrics: List[Dict[str, Any]],
    columns: int = 4,
    card_type: str = "standard"
) -> None:
    """
    Display metrics in a responsive grid

    Args:
        metrics: List of metric dictionaries with keys: label, value, delta, etc.
        columns: Number of columns in grid (2, 3, 4, or 6)
        card_type: "standard" or "trading"
    """
    # Validate columns
    valid_columns = [2, 3, 4, 6]
    if columns not in valid_columns:
        columns = 4

    # Create columns
    cols = st.columns(columns)

    # Display metrics
    for idx, metric in enumerate(metrics):
        col_idx = idx % columns
        with cols[col_idx]:
            if card_type == "trading":
                trading_metric_card(
                    label=metric.get("label", ""),
                    value=metric.get("value", ""),
                    change=metric.get("change"),
                    trend=metric.get("trend"),
                    sparkline_data=metric.get("sparkline_data"),
                    status=metric.get("status"),
                    format_type=metric.get("format_type", "auto")
                )
            else:
                metric_card(
                    label=metric.get("label", ""),
                    value=metric.get("value", ""),
                    delta=metric.get("delta"),
                    delta_color=metric.get("delta_color", "auto"),
                    format_as_currency=metric.get("format_as_currency", False),
                    help_text=metric.get("help_text"),
                    key=metric.get("key")
                )


def status_indicator(
    label: str,
    status: str,
    value: Optional[str] = None,
    size: str = "medium"
) -> None:
    """
    Status indicator with label and value

    Args:
        label: Status label
        status: Status type ("success", "warning", "error", "info")
        value: Optional value to display
        size: Size variant ("small", "medium", "large")
    """
    # Status colors
    status_colors = {
        "success": {"bg": "rgba(34, 197, 94, 0.1)", "text": "#22c55e", "border": "rgba(34, 197, 94, 0.3)"},
        "warning": {"bg": "rgba(245, 158, 11, 0.1)", "text": "#f59e0b", "border": "rgba(245, 158, 11, 0.3)"},
        "error": {"bg": "rgba(239, 68, 68, 0.1)", "text": "#ef4444", "border": "rgba(239, 68, 68, 0.3)"},
        "info": {"bg": "rgba(59, 130, 246, 0.1)", "text": "#3b82f6", "border": "rgba(59, 130, 246, 0.3)"}
    }

    colors = status_colors.get(status, status_colors["info"])

    # Size classes
    size_classes = {
        "small": {"padding": "4px 12px", "font_size": "0.75rem"},
        "medium": {"padding": "8px 16px", "font_size": "0.875rem"},
        "large": {"padding": "12px 20px", "font_size": "1rem"}
    }
    size_style = size_classes.get(size, size_classes["medium"])

    # Generate HTML
    value_html = f'<div style="font-weight: 600; margin-left: 8px;">{value}</div>' if value else ""

    html = f"""
    <div style="
        display: inline-flex;
        align-items: center;
        background-color: {colors['bg']};
        color: {colors['text']};
        border: 1px solid {colors['border']};
        border-radius: 20px;
        padding: {size_style['padding']};
        font-size: {size_style['font_size']};
        font-weight: 500;
    ">
        <div>{label}</div>
        {value_html}
    </div>
    """

    st.markdown(html, unsafe_allow_html=True)


# ============================================================================
# SPECIALIZED TRADING METRICS
# ============================================================================

def pnl_metric(
    label: str,
    value: float,
    daily_change: Optional[float] = None,
    weekly_change: Optional[float] = None
) -> None:
    """
    Specialized P&L metric with daily and weekly changes

    Args:
        label: Metric label
        value: Current P&L value
        daily_change: Daily change percentage
        weekly_change: Weekly change percentage
    """
    # Format value
    if abs(value) >= 1_000_000:
        formatted_value = f"${value/1_000_000:.2f}M"
    elif abs(value) >= 1_000:
        formatted_value = f"${value/1_000:.1f}K"
    else:
        formatted_value = f"${value:,.2f}"

    # Generate change indicators
    changes_html = ""
    if daily_change is not None or weekly_change is not None:
        changes = []
        if daily_change is not None:
            daily_color = "#22c55e" if daily_change >= 0 else "#ef4444"
            daily_arrow = "↗" if daily_change >= 0 else "↘"
            changes.append(
                f'<span style="color: {daily_color}">{daily_arrow} {abs(daily_change):.1f}% (D)</span>')

        if weekly_change is not None:
            weekly_color = "#22c55e" if weekly_change >= 0 else "#ef4444"
            weekly_arrow = "↗" if weekly_change >= 0 else "↘"
            changes.append(
                f'<span style="color: {weekly_color}">{weekly_arrow} {abs(weekly_change):.1f}% (W)</span>')

        changes_html = f'<div style="font-size: 0.75rem; margin-top: 4px;">{" | ".join(changes)}</div>'

    # Generate HTML
    html = f"""
    <div class="metric-container">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{formatted_value}</div>
        {changes_html}
    </div>
    """

    st.markdown(html, unsafe_allow_html=True)


def win_rate_metric(
    wins: int,
    losses: int,
    label: str = "Win Rate"
) -> None:
    """
    Win rate metric with counts

    Args:
        wins: Number of winning trades
        losses: Number of losing trades
        label: Metric label
    """
    total = wins + losses
    win_rate = (wins / total * 100) if total > 0 else 0

    # Determine color based on win rate
    if win_rate >= 70:
        color = "#22c55e"
    elif win_rate >= 50:
        color = "#f59e0b"
    else:
        color = "#ef4444"

    # Generate HTML
    html = f"""
    <div class="metric-container">
        <div class="metric-label">{label}</div>
        <div class="metric-value" style="color: {color}">{win_rate:.1f}%</div>
        <div style="font-size: 0.75rem; color: #94a3b8; margin-top: 4px;">
            {wins}W / {losses}L
        </div>
    </div>
    """

    st.markdown(html, unsafe_allow_html=True)


def gauge_metric(
    label: str,
    value: float,
    min_val: float = 0,
    max_val: float = 100,
    target: Optional[float] = None,
    thresholds: Optional[List[Dict]] = None
) -> None:
    """
    Gauge-style metric using Plotly

    Args:
        label: Metric label
        value: Current value
        min_val: Minimum value
        max_val: Maximum value
        target: Target value (shows as line)
        thresholds: List of threshold dictionaries with 'range' and 'color'
    """
    # Default thresholds
    if thresholds is None:
        thresholds = [
            {'range': [min_val, max_val * 0.7],
                'color': "rgba(239, 68, 68, 0.1)"},
            {'range': [max_val * 0.7, max_val * 0.9],
                'color': "rgba(245, 158, 11, 0.1)"},
            {'range': [max_val * 0.9, max_val],
                'color': "rgba(34, 197, 94, 0.1)"}
        ]

    # Create gauge figure
    fig = go.Figure()

    fig.add_trace(go.Indicator(
        mode="gauge+number",
        value=value,
        title={'text': label, 'font': {'size': 14}},
        number={'font': {'size': 24}},
        gauge={
            'axis': {'range': [min_val, max_val]},
            'bar': {'color': "#2563eb"},
            'steps': thresholds,
            'threshold': {
                'line': {'color': "red", 'width': 3},
                'thickness': 0.8,
                'value': target if target else max_val * 0.9
            }
        }
    ))

    fig.update_layout(
        height=200,
        margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#ffffff")
    )

    st.plotly_chart(fig, use_container_width=True, use_container_height=True)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def format_currency(value: float) -> str:
    """
    Format currency value with K/M suffix

    Args:
        value: Currency value

    Returns:
        Formatted string
    """
    if abs(value) >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    elif abs(value) >= 1_000:
        return f"${value/1_000:.1f}K"
    else:
        return f"${value:,.2f}"


def format_percentage(value: float, decimals: int = 1) -> str:
    """
    Format percentage value

    Args:
        value: Percentage value
        decimals: Number of decimal places

    Returns:
        Formatted string
    """
    return f"{value:.{decimals}f}%"


def create_sparkline_data(values: List[float], length: int = 10) -> List[float]:
    """
    Create sparkline data from values

    Args:
        values: List of values
        length: Desired sparkline length

    Returns:
        Resampled sparkline data
    """
    if len(values) <= length:
        return values

    # Resample to desired length
    indices = np.linspace(0, len(values) - 1, length, dtype=int)
    return [values[i] for i in indices]


# ============================================================================
# DEMO/EXAMPLE FUNCTION
# ============================================================================

def show_metrics_demo():
    """
    Demo function showing all metric types
    """
    st.title("📊 Metrics Components Demo")

    # Load CSS
    load_metrics_css()

    st.subheader("1. Standard Metric Cards")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        metric_card("Total Profit", 15240, "+12.5%", format_as_currency=True)
    with col2:
        metric_card("Win Rate", "72.4%", "+4.2%")
    with col3:
        metric_card("Active Trades", 8, "-2")
    with col4:
        metric_card("Avg Trade", 420.50, "-$12.50", format_as_currency=True)

    st.subheader("2. Trading Metric Cards with Sparklines")
    sparkline_up = [10, 12, 11, 13, 12, 14, 15, 14, 16]
    sparkline_down = [16, 15, 14, 13, 12, 11, 10, 11, 10]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        trading_metric_card(
            label="Portfolio Value",
            value=125000,
            change="+12.5%",
            trend="up",
            sparkline_data=sparkline_up,
            format_type="currency"
        )
    with col2:
        trading_metric_card(
            label="Risk Exposure",
            value="65%",
            change="-8.2%",
            trend="down",
            sparkline_data=sparkline_down,
            status="active"
        )
    with col3:
        win_rate_metric(wins=85, losses=35)
    with col4:
        pnl_metric("Today's P&L", 2450.75, daily_change=2.5, weekly_change=8.2)

    st.subheader("3. Metric Grid")
    metrics_list = [
        {"label": "Max Drawdown", "value": "-8.2%",
            "delta": "-1.2%", "delta_color": "negative"},
        {"label": "Sharpe Ratio", "value": "2.4",
            "delta": "+0.3", "delta_color": "positive"},
        {"label": "Volatility", "value": "18.5%",
            "delta": "-2.1%", "delta_color": "negative"},
        {"label": "Beta", "value": "1.2", "delta": "+0.1", "delta_color": "neutral"},
    ]
    metric_grid(metrics_list, columns=4)

    st.subheader("4. Status Indicators")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        status_indicator("Bot Status", "success", "Active", size="medium")
    with col2:
        status_indicator("Market", "info", "Open", size="medium")
    with col3:
        status_indicator("Connection", "error", "Lost", size="medium")
    with col4:
        status_indicator("Data Feed", "warning", "Delayed", size="medium")

    st.subheader("5. Gauge Metric")
    gauge_metric("Risk Level", 65, min_val=0, max_val=100, target=80)


# ============================================================================
# MAIN EXPORT
# ============================================================================

if __name__ == "__main__":
    # Run demo when file is executed directly
    show_metrics_demo()
