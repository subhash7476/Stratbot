"""
Diagnostic: Split all 9,800 test trades by TREND vs REVERSION,
volatility bucket (ATR%), and LONG vs SHORT.

Expert's recommended Tests A, B, and partial E.
"""
import sys, os, sqlite3, duckdb, json
import pandas as pd, numpy as np
from datetime import datetime, timedelta
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.analytics.resampler import resample_ohlcv
from core.strategies.pixityAI_batch_events import batch_generate_events

import logging
logging.basicConfig(level=logging.WARNING)

db = DatabaseManager(Path("data"))
query = MarketDataQuery(db)

# Get all symbols from scan
conn = sqlite3.connect("data/scanner/scanner_index.db")
scan_id = "scan_20260215_180817_d70dfc"
symbols_df = pd.read_sql_query(
    f"SELECT trading_symbol, symbol as instrument_key, test_run_id, test_pnl "
    f"FROM scanner_symbol_results "
    f"WHERE scan_id = '{scan_id}' AND test_trades > 0 "
    f"ORDER BY test_pnl DESC",
    conn,
)
conn.close()

TEST_START = datetime(2025, 6, 1)
TEST_END = datetime(2025, 12, 31)
WARMUP_START = TEST_START - timedelta(days=90)

results = []
errors = 0

for idx, row in symbols_df.iterrows():
    sym = row["instrument_key"]
    name = row["trading_symbol"]
    test_pnl = row["test_pnl"]
    run_id = row["test_run_id"]

    try:
        df_1m = query.get_ohlcv(sym, start_time=WARMUP_START, end_time=TEST_END, timeframe="1m")
        if df_1m.empty:
            errors += 1
            continue
        df_1m["timestamp"] = pd.to_datetime(df_1m["timestamp"])
        df_1m.set_index("timestamp", inplace=True)
        df_15m = resample_ohlcv(df_1m, "15m")

        events = batch_generate_events(df_15m, swing_period=5, reversion_k=2.0, time_stop_bars=12, bar_minutes=15)
        events = [e for e in events if e.timestamp >= TEST_START]

        # ATR% for volatility bucketing
        atr_vals = [e.metadata.get("atr_pct", 0) for e in events if e.metadata.get("atr_pct", 0) > 0]
        avg_atr_pct = np.mean(atr_vals) if atr_vals else 0

        # Build signal lookup: timestamp -> event_type
        sig_lookup = {}
        for e in events:
            sig_lookup[e.timestamp.isoformat()] = e.metadata.get("event_type", "UNKNOWN")

        # Load trades and match
        db_path = os.path.join("data/backtest/runs", f"{run_id}.duckdb")
        if not os.path.exists(db_path):
            errors += 1
            continue

        con = duckdb.connect(db_path, read_only=True)
        trades = con.execute("SELECT entry_ts, direction, pnl, entry_price, exit_price FROM trades").df()
        con.close()
        trades["entry_ts"] = pd.to_datetime(trades["entry_ts"])

        trend_pnl = rev_pnl = 0.0
        trend_trades = rev_trades = 0
        trend_wins = rev_wins = 0
        unmatched = 0

        for _, trade in trades.iterrows():
            ts_key = trade["entry_ts"].isoformat()
            etype = sig_lookup.get(ts_key, "UNKNOWN")

            if etype == "TREND":
                trend_pnl += trade["pnl"]
                trend_trades += 1
                if trade["pnl"] > 0:
                    trend_wins += 1
            elif etype == "REVERSION":
                rev_pnl += trade["pnl"]
                rev_trades += 1
                if trade["pnl"] > 0:
                    rev_wins += 1
            else:
                unmatched += 1

        results.append({
            "symbol": name,
            "test_pnl": test_pnl,
            "avg_atr_pct": avg_atr_pct,
            "trend_trades": trend_trades,
            "rev_trades": rev_trades,
            "trend_pnl": trend_pnl,
            "rev_pnl": rev_pnl,
            "trend_wr": (trend_wins / trend_trades * 100) if trend_trades > 0 else 0,
            "rev_wr": (rev_wins / rev_trades * 100) if rev_trades > 0 else 0,
            "unmatched": unmatched,
        })
    except Exception as e:
        errors += 1

    if (idx + 1) % 20 == 0:
        print(f"  Processed {idx+1}/{len(symbols_df)} symbols...", flush=True)

