import pandas as pd
from datetime import datetime
from typing import Optional, List
from core.events import OHLCVBar, SignalType
from core.strategies.pixityAIMetaStrategy import PixityAIMetaStrategy
from core.execution.pixityAI_risk_engine import PixityAIRiskEngine
from core.strategies.base import StrategyContext


def run_pixityAI_backtest(
    csv_path: str,
    symbols: Optional[List[str]] = None,
    use_fo_universe: bool = False,
):
    """
    Run PixityAI strategy backtest on historical data.

    Args:
        csv_path: Path to CSV file with OHLCV data.
        symbols: Optional list of symbols to filter (e.g., ['SBIN', 'RELIANCE']).
        use_fo_universe: If True, load all FO-eligible stocks from config DB.
    """
    df = pd.read_csv(csv_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    # Symbol filtering
    if use_fo_universe:
        from core.database.utils.symbol_utils import get_all_fo_symbols
        symbols = get_all_fo_symbols()
        print(f"Loaded {len(symbols)} FO-eligible symbols")

    if symbols:
        original_count = len(df['symbol'].unique())
        df = df[df['symbol'].isin(symbols)]
        filtered_count = len(df['symbol'].unique())
        print(f"Filtered from {original_count} to {filtered_count} symbols")

        if df.empty:
            print("Warning: No matching symbols found in data!")
            return pd.DataFrame()

    strategy = PixityAIMetaStrategy(config={"long_threshold": 0.5, "short_threshold": 0.5})
    risk_engine = PixityAIRiskEngine(risk_per_trade=1000)

    equity = 100000
    pnl = 0
    trades = []

    # Simple backtest loop
    for symbol, group in df.groupby('symbol'):
        group = group.sort_values('timestamp')
        context = StrategyContext(
            symbol=symbol,
            current_position=0,
            analytics_snapshot=None,
            market_regime=None,
            strategy_params={},
        )

        active_trade = None

        for i in range(len(group)):
            row = group.iloc[i]
            bar = OHLCVBar(
                symbol=symbol,
                timestamp=row['timestamp'],
                open=row['open'],
                high=row['high'],
                low=row['low'],
                close=row['close'],
                volume=row['volume'],
            )

            # 1. Check existing trade
            if active_trade:
                # Conservative rule: check SL then TP
                hit_sl = False
                hit_tp = False

                if active_trade['side'] == SignalType.BUY:
                    if bar.low <= active_trade['sl']:
                        hit_sl = True
                    elif bar.high >= active_trade['tp']:
                        hit_tp = True
                else:
                    if active_trade['side'] == SignalType.SELL:
                        if bar.high >= active_trade['sl']:
                            hit_sl = True
                        elif bar.low <= active_trade['tp']:
                            hit_tp = True

                if hit_sl or hit_tp:
                    exit_price = active_trade['sl'] if hit_sl else active_trade['tp']
                    trade_pnl = (exit_price - active_trade['entry']) * active_trade['qty']
                    if active_trade['side'] == SignalType.SELL:
                        trade_pnl = -trade_pnl

                    # Apply STT on SELL side
                    stt = risk_engine.calculate_stt(
                        SignalType.SELL,
                        exit_price if active_trade['side'] == SignalType.BUY else active_trade['entry'],
                        active_trade['qty'],
                    )

                    pnl += trade_pnl - stt
                    trades.append({
                        **active_trade,
                        "symbol": symbol,
                        "exit_price": exit_price,
                        "pnl": trade_pnl,
                        "stt": stt,
                    })
                    active_trade = None
                    context.current_position = 0
                continue

            # 2. Generate new signal
            signal = strategy.process_bar(bar, context)
            if signal and signal.signal_type in [SignalType.BUY, SignalType.SELL]:
                pos_info = risk_engine.calculate_position(signal, equity + pnl)
                if pos_info['quantity'] > 0:
                    active_trade = {
                        "side": signal.signal_type,
                        "entry": pos_info['entry'],
                        "qty": pos_info['quantity'],
                        "sl": pos_info['sl'],
                        "tp": pos_info['tp'],
                        "timestamp": bar.timestamp,
                    }
                    context.current_position = (
                        pos_info['quantity']
                        if signal.signal_type == SignalType.BUY
                        else -pos_info['quantity']
                    )

    print(f"Final PnL: {pnl:.2f}")
    print(f"Total Trades: {len(trades)}")
    return pd.DataFrame(trades)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='PixityAI Strategy Backtest Runner')
    parser.add_argument('--csv', help='Path to OHLCV CSV file')
    parser.add_argument(
        '--symbols',
        help='Comma-separated symbols to backtest (e.g., SBIN,RELIANCE,HDFC)',
    )
    parser.add_argument(
        '--fo-universe',
        action='store_true',
        help='Use all FO-eligible stocks from config database',
    )
    args = parser.parse_args()

    if args.csv:
        symbols_list = args.symbols.split(',') if args.symbols else None
        result = run_pixityAI_backtest(
            args.csv,
            symbols=symbols_list,
            use_fo_universe=args.fo_universe,
        )
        if not result.empty:
            print("\nTrades Summary:")
            print(result.to_string())
    else:
        print("PixityAI Backtest Runner is ready.")
        print("\nUsage:")
        print("  python scripts/pixityAI_backtest_runner.py --csv data/ohlcv.csv")
        print("  python scripts/pixityAI_backtest_runner.py --csv data/ohlcv.csv --symbols SBIN,RELIANCE")
        print("  python scripts/pixityAI_backtest_runner.py --csv data/ohlcv.csv --fo-universe")
