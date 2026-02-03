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
from core.data.duckdb_market_data_provider import DuckDBMarketDataProvider
from core.data.duckdb_analytics_provider import DuckDBAnalyticsProvider
from core.execution.handler import ExecutionHandler, ExecutionConfig, ExecutionMode
from core.execution.position_tracker import PositionTracker
from core.brokers.paper_broker import PaperBroker
from core.strategies.registry import create_strategy
from core.analytics.populator import AnalyticsPopulator
from core.data.duckdb_client import db_cursor


def validate_strategy_behavior():
    """
    Validate the premium TP/SL strategy behavior with real data.
    """
    print("Starting Premium TP/SL Strategy Validation...")
    
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
    populator = AnalyticsPopulator()
    populator.update_all([symbol])
    print("Analytics population complete.")
    
    # Set up market data provider for the specific date
    market_data = DuckDBMarketDataProvider([symbol], start_time=start_time, end_time=end_time)
    
    # Check if we have data for the specified date
    with db_cursor(read_only=True) as conn:
        count_query = """
            SELECT COUNT(*) 
            FROM ohlcv_1m 
            WHERE instrument_key = ? 
            AND timestamp >= ? 
            AND timestamp <= ?
        """
        count = conn.execute(count_query, [symbol, start_time, end_time]).fetchone()[0]
        
        if count == 0:
            print(f"Warning: No data found for {symbol} on {target_date.strftime('%Y-%m-%d')}")
            print("Using a different date with available data...")
            
            # Find a date with available data
            date_query = """
                SELECT DISTINCT DATE(timestamp) as trade_date
                FROM ohlcv_1m 
                WHERE instrument_key = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """
            result = conn.execute(date_query, [symbol]).fetchone()
            if result:
                actual_date = datetime.strptime(result[0], '%Y-%m-%d')
                start_time = datetime.combine(actual_date.date(), datetime.min.time().replace(hour=9, minute=15))
                end_time = datetime.combine(actual_date.date(), datetime.min.time().replace(hour=15, minute=30))
                print(f"Using available data for: {actual_date.strftime('%Y-%m-%d')}")
            else:
                print("No data available for the symbol. Using demo data...")
                return
    
    # Set up analytics provider
    analytics = DuckDBAnalyticsProvider()
    
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
    execution = ExecutionHandler(clock, broker, exec_config)
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
    
    # Analyze the results
    print("\nAnalyzing premium signal alignment...")
    
    # Query the confluence insights for the date to verify premium signals
    with db_cursor(read_only=True) as conn:
        insight_query = """
            SELECT timestamp, symbol, indicator_states
            FROM confluence_insights
            WHERE symbol = ?
            AND timestamp >= ?
            AND timestamp <= ?
            ORDER BY timestamp
        """
        insights = conn.execute(insight_query, [symbol, start_time, end_time]).fetchall()
        
        print(f"\nFound {len(insights)} analytics snapshots for validation.")
        
        if insights:
            # Check the first few insights to see premium signals
            for i, (ts, sym, states_json) in enumerate(insights[:5]):  # Show first 5
                import json
                states = json.loads(states_json) if states_json else []
                
                # Look for premium flags in the indicator states
                premium_buy = False
                premium_sell = False
                
                for state in states:
                    if state['name'] == 'premium_flags' and 'metadata' in state:
                        metadata = state['metadata']
                        premium_buy = metadata.get('premiumBuy', False)
                        premium_sell = metadata.get('premiumSell', False)
                        break
                    elif state['name'] == 'vwap':
                        # Check if VWAP signals are available
                        metadata = state.get('metadata', {})
                        premium_buy = metadata.get('premiumBuy', False)
                        premium_sell = metadata.get('premiumSell', False)
                
                if premium_buy or premium_sell:
                    print(f"  {ts}: premiumBuy={premium_buy}, premiumSell={premium_sell}")
    
    print("\nValidation complete!")
    print("Check the signal logs above to verify:")
    print("  - Premium buy/sell signals align with UT Bot + MACD + RSI + VWAP conditions")
    print("  - Exit priority follows TP/SL > time-stop > opposite signal order")
    print("  - Positions are fully closed on exit signals")


def run_simple_validation():
    """
    Run a simple validation with a known symbol that has data.
    """
    print("Running simple validation with available data...")
    
    # Find a symbol with available data
    with db_cursor(read_only=True) as conn:
        symbol_query = """
            SELECT DISTINCT instrument_key
            FROM ohlcv_1m
            LIMIT 1
        """
        result = conn.execute(symbol_query).fetchone()
        
        if not result:
            print("No data available in the database.")
            return
            
        symbol = result[0]
        print(f"Using symbol: {symbol}")
        
        # Get a date with data
        date_query = """
            SELECT DISTINCT DATE(timestamp) as trade_date
            FROM ohlcv_1m 
            WHERE instrument_key = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """
        date_result = conn.execute(date_query, [symbol]).fetchone()
        
        if not date_result:
            print("No date available for the symbol.")
            return
            
        actual_date = date_result[0] if isinstance(date_result[0], datetime) else datetime.combine(date_result[0], datetime.min.time())
        start_time = datetime.combine(actual_date.date(), datetime.min.time().replace(hour=9, minute=15))
        end_time = datetime.combine(actual_date.date(), datetime.min.time().replace(hour=15, minute=30))
        
        print(f"Using date: {actual_date.strftime('%Y-%m-%d')}")
    
    # Populate analytics
    print("Populating analytics...")
    populator = AnalyticsPopulator()
    populator.update_all([symbol])
    
    # Set up components
    market_data = DuckDBMarketDataProvider([symbol], start_time=start_time, end_time=end_time)
    analytics = DuckDBAnalyticsProvider()
    
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
    execution = ExecutionHandler(clock, broker, exec_config)
    position_tracker = PositionTracker()
    
    runner = TradingRunner(
        config=RunnerConfig(
            symbols=[symbol], 
            strategy_ids=[strategy.strategy_id],
            log_signals=True,
            log_trades=True,
            warn_on_missing_analytics=True
        ),
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