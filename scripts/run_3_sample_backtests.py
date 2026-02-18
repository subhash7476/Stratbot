"""
Run 3 sample backtests from Phase 6 scan with:
  - SL = 2.0 x ATR, TP = 4.0 x ATR (current risk engine hardcoded values)
  - No trade cap (max_trades_per_day=999999 in runner)
  - Same train/test periods as Phase 6

Symbols (ranked by test PnL from Phase 6):
  Rank 10:  LAURUSLABS (NSE_EQ|INE947Q01028)
  Rank 100: LTIM       (NSE_EQ|INE214T01019)
  Rank 196: ONGC       (NSE_EQ|INE213A01029)
"""
import sys, os, logging
from datetime import datetime
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database.manager import DatabaseManager
from core.backtest.runner import BacktestRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("core.database").setLevel(logging.WARNING)
logging.getLogger("core.runner").setLevel(logging.WARNING)
logging.getLogger("core.execution").setLevel(logging.WARNING)
logging.getLogger("core.brokers").setLevel(logging.WARNING)

SYMBOLS = [
    ("NSE_EQ|INE947Q01028", "LAURUSLABS", 10),
    ("NSE_EQ|INE214T01019", "LTIM", 100),
    ("NSE_EQ|INE213A01029", "ONGC", 196),
]

TRAIN_START = datetime(2024, 10, 17)
TRAIN_END   = datetime(2025, 5, 31)
TEST_START  = datetime(2025, 6, 1)
TEST_END    = datetime(2025, 12, 31)
CAPITAL     = 100000.0

STRATEGY_PARAMS = {
    "skip_meta_model": True,
    "use_signal_quality_filter": False,
}


def read_metrics(db, run_id):
    try:
        with db.backtest_index_reader() as conn:
            row = conn.execute(
                "SELECT total_trades, win_rate, total_pnl, max_drawdown "
                "FROM backtest_runs WHERE run_id = ?", [run_id]
            ).fetchone()
            if row:
                return {
                    "trades": row[0] or 0,
                    "win_rate": row[1] or 0.0,
                    "pnl": row[2] or 0.0,
                    "max_dd": row[3] or 0.0,
                }
    except Exception as e:
        print(f"  Error reading metrics for {run_id}: {e}")
    return {"trades": 0, "win_rate": 0.0, "pnl": 0.0, "max_dd": 0.0}


def main():
    db = DatabaseManager(Path("data"))
    runner = BacktestRunner(db)

    results = []

    for instrument_key, name, rank in SYMBOLS:
        print(f"\n{'='*70}")
        print(f"  {name} (Rank #{rank}) — {instrument_key}")
        print(f"  SL=2.0xATR, TP=4.0xATR, no trade cap")
        print(f"{'='*70}")

        slug = instrument_key.split("|")[-1].lower()

        # --- Train ---
        train_id = f"sample3_train_{slug}"
        print(f"\n  [TRAIN] {TRAIN_START.date()} -> {TRAIN_END.date()}")
        try:
            runner.run(
                strategy_id="pixityAI_meta",
                symbol=instrument_key,
                start_time=TRAIN_START,
                end_time=TRAIN_END,
                initial_capital=CAPITAL,
                strategy_params=dict(STRATEGY_PARAMS),
                timeframe="15m",
                run_id=train_id,
            )
            train = read_metrics(db, train_id)
            print(f"    PnL: Rs {train['pnl']:,.2f} | Trades: {train['trades']} | "
                  f"WR: {train['win_rate']:.1f}% | DD: {train['max_dd']:.2f}%")
        except Exception as e:
            print(f"    FAILED: {e}")
            train = {"trades": 0, "win_rate": 0.0, "pnl": 0.0, "max_dd": 0.0}

        # --- Test ---
        test_id = f"sample3_test_{slug}"
        print(f"\n  [TEST]  {TEST_START.date()} -> {TEST_END.date()}")
        try:
            runner.run(
                strategy_id="pixityAI_meta",
                symbol=instrument_key,
                start_time=TEST_START,
                end_time=TEST_END,
                initial_capital=CAPITAL,
                strategy_params=dict(STRATEGY_PARAMS),
                timeframe="15m",
                run_id=test_id,
            )
            test = read_metrics(db, test_id)
            print(f"    PnL: Rs {test['pnl']:,.2f} | Trades: {test['trades']} | "
                  f"WR: {test['win_rate']:.1f}% | DD: {test['max_dd']:.2f}%")
        except Exception as e:
            print(f"    FAILED: {e}")
            test = {"trades": 0, "win_rate": 0.0, "pnl": 0.0, "max_dd": 0.0}

        results.append((name, rank, train, test))

    # --- Summary ---
    print(f"\n\n{'='*100}")
    print(f"  SUMMARY — SL=2.0xATR, TP=4.0xATR, NO TRADE CAP")
    print(f"{'='*100}")
    print(f"  {'Symbol':<15} {'Rank':>5} | {'Train PnL':>12} {'Trades':>7} {'WR':>6} {'DD':>7} | {'Test PnL':>12} {'Trades':>7} {'WR':>6} {'DD':>7}")
    print(f"  {'-'*95}")
    for name, rank, train, test in results:
        print(f"  {name:<15} {rank:>5} | "
              f"Rs {train['pnl']:>9,.0f} {train['trades']:>7} {train['win_rate']:>5.1f}% {train['max_dd']:>6.2f}% | "
              f"Rs {test['pnl']:>9,.0f} {test['trades']:>7} {test['win_rate']:>5.1f}% {test['max_dd']:>6.2f}%")

    # --- Phase 6 comparison ---
    print(f"\n  {'='*95}")
    print(f"  Phase 6 results (SL=2.0xATR, TP=4.0xATR, CAPPED at 50 trades):")
    print(f"  {'-'*95}")
    phase6 = [
        ("LAURUSLABS", 10, 444.03, 50, 42.0, 4.00, -744.95, 50, "—", "—"),
        ("LTIM", 100, -5857.40, 50, 34.0, 7.61, -1412.24, 50, "—", "—"),
        ("ONGC", 196, -19669.15, 50, 16.0, 19.96, -14946.60, 50, "—", "—"),
    ]
    for name, rank, tp, tt, twr, tdd, trp, trt, *_ in phase6:
        print(f"  {name:<15} {rank:>5} | Rs {trp:>9,.0f} {trt:>7}    —      —   | Rs {tp:>9,.0f} {tt:>7} {twr:>5.1f}% {tdd:>6.2f}%")

    print()


if __name__ == "__main__":
    main()
