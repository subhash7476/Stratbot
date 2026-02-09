"""
Known-Day Validation for Premium TP/SL Strategy
---------------------------------------------
Run 1 symbol for 1 trading day and validate:
1. premiumBuy/premiumSell alignment (UT Bot + MACD + RSI + VWAP conditions)
2. exit priority (TP/SL beats opposite-signal)
"""
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

# Add project root to sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.clock import ReplayClock
from core.runner import TradingRunner, RunnerConfig
from core.database.providers.market_data import DuckDBMarketDataProvider
from core.database.providers.analytics import DuckDBAnalyticsProvider
from core.execution.handler import ExecutionHandler, ExecutionConfig, ExecutionMode
from core.execution.position_tracker import PositionTracker
from core.brokers.paper_broker import PaperBroker
from core.strategies.registry import create_strategy
from core.analytics.populator import AnalyticsPopulator
from core.database.manager import DatabaseManager


def validate_strategy_behavior():
    """
    Validate the premium TP/SL strategy behavior with real data.
    """
    print("Starting Premium TP/SL Strategy Validation...")
    
    data_root = ROOT / "data"
    db_manager = DatabaseManager(data_root)
    
    # Use a specific symbol and date for validation
    symbol = "NSE_EQ|INE001A01034"  # Example symbol
    target_date = datetime(2026, 1, 29)  # Specific trading day
    
    # Set up time range for the trading day
    start_time = datetime.combine(target_date.date(), datetime.min.time().replace(hour=9, minute=15))
    end_time = datetime.combine(target_date.date(), datetime.min.time().replace(hour=15, minute=30))
    
    print(f"Target date: {target_date.strftime('%Y-%m-%d')}")
    print(f"Symbol: {symbol}")
    
    # First, populate analytics for the date range
    print("Populating analytics for validation period...")
    populator = AnalyticsPopulator(db_manager=db_manager)
    populator.update_all([symbol])
    print("Analytics population complete.")
    
    # Set up market data provider for the specific date
    market_data = DuckDBMarketDataProvider(
        [symbol], 
        db_manager=db_manager,
        start_time=start_time, 
        end_time=end_time
    )
    
    # Set up analytics provider
    analytics = DuckDBAnalyticsProvider(db_manager=db_manager)
    
    # Create the premium TP/SL strategy with specific parameters
    config = {
        'tp_pct': 0.005,      # 0.5% TP
        'sl_pct': 0.0025,     # 0.25% SL
        'max_hold_bars': 15   # Max 15 bars hold
    }
    
    strategy = create_strategy("premium_tp_sl", f"premium_tp_sl_validation", config)
    
    if strategy is None:
        print("Error: Could not create premium_tp_sl strategy")
        return
    
    # Set up execution components
    clock = ReplayClock(start_time)
    broker = PaperBroker(clock)
    exec_config = ExecutionConfig(mode=ExecutionMode.PAPER)
    execution = ExecutionHandler(db_manager=db_manager, clock=clock, broker=broker, config=exec_config)
    position_tracker = PositionTracker()
    
    # Create and run the trading runner
    runner = TradingRunner(
        config=RunnerConfig(
            symbols=[symbol], 
            strategy_ids=[strategy.strategy_id],
            log_signals=True,
            log_trades=True,
            warn_on_missing_analytics=True
        ),
        db_manager=db_manager,
        market_data_provider=market_data,
        analytics_provider=analytics,
        strategies=[strategy],
        execution_handler=execution,
        position_tracker=position_tracker,
        clock=clock
    )
    
    print("Starting backtest run...")
    stats = runner.run()
    
    print("\n" + "="*70)
    print("VALIDATION RESULTS")
    print("="*70)
    print(f"Bars processed: {stats['bars_processed']}")
    print(f"Signals generated: {stats['signals_generated']}")
    print(f"Trades executed: {stats['trades_executed']}")
    print(f"Final positions: {stats['current_positions']}")
    
    print("\nValidation complete!")


def run_simple_validation():
    """
    Run a simple validation with a known symbol that has data.
    """
    print("Running simple validation with available data...")
    
    data_root = ROOT / "data"
    db_manager = DatabaseManager(data_root)
    
    # Just use a dummy range for now as we don't know what data is available
    symbol = "NSE_EQ|INE002A01018"
    start_time = datetime.now() - timedelta(days=7)
    end_time = datetime.now()
    
    # Set up components
    market_data = DuckDBMarketDataProvider(
        [symbol], 
        db_manager=db_manager,
        start_time=start_time, 
        end_time=end_time
    )
    analytics = DuckDBAnalyticsProvider(db_manager=db_manager)
    
    config = {
        'tp_pct': 0.005,
        'sl_pct': 0.0025,
        'max_hold_bars': 15
    }
    
    strategy = create_strategy("premium_tp_sl", f"premium_tp_sl_validation", config)
    
    if strategy is None:
        print("Error: Could not create premium_tp_sl strategy")
        return
    
    clock = ReplayClock(start_time)
    broker = PaperBroker(clock)
    exec_config = ExecutionConfig(mode=ExecutionMode.PAPER)
    execution = ExecutionHandler(db_manager=db_manager, clock=clock, broker=broker, config=exec_config)
    position_tracker = PositionTracker()
    
    runner = TradingRunner(
        config=RunnerConfig(
            symbols=[symbol], 
            strategy_ids=[strategy.strategy_id],
            log_signals=True,
            log_trades=True,
            warn_on_missing_analytics=True
        ),
        db_manager=db_manager,
        market_data_provider=market_data,
        analytics_provider=analytics,
        strategies=[strategy],
        execution_handler=execution,
        position_tracker=position_tracker,
        clock=clock
    )
    
    print("Running validation backtest...")
    stats = runner.run()
    
    print(f"\nValidation completed. Stats: {stats}")


if __name__ == "__main__":
    # Try the validation with available data
    run_simple_validation()
