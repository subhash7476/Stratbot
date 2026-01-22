# scripts/integration_test.py
"""
Simple integration tests for core modules.
Usage:
    python scripts/integration_test.py
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import traceback
from core.indicators import compute_supertrend
from core.quant import generate_signals
from core.config import get_access_token
from core.data_utils import load_parquet
from core.api.upstox_client import UpstoxClient

SAMPLE = Path("data/processed")
SAMPLE_FILES = list(SAMPLE.glob("*.parquet"))

def offline_tests():
    print("== Offline tests: indicators & quant ==")
    if not SAMPLE_FILES:
        print("No sample parquet found in data/processed. Create one and re-run.")
        return False

    f = SAMPLE_FILES[0]
    print("Using sample:", f)
    df = load_parquet(f)
    # quick sanity: must have Open,High,Low,Close
    for c in ['Open','High','Low','Close']:
        if c not in df.columns:
            print("Missing column", c, "in", f)
            return False

    print("Running compute_supertrend...")
    st = compute_supertrend(df.head(200), atr_period=10, m=3.0)
    print("Supertrend OK:", 'Supertrend' in st.columns and 'ATR' in st.columns)

    print("Running generate_signals...")
    sig = generate_signals(st)
    print("Signals computed. Sample counts:", sig['FinalSignal'].value_counts().to_dict())
    return True

def network_tests():
    print("\n== Network tests: Upstox client (skipped if no token) ==")
    token = get_access_token()
    if not token:
        print("No token found in config. Skipping network tests.")
        return True

    client = UpstoxClient()
    try:
        exps = client.get_expiries_for_underlying("NIFTY")
        print("Expiries for NIFTY:", exps[:5])
    except Exception as e:
        print("Expiries call failed:", e)
        traceback.print_exc()
        return False

    # fetch OHLC for a known instrument key (Nifty)
    try:
        inst = "NSE_INDEX|Nifty 50"
        today = __import__("datetime").date.today()
        res = client.fetch_ohlc(inst, "days", 1, today, today)
        print("OHLC fetch OK (keys):", list(res.keys())[:5])
    except Exception as e:
        print("OHLC fetch failed:", e)
        traceback.print_exc()
        return False

    return True

if __name__ == "__main__":
    ok = offline_tests()
    net = network_tests()
    if ok and net:
        print("\nINTEGRATION TESTS PASSED\n")
    else:
        print("\nINTEGRATION TESTS FAILED\n")
