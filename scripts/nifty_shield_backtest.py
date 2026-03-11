#!/usr/bin/env python3
"""
NiftyShield Walk-Forward Backtest
----------------------------------
Replays 1m Nifty + BankNifty bars through NiftyShieldStrategy using
Black-76 synthetic option pricing. No real option chain needed.

Usage:
    python scripts/nifty_shield_backtest.py --start 2024-01-01 --end 2025-12-31
    python scripts/nifty_shield_backtest.py --start 2025-01-01 --end 2025-12-31 --structure short_straddle
    python scripts/nifty_shield_backtest.py --start 2024-01-01 --end 2025-12-31 --by-regime
    python scripts/nifty_shield_backtest.py --walkforward
"""
import sys
import argparse
import logging
from datetime import datetime, date, timedelta, time as dt_time
from pathlib import Path
from typing import List, Dict, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.strategies.nifty_shield_strategy import NiftyShieldStrategy, NF_SYMBOL
from core.logging import setup_logger

logger = setup_logger("ns_backtest")
IST = ZoneInfo("Asia/Kolkata")

BN_SYMBOL = "NSE_INDEX|Nifty Bank"


def _trading_days(start: date, end: date, nf_index: set) -> List[date]:
    """Return sorted list of weekdays between start and end that have NF data."""
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5 and cur in nf_index:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def run_backtest(
    start: date,
    end: date,
    config_path: str = "core/models/nifty_shield_config.json",
    verbose: bool = False,
) -> List[Dict]:
    db = DatabaseManager(Path("data"))
    q  = MarketDataQuery(db)

    logger.info(f"Loading NF 1m data {start} → {end} ...")
    nf_df = q.get_ohlcv(NF_SYMBOL, datetime.combine(start, dt_time(0, 0)),
                         datetime.combine(end, dt_time(23, 59)), "1m")
    logger.info(f"Loading BN 1m data {start} → {end} ...")
    bn_df = q.get_ohlcv(BN_SYMBOL, datetime.combine(start, dt_time(0, 0)),
                         datetime.combine(end, dt_time(23, 59)), "1m")

    if nf_df.empty:
        logger.error("No NF data found — aborting")
        return []

    nf_df["timestamp"] = nf_df["timestamp"].dt.tz_localize(IST, ambiguous="NaT", nonexistent="NaT")
    bn_df["timestamp"] = bn_df["timestamp"].dt.tz_localize(IST, ambiguous="NaT", nonexistent="NaT")

    nf_by_date: Dict[date, List[Dict]] = {}
    for _, row in nf_df.iterrows():
        d = row["timestamp"].date()
        nf_by_date.setdefault(d, []).append(row.to_dict())

    bn_by_date: Dict[date, List[Dict]] = {}
    for _, row in bn_df.iterrows():
        d = row["timestamp"].date()
        bn_by_date.setdefault(d, []).append(row.to_dict())

    trading_days = _trading_days(start, end, set(nf_by_date.keys()))
    logger.info(f"Backtest: {len(trading_days)} trading days | {start} → {end}")

    strategy = NiftyShieldStrategy(db, config_path=config_path, backtest_mode=True)

    results = []
    for session_date in trading_days:
        strategy.on_session_start(session_date)

        nf_bars = nf_by_date.get(session_date, [])
        bn_bars = bn_by_date.get(session_date, [])
        bn_by_ts = {b["timestamp"]: b for b in bn_bars}

        for bar in nf_bars:
            bn_bar = bn_by_ts.get(bar["timestamp"])
            if bn_bar:
                strategy.on_bn_bar(bn_bar)
            strategy.on_bar(bar)

        result = strategy.get_session_result()
        if result:
            results.append(result)
            if verbose:
                logger.info(
                    f"  {session_date} [{result['day_type']:<12}] "
                    f"VIX={result['vix_close'] or '?':>5} | "
                    f"lots={result['lots']} | "
                    f"premium={result['total_premium']:.1f} | "
                    f"PnL Rs {result['pnl_net_rs']:+,.0f} | "
                    f"exit={result['exit_reason']}"
                )

    return results


