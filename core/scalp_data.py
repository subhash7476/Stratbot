# scalp_data.py
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import streamlit as st


@st.cache_data(ttl=60)  # Cache for 60 seconds
def get_stock_data(symbol, interval="1m", period="1d"):
    """Get stock data for Indian stocks"""
    try:
        # Add .NS for NSE stocks
        ticker_symbol = f"{symbol}.NS"
        ticker = yf.Ticker(ticker_symbol)

        data = ticker.history(period=period, interval=interval)

        if data.empty:
            # Try without .NS
            ticker = yf.Ticker(symbol)
            data = ticker.history(period=period, interval=interval)

        return data
    except Exception as e:
        st.error(f"Error fetching data for {symbol}: {e}")
        return pd.DataFrame()


def calculate_atr(data, period=14):
    """Calculate Average True Range"""
    if data.empty:
        return 0

    high_low = data['High'] - data['Low']
    high_close = abs(data['High'] - data['Close'].shift())
    low_close = abs(data['Low'] - data['Close'].shift())

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)

    return true_range.rolling(window=period).mean().iloc[-1]


def get_vwap(data):
    """Calculate Volume Weighted Average Price"""
    if data.empty or 'Volume' not in data.columns:
        return 0

    data['VWAP'] = (data['Volume'] * (data['High'] + data['Low'] +
                    data['Close']) / 3).cumsum() / data['Volume'].cumsum()
    return data['VWAP'].iloc[-1]
