"""
Simple backtest runner for the Indian Market Squeeze strategy.
Usage:
    python tools\squeeze_backtest.py <instrument_key> --start 2025-11-01 --end 2025-12-31

It queries `ohlcv_resampled` via `core.database.get_db()` (same DB used by the app), builds 15m signals via `build_15m_signals`, then simulates each signal until SL/TP hit or data end.
Produces basic metrics: total trades, win rate, profit factor, expectancy, Sharpe (trade-level), max drawdown.

Notes:
- Intrabar resolution is not available; if both SL and TP occur within a single bar the script treats that bar conservatively as an SL hit.
- The script assumes entry at the `entry_price` returned by `build_15m_signals`.
"""

import argparse
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import math
import os
import sys

# Ensure project root is importable
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database import get_db
from core.strategies.indian_market_squeeze import build_15m_signals


def load_ohlcv(instrument_key: str, timeframe: str, start_ts: datetime, end_ts: datetime) -> pd.DataFrame:
    db = get_db()
    query = '''
    SELECT timestamp, open as Open, high as High, low as Low, close as Close, volume as Volume
    FROM ohlcv_resampled
    WHERE instrument_key = ? AND timeframe = ? AND timestamp >= ? AND timestamp <= ?
    ORDER BY timestamp
    '''
    params = [instrument_key, timeframe, start_ts.strftime('%Y-%m-%d %H:%M:%S'), end_ts.strftime('%Y-%m-%d %H:%M:%S')]
    df = db.con.execute(query, params).df()
    if df.empty:
        return pd.DataFrame()
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    return df


def simulate_trades(df_15m: pd.DataFrame, signals, verbose=False):
    trades = []
    idx = df_15m.index
    for sig in signals:
        entry_time = pd.to_datetime(sig.timestamp)
        if entry_time not in idx:
            # if timestamp not exactly in index, find nearest after
            possible = idx[idx >= entry_time]
            if possible.empty:
                continue
            entry_idx = df_15m.index.get_loc(possible[0])
        else:
            entry_idx = df_15m.index.get_loc(entry_time)

        entry_price = sig.entry_price
        sl = sig.sl_price
        tp = sig.tp_price
        side = sig.signal_type

        exit_price = None
        exit_time = None
        exit_type = None

        # walk forward from next bar
        for j in range(entry_idx + 1, len(df_15m)):
            h = df_15m['High'].iat[j]
            l = df_15m['Low'].iat[j]
            t = df_15m.index[j]

            if side == 'LONG':
                hit_tp = h >= tp
                hit_sl = l <= sl
                if hit_tp and hit_sl:
                    # conservative: assume SL hit first
                    exit_price = sl
                    exit_time = t
                    exit_type = 'SL'
                    break
                elif hit_sl:
                    exit_price = sl
                    exit_time = t
                    exit_type = 'SL'
                    break
                elif hit_tp:
                    exit_price = tp
                    exit_time = t
                    exit_type = 'TP'
                    break
            else:
                # SHORT
                hit_tp = l <= tp
                hit_sl = h >= sl
                if hit_tp and hit_sl:
                    exit_price = sl
                    exit_time = t
                    exit_type = 'SL'
                    break
                elif hit_sl:
                    exit_price = sl
                    exit_time = t
                    exit_type = 'SL'
                    break
                elif hit_tp:
                    exit_price = tp
                    exit_time = t
                    exit_type = 'TP'
                    break
        if exit_price is None:
            # exit at last close
            exit_time = df_15m.index[-1]
            exit_price = float(df_15m['Close'].iat[-1])
            exit_type = 'EOD'

        # compute return
        if side == 'LONG':
            ret = (exit_price - entry_price) / entry_price
        else:
            ret = (entry_price - exit_price) / entry_price

        trades.append({
            'entry_time': entry_time,
            'exit_time': exit_time,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'side': side,
            'exit_type': exit_type,
            'return': ret,
            'score': getattr(sig, 'score', 0.0),
        })

    return pd.DataFrame(trades)


def compute_metrics(trades_df: pd.DataFrame):
    if trades_df.empty:
        return {}
    returns = trades_df['return'].values
    total = len(returns)
    wins = (returns > 0).sum()
    losses = (returns <= 0).sum()
    win_rate = wins / total
    gross_profit = returns[returns > 0].sum() if (returns > 0).any() else 0.0
    gross_loss = -returns[returns <= 0].sum() if (returns <= 0).any() else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
    avg_win = returns[returns > 0].mean() if (returns > 0).any() else 0.0
    avg_loss = -returns[returns <= 0].mean() if (returns <= 0).any() else 0.0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

    # trade-level Sharpe (annualized-ish) -- sqrt(N) scaling
    mean_r = returns.mean()
    std_r = returns.std(ddof=1)
    sharpe = (mean_r / std_r * math.sqrt(total)) if std_r > 0 else float('inf')

    # equity curve
    eq = (1 + pd.Series(returns)).cumprod()
    peak = eq.cummax()
    drawdown = (eq / peak - 1)
    max_dd = drawdown.min()

    return {
        'total_trades': total,
        'wins': int(wins),
        'losses': int(losses),
        'win_rate': win_rate,
        'gross_profit': float(gross_profit),
        'gross_loss': float(gross_loss),
        'profit_factor': float(profit_factor),
        'avg_win': float(avg_win),
        'avg_loss': float(avg_loss),
        'expectancy': float(expectancy),
        'sharpe_trade_level': float(sharpe),
        'max_drawdown': float(max_dd),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('instrument_key')
    p.add_argument('--start', required=True)
    p.add_argument('--end', required=True)
    p.add_argument('--lookback_days', type=int, default=60)
    p.add_argument('--out', default=None)
    args = p.parse_args()

    start_ts = datetime.fromisoformat(args.start)
    end_ts = datetime.fromisoformat(args.end)

    print(f'Loading OHLCV for {args.instrument_key} from {start_ts} to {end_ts}...')
    df = load_ohlcv(args.instrument_key, '15minute', start_ts, end_ts)
    if df.empty:
        print('No data returned for period.')
        return

    print('Building signals...')
    signals = build_15m_signals(df, sl_mode='PCT')
    print(f'{len(signals)} signals found')

    trades = simulate_trades(df, signals)
    print(f'{len(trades)} simulated trades')

    metrics = compute_metrics(trades)
    print('Metrics:')
    for k, v in metrics.items():
        print(f'  {k}: {v}')

    if args.out:
        trades.to_csv(args.out, index=False)
        print('Trades saved to', args.out)


if __name__ == '__main__':
    main()