def print_summary(results: List[Dict], label: str = ""):
    if not results:
        print(f"\n{label or 'Result'}: No trades generated.")
        return

    total     = len(results)
    wins      = sum(1 for r in results if r["pnl_net_rs"] > 0)
    losses    = total - wins
    net_pnl   = sum(r["pnl_net_rs"] for r in results)
    gross_pnl = sum(r["pnl_gross_rs"] for r in results)
    avg_prem  = sum(r["total_premium"] for r in results) / total
    avg_pnl   = net_pnl / total

    # Sharpe (daily PnL)
    import statistics
    pnls = [r["pnl_net_rs"] for r in results]
    if len(pnls) > 1 and statistics.stdev(pnls) > 0:
        sharpe = (statistics.mean(pnls) / statistics.stdev(pnls)) * (252 ** 0.5)
    else:
        sharpe = 0.0

    # Max drawdown (running equity)
    equity = 0.0
    peak   = 0.0
    max_dd = 0.0
    for r in results:
        equity += r["pnl_net_rs"]
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    # Exit reason breakdown
    reasons: Dict[str, int] = {}
    for r in results:
        k = r["exit_reason"]
        reasons[k] = reasons.get(k, 0) + 1

    # Regime breakdown
    regimes: Dict[str, Dict] = {}
    for r in results:
        dt = r["day_type"]
        if dt not in regimes:
            regimes[dt] = {"count": 0, "pnl": 0.0, "wins": 0}
        regimes[dt]["count"] += 1
        regimes[dt]["pnl"]   += r["pnl_net_rs"]
        if r["pnl_net_rs"] > 0:
            regimes[dt]["wins"] += 1

    hdr = f"\n{'='*60}\n  {label or 'NiftyShield Backtest Results'}\n{'='*60}"
    print(hdr)
    print(f"  Trades          : {total} ({wins} wins / {losses} losses)")
    print(f"  Win Rate        : {wins/total*100:.1f}%")
    print(f"  Net PnL         : Rs {net_pnl:+,.0f}")
    print(f"  Gross PnL       : Rs {gross_pnl:+,.0f}")
    print(f"  Avg PnL/trade   : Rs {avg_pnl:+,.0f}")
    print(f"  Avg Premium     : {avg_prem:.1f} pts")
    print(f"  Sharpe (ann.)   : {sharpe:.2f}")
    print(f"  Max Drawdown    : Rs {max_dd:,.0f}")
    print(f"\n  Exit Reasons:")
    for reason, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"    {reason:<20} {cnt:>3} trades")
    print(f"\n  By Regime:")
    for dt, info in sorted(regimes.items()):
        wr = info["wins"] / info["count"] * 100
        print(f"    {dt:<15} {info['count']:>3} trades | "
              f"WR {wr:.0f}% | PnL Rs {info['pnl']:+,.0f}")
    print("="*60)


def run_walkforward(config_path: str):
    """4-window walk-forward validation."""
    windows = [
        ("Window 1 (H2 2024)", date(2024, 7, 1),  date(2024, 12, 31)),
        ("Window 2 (H1 2025)", date(2025, 1, 1),  date(2025, 6, 30)),
        ("Window 3 (H2 2025)", date(2025, 7, 1),  date(2025, 12, 31)),
        ("Window 4 (2026 YTD)",date(2026, 1, 1),  date(2026, 2, 28)),
    ]
    all_results = []
    for label, start, end in windows:
        results = run_backtest(start, end, config_path)
        print_summary(results, label)
        all_results.extend(results)

    print_summary(all_results, "COMBINED WALK-FORWARD")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NiftyShield Walk-Forward Backtest")
    parser.add_argument("--start",       default="2024-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end",         default="2025-12-31", help="End date YYYY-MM-DD")
    parser.add_argument("--config",      default="core/models/nifty_shield_config.json")
    parser.add_argument("--verbose",     action="store_true", help="Print each trade")
    parser.add_argument("--walkforward", action="store_true", help="Run 4-window walk-forward")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    logger.setLevel(logging.INFO)

    if args.walkforward:
        run_walkforward(args.config)
    else:
        results = run_backtest(
            date.fromisoformat(args.start),
            date.fromisoformat(args.end),
            config_path=args.config,
            verbose=args.verbose,
        )
        print_summary(results)