print(f"\nProcessed {len(results)} symbols, {errors} errors")

rdf = pd.DataFrame(results)

# -── TREND vs REVERSION ───
print(f"\n{'='*80}")
print(f"  DIAGNOSTIC 1: TREND vs REVERSION SPLIT (Test Period)")
print(f"{'='*80}")

total_trend_pnl = rdf["trend_pnl"].sum()
total_rev_pnl = rdf["rev_pnl"].sum()
total_trend_trades = int(rdf["trend_trades"].sum())
total_rev_trades = int(rdf["rev_trades"].sum())
total_trend_wins = sum(r["trend_wr"] * r["trend_trades"] / 100 for _, r in rdf.iterrows())
total_rev_wins = sum(r["rev_wr"] * r["rev_trades"] / 100 for _, r in rdf.iterrows())
total_unmatched = int(rdf["unmatched"].sum())

def safe_wr(wins, trades):
    return (wins / trades * 100) if trades > 0 else 0

def safe_avg(pnl, trades):
    return pnl / trades if trades > 0 else 0

print(f"\n  {'Type':<12} | {'Total PnL':>14} | {'Trades':>7} | {'WR':>6} | {'Avg PnL/Trade':>14}")
print(f"  {'-'*65}")
print(f"  {'TREND':<12} | Rs {total_trend_pnl:>10,.0f} | {total_trend_trades:>7} | {safe_wr(total_trend_wins, total_trend_trades):>5.1f}% | Rs {safe_avg(total_trend_pnl, total_trend_trades):>10,.1f}")
print(f"  {'REVERSION':<12} | Rs {total_rev_pnl:>10,.0f} | {total_rev_trades:>7} | {safe_wr(total_rev_wins, total_rev_trades):>5.1f}% | Rs {safe_avg(total_rev_pnl, total_rev_trades):>10,.1f}")
print(f"  {'COMBINED':<12} | Rs {total_trend_pnl+total_rev_pnl:>10,.0f} | {total_trend_trades+total_rev_trades:>7} | {safe_wr(total_trend_wins+total_rev_wins, total_trend_trades+total_rev_trades):>5.1f}% | Rs {safe_avg(total_trend_pnl+total_rev_pnl, total_trend_trades+total_rev_trades):>10,.1f}")
print(f"\n  Unmatched trades (no signal timestamp match): {total_unmatched}")

# -── VOLATILITY BUCKETS ───
print(f"\n{'='*80}")
print(f"  DIAGNOSTIC 2: VOLATILITY BUCKETS (by avg ATR%)")
print(f"{'='*80}")

rdf["atr_bucket"] = pd.qcut(rdf["avg_atr_pct"], q=3, labels=["Low Vol", "Mid Vol", "High Vol"])

for bucket in ["Low Vol", "Mid Vol", "High Vol"]:
    bdf = rdf[rdf["atr_bucket"] == bucket]
    bkt_pnl = bdf["test_pnl"].sum()
    bkt_count = len(bdf)
    bkt_profitable = (bdf["test_pnl"] > 0).sum()
    bkt_med = bdf["test_pnl"].median()
    bkt_atr_lo = bdf["avg_atr_pct"].min() * 100
    bkt_atr_hi = bdf["avg_atr_pct"].max() * 100

    bt_trend_pnl = bdf["trend_pnl"].sum()
    bt_rev_pnl = bdf["rev_pnl"].sum()
    bt_trend_trades = int(bdf["trend_trades"].sum())
    bt_rev_trades = int(bdf["rev_trades"].sum())
    bt_trend_wins = sum(r["trend_wr"] * r["trend_trades"] / 100 for _, r in bdf.iterrows())
    bt_rev_wins = sum(r["rev_wr"] * r["rev_trades"] / 100 for _, r in bdf.iterrows())

    print(f"\n  {bucket} ({bkt_count} symbols, ATR%: {bkt_atr_lo:.2f}% - {bkt_atr_hi:.2f}%)")
    print(f"    Total PnL: Rs {bkt_pnl:>10,.0f} | Profitable: {bkt_profitable}/{bkt_count} | Median: Rs {bkt_med:>8,.0f}")
    print(f"    TREND:     Rs {bt_trend_pnl:>10,.0f} ({bt_trend_trades} trades, {safe_wr(bt_trend_wins, bt_trend_trades):.1f}% WR)")
    print(f"    REVERSION: Rs {bt_rev_pnl:>10,.0f} ({bt_rev_trades} trades, {safe_wr(bt_rev_wins, bt_rev_trades):.1f}% WR)")

