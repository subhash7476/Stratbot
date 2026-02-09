import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add root to sys.path
ROOT = Path("D:/BOT/root")
sys.path.append(str(ROOT))

from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.analytics.regime_engine import RegimeDetector

def run_diagnostic():
    db = DatabaseManager(ROOT / "data")
    query = MarketDataQuery(db)
    detector = RegimeDetector()
    
    symbol = "NSE_EQ|INE002A01018"
    print(f"--- REGIME DIAGNOSTIC FOR {symbol} ---")
    
    # Load last 200 bars for accurate indicators
    df = query.get_ohlcv(symbol, limit=200)
    
    if df.empty:
        print("No data found for symbol.")
        return

    # Run detection
    snapshot = detector.detect(symbol, df)
    
    if not snapshot:
        print("Could not generate snapshot (insufficient data).")
        return

    # Print Detailed Snapshot
    print(f"Timestamp: {snapshot.timestamp}")
    print(f"Market Regime: {snapshot.regime}")
    print(f"Momentum Bias: {snapshot.momentum_bias}")
    print(f"Trend Strength (ADX): {snapshot.trend_strength:.2f}")
    print(f"Volatility Level: {snapshot.volatility_level}")
    print(f"MA Fast (20): {snapshot.ma_fast:.2f}")
    print(f"MA Med  (50): {snapshot.ma_medium:.2f}")
    print(f"MA Slow (200): {snapshot.ma_slow:.2f}")
    print(f"Current Price: {df['close'].iloc[-1]:.2f}")
    
    # Logic Explanation
    print("\n--- LOGIC BREAKDOWN ---")
    adx_val = snapshot.trend_strength * 50
    print(f"1. Trend Check: ADX is {adx_val:.1f}. (Threshold > 22 for Trend, < 20 for Range)")
    
    price = df['close'].iloc[-1]
    f, m = snapshot.ma_fast, snapshot.ma_medium
    is_bull = price > f > m
    is_bear = price < f < m
    print(f"2. Alignment: Price({price:.1f}) > EMA20({f:.1f}) > EMA50({m:.1f}) is {is_bull}")
    print(f"3. Result: Determined regime as {snapshot.regime}")

if __name__ == "__main__":
    run_diagnostic()