# -── LONG vs SHORT ───
print(f"\n{'='*80}")
print(f"  DIAGNOSTIC 3: LONG vs SHORT")
print(f"{'='*80}")

long_pnl = short_pnl = 0.0
long_trades = short_trades = 0
long_wins = short_wins = 0

for _, row in symbols_df.iterrows():
    run_id = row["test_run_id"]
    db_path = os.path.join("data/backtest/runs", f"{run_id}.duckdb")
    if not os.path.exists(db_path):
        continue
    try:
        con = duckdb.connect(db_path, read_only=True)
        trades = con.execute("SELECT direction, pnl FROM trades").df()
        con.close()
        for _, t in trades.iterrows():
            if t["direction"] == "LONG":
                long_pnl += t["pnl"]
                long_trades += 1
                if t["pnl"] > 0:
                    long_wins += 1
            else:
                short_pnl += t["pnl"]
                short_trades += 1
                if t["pnl"] > 0:
                    short_wins += 1
    except:
        pass

print(f"\n  {'Direction':<10} | {'Total PnL':>14} | {'Trades':>7} | {'WR':>6} | {'Avg PnL/Trade':>14}")
print(f"  {'-'*65}")
print(f"  {'LONG':<10} | Rs {long_pnl:>10,.0f} | {long_trades:>7} | {safe_wr(long_wins, long_trades):>5.1f}% | Rs {safe_avg(long_pnl, long_trades):>10,.1f}")
print(f"  {'SHORT':<10} | Rs {short_pnl:>10,.0f} | {short_trades:>7} | {safe_wr(short_wins, short_trades):>5.1f}% | Rs {safe_avg(short_pnl, short_trades):>10,.1f}")

# -── PnL OUTCOME DISTRIBUTION ───
print(f"\n{'='*80}")
print(f"  DIAGNOSTIC 4: PnL OUTCOME CLUSTERING")
print(f"{'='*80}")

all_pnls = []
for _, row in symbols_df.iterrows():
    run_id = row["test_run_id"]
    db_path = os.path.join("data/backtest/runs", f"{run_id}.duckdb")
    if not os.path.exists(db_path):
        continue
    try:
        con = duckdb.connect(db_path, read_only=True)
        trades = con.execute("SELECT pnl FROM trades").df()
        con.close()
        all_pnls.extend(trades["pnl"].tolist())
    except:
        pass

pnl_arr = np.array(all_pnls)
sl_hits = np.sum(pnl_arr < -400)   # Close to -500 (SL hit)
tp_hits = np.sum(pnl_arr > 800)    # Close to +1000 (TP hit)
time_stops = len(pnl_arr) - sl_hits - tp_hits

print(f"\n  Total trades: {len(pnl_arr)}")
print(f"  SL hits (PnL < -400):  {sl_hits} ({sl_hits/len(pnl_arr)*100:.1f}%)")
print(f"  TP hits (PnL > +800):  {tp_hits} ({tp_hits/len(pnl_arr)*100:.1f}%)")
print(f"  Time stops (middle):   {time_stops} ({time_stops/len(pnl_arr)*100:.1f}%)")
print(f"\n  Avg SL hit PnL: Rs {pnl_arr[pnl_arr < -400].mean():,.1f}")
print(f"  Avg TP hit PnL: Rs {pnl_arr[pnl_arr > 800].mean():,.1f}")
print(f"  Avg time stop PnL: Rs {pnl_arr[(pnl_arr >= -400) & (pnl_arr <= 800)].mean():,.1f}")

print()
