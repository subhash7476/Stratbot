# core/shared_state.py
"""
Centralized state management for Trading Bot Pro
Manages data flow between pages 9-13:
  Page 9: Regime Analyzer → shortlisted_stocks.csv
  Page 10: Trade Zone Validator → trade_zone_validated.csv
  Page 11: Signal Generator → live_signals.csv
  Page 12: Paper Trading → paper_trades.csv
  Page 13: Live Trading → live_trades.csv
"""

import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List
import json

# ============================================================================
# DIRECTORY SETUP
# ============================================================================
DATA_DIR = Path("data")
STATE_DIR = DATA_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================================
# FILE PATHS
# ============================================================================
# Page 9: Regime Analyzer outputs
SHORTLISTED_STOCKS_CSV = STATE_DIR / "shortlisted_stocks.csv"
REGIME_ANALYSIS_JSON = STATE_DIR / "regime_analysis.json"

# Page 10: Trade Zone Validator outputs
TRADE_ZONE_CSV = STATE_DIR / "trade_zone_validated.csv"
VALIDATION_METADATA_JSON = STATE_DIR / "validation_metadata.json"

# Page 11: Signal Generator outputs
SIGNALS_CSV = STATE_DIR / "live_signals.csv"
SIGNAL_METADATA_JSON = STATE_DIR / "signal_metadata.json"

# Page 12: Paper Trading outputs
PAPER_TRADES_CSV = STATE_DIR / "paper_trades.csv"
PAPER_POSITIONS_CSV = STATE_DIR / "paper_positions.csv"

# Page 13: Live Trading outputs
LIVE_TRADES_CSV = STATE_DIR / "live_trades.csv"
LIVE_POSITIONS_CSV = STATE_DIR / "live_positions.csv"

# Cached live data
LIVE_DATA_CACHE = STATE_DIR / "live_data_cache.parquet"
LAST_UPDATE_JSON = STATE_DIR / "last_update.json"


# ============================================================================
# PAGE 9: REGIME ANALYZER
# ============================================================================

def save_shortlisted_stocks(df: pd.DataFrame, metadata: Optional[Dict] = None):
    """
    Save stocks shortlisted by Page 9 Regime Analyzer
    
    Expected DataFrame columns:
    - Symbol: Stock symbol
    - Regime: Current regime (Trending/Ranging/Volatile)
    - ER: Efficiency Ratio
    - Z_Score: Z-Score
    - Strength: Trend strength score
    - Confidence: Analysis confidence (0-100)
    - Recommended_Strategy: Best strategy for this regime
    - Last_Price: Current price
    - ATR: Average True Range
    - Volume_Status: Volume analysis
    """
    # Add timestamp
    df['Shortlisted_At'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Save to CSV
    df.to_csv(SHORTLISTED_STOCKS_CSV, index=False)
    
    # Save metadata
    if metadata is None:
        metadata = {}
    
    metadata.update({
        'total_stocks_analyzed': len(df),
        'timestamp': datetime.now().isoformat(),
        'source': 'Page_9_Regime_Analyzer'
    })
    
    with open(REGIME_ANALYSIS_JSON, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    return SHORTLISTED_STOCKS_CSV


def load_shortlisted_stocks() -> pd.DataFrame:
    """Load stocks shortlisted by Regime Analyzer"""
    if SHORTLISTED_STOCKS_CSV.exists():
        df = pd.read_csv(SHORTLISTED_STOCKS_CSV)
        return df
    return pd.DataFrame(columns=[
        'Symbol', 'Regime', 'ER', 'Z_Score', 'Strength', 
        'Confidence', 'Recommended_Strategy', 'Last_Price', 
        'ATR', 'Volume_Status', 'Shortlisted_At'
    ])


def get_regime_analysis_metadata() -> Dict:
    """Get metadata from last regime analysis"""
    if REGIME_ANALYSIS_JSON.exists():
        with open(REGIME_ANALYSIS_JSON, 'r') as f:
            return json.load(f)
    return {}


# ============================================================================
# PAGE 10: TRADE ZONE VALIDATOR
# ============================================================================

def save_trade_zone(df: pd.DataFrame, metadata: Optional[Dict] = None):
    """
    Save validated trade zones from Page 10
    
    Expected DataFrame columns:
    - Symbol: Stock symbol (from Page 9)
    - Regime: Confirmed regime
    - Entry_Zone_Low: Lower bound of entry zone
    - Entry_Zone_High: Upper bound of entry zone
    - Current_Price: Current market price
    - Stop_Loss: Stop loss level
    - Target_1: First target
    - Target_2: Second target
    - Risk_Reward: Risk-reward ratio
    - Position_Size: Recommended position size
    - Validation_Score: Validation confidence (0-100)
    - Validation_Criteria: List of passed criteria
    - Ready_To_Trade: Boolean flag
    """
    # Add timestamp
    df['Validated_At'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Save to CSV
    df.to_csv(TRADE_ZONE_CSV, index=False)
    
    # Save metadata
    if metadata is None:
        metadata = {}
    
    metadata.update({
        'total_validated': len(df),
        'ready_to_trade': len(df[df.get('Ready_To_Trade', False) == True]),
        'timestamp': datetime.now().isoformat(),
        'source': 'Page_10_Trade_Zone_Validator'
    })
    
    with open(VALIDATION_METADATA_JSON, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    return TRADE_ZONE_CSV


def load_trade_zone() -> pd.DataFrame:
    """Load validated trade zones"""
    if TRADE_ZONE_CSV.exists():
        df = pd.read_csv(TRADE_ZONE_CSV)
        return df
    return pd.DataFrame(columns=[
        'Symbol', 'Regime', 'Entry_Zone_Low', 'Entry_Zone_High',
        'Current_Price', 'Stop_Loss', 'Target_1', 'Target_2',
        'Risk_Reward', 'Position_Size', 'Validation_Score',
        'Validation_Criteria', 'Ready_To_Trade', 'Validated_At'
    ])


def get_ready_to_trade_stocks() -> pd.DataFrame:
    """Get only stocks that are ready to trade"""
    df = load_trade_zone()
    if not df.empty and 'Ready_To_Trade' in df.columns:
        return df[df['Ready_To_Trade'] == True].copy()
    return pd.DataFrame()


# ============================================================================
# PAGE 11: SIGNAL GENERATOR
# ============================================================================

def log_signal(
    symbol: str,
    direction: str,
    entry_price: float,
    sl: float,
    tp: float,
    timestamp: Optional[str] = None,
    strategy: Optional[str] = None,
    confidence: Optional[float] = None,
    metadata: Optional[Dict] = None
):
    """
    Log a new trading signal from Page 11
    
    Args:
        symbol: Stock symbol (from validated trade zone)
        direction: 'Long' or 'Short'
        entry_price: Entry price
        sl: Stop loss price
        tp: Take profit price
        timestamp: Signal timestamp (auto if None)
        strategy: Strategy name that generated signal
        confidence: Signal confidence (0-100)
        metadata: Additional signal metadata
    """
    df = load_signals()
    
    if timestamp is None:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    new_row = pd.DataFrame({
        'Symbol': [symbol],
        'Direction': [direction],
        'Entry_Price': [entry_price],
        'SL': [sl],
        'TP': [tp],
        'Risk_Reward': [(tp - entry_price) / (entry_price - sl) if direction == 'Long' 
                        else (entry_price - tp) / (sl - entry_price)],
        'Strategy': [strategy if strategy else 'Unknown'],
        'Confidence': [confidence if confidence else 50.0],
        'Timestamp': [timestamp],
        'Status': ['Open'],
        'Source': ['Page_11_Signal_Generator']
    })
    
    df = pd.concat([df, new_row], ignore_index=True)
    df.to_csv(SIGNALS_CSV, index=False)
    
    # Update metadata
    meta = {
        'last_signal_time': timestamp,
        'total_signals': len(df),
        'open_signals': len(df[df['Status'] == 'Open'])
    }
    
    with open(SIGNAL_METADATA_JSON, 'w') as f:
        json.dump(meta, f, indent=2)
    
    return SIGNALS_CSV


def load_signals() -> pd.DataFrame:
    """Load all trading signals"""
    if SIGNALS_CSV.exists():
        df = pd.read_csv(SIGNALS_CSV)
        return df
    return pd.DataFrame(columns=[
        'Symbol', 'Direction', 'Entry_Price', 'SL', 'TP',
        'Risk_Reward', 'Strategy', 'Confidence', 'Timestamp',
        'Status', 'Source'
    ])


def get_open_signals() -> pd.DataFrame:
    """Get only open (active) signals"""
    df = load_signals()
    if not df.empty and 'Status' in df.columns:
        return df[df['Status'] == 'Open'].copy()
    return pd.DataFrame()


def update_signal_status(symbol: str, timestamp: str, new_status: str):
    """Update status of a specific signal"""
    df = load_signals()
    mask = (df['Symbol'] == symbol) & (df['Timestamp'] == timestamp)
    df.loc[mask, 'Status'] = new_status
    df.to_csv(SIGNALS_CSV, index=False)


# ============================================================================
# PAGE 12: PAPER TRADING
# ============================================================================

def log_paper_trade(trade_details: dict):
    """
    Log a paper trade execution from Page 12
    
    Expected keys in trade_details:
    - symbol: Stock symbol
    - option_type: 'CE' or 'PE'
    - strike: Strike price
    - expiry: Expiry date
    - entry_premium: Entry premium paid
    - tp_premium: Take profit premium target
    - sl_premium: Stop loss premium
    - quantity: Number of lots
    - lot_size: Lot size for the symbol
    - timestamp: Entry timestamp
    - signal_source: Reference to signal that triggered this
    """
    df = load_paper_trades()
    
    # Ensure required fields
    required_fields = ['symbol', 'option_type', 'strike', 'entry_premium', 
                       'tp_premium', 'sl_premium', 'timestamp']
    for field in required_fields:
        if field not in trade_details:
            trade_details[field] = None
    
    # Add default fields
    trade_details.setdefault('status', 'Open')
    trade_details.setdefault('exit_premium', None)
    trade_details.setdefault('pnl', 0.0)
    trade_details.setdefault('quantity', 1)
    trade_details.setdefault('lot_size', 1)
    
    new_row = pd.DataFrame([trade_details])
    df = pd.concat([df, new_row], ignore_index=True)
    df.to_csv(PAPER_TRADES_CSV, index=False)
    
    return PAPER_TRADES_CSV


def load_paper_trades() -> pd.DataFrame:
    """Load all paper trades"""
    if PAPER_TRADES_CSV.exists():
        df = pd.read_csv(PAPER_TRADES_CSV)
        return df
    return pd.DataFrame(columns=[
        'symbol', 'option_type', 'strike', 'expiry', 'entry_premium',
        'tp_premium', 'sl_premium', 'quantity', 'lot_size', 'timestamp',
        'exit_premium', 'pnl', 'status', 'signal_source'
    ])


def get_paper_positions() -> pd.DataFrame:
    """Get current open paper positions"""
    df = load_paper_trades()
    if not df.empty and 'status' in df.columns:
        return df[df['status'] == 'Open'].copy()
    return pd.DataFrame()


def update_paper_trade(symbol: str, timestamp: str, exit_premium: float, pnl: float):
    """Update paper trade with exit details"""
    df = load_paper_trades()
    mask = (df['symbol'] == symbol) & (df['timestamp'] == timestamp)
    df.loc[mask, 'exit_premium'] = exit_premium
    df.loc[mask, 'pnl'] = pnl
    df.loc[mask, 'status'] = 'Closed'
    df.to_csv(PAPER_TRADES_CSV, index=False)


# ============================================================================
# PAGE 13: LIVE TRADING & DATA CACHE
# ============================================================================

def save_live_data_cache(df: pd.DataFrame, symbols: List[str]):
    """
    Cache live market data for Page 13
    
    Args:
        df: DataFrame with live OHLCV data
        symbols: List of symbols in the cache
    """
    df.to_parquet(LIVE_DATA_CACHE, index=False)
    
    metadata = {
        'symbols': symbols,
        'last_update': datetime.now().isoformat(),
        'row_count': len(df)
    }
    
    with open(LAST_UPDATE_JSON, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    return LIVE_DATA_CACHE


def load_live_data_cache() -> Optional[pd.DataFrame]:
    """Load cached live data"""
    if LIVE_DATA_CACHE.exists():
        return pd.read_parquet(LIVE_DATA_CACHE)
    return None


def get_live_data_metadata() -> Dict:
    """Get metadata about cached live data"""
    if LAST_UPDATE_JSON.exists():
        with open(LAST_UPDATE_JSON, 'r') as f:
            return json.load(f)
    return {}


def log_live_trade(trade_details: dict):
    """
    Log a live trade execution
    Similar to paper trade but with actual broker order IDs
    """
    df = load_live_trades()
    
    trade_details.setdefault('status', 'Open')
    trade_details.setdefault('order_id', None)
    trade_details.setdefault('broker_status', None)
    
    new_row = pd.DataFrame([trade_details])
    df = pd.concat([df, new_row], ignore_index=True)
    df.to_csv(LIVE_TRADES_CSV, index=False)
    
    return LIVE_TRADES_CSV


def load_live_trades() -> pd.DataFrame:
    """Load all live trades"""
    if LIVE_TRADES_CSV.exists():
        df = pd.read_csv(LIVE_TRADES_CSV)
        return df
    return pd.DataFrame(columns=[
        'symbol', 'option_type', 'strike', 'expiry', 'entry_premium',
        'tp_premium', 'sl_premium', 'quantity', 'lot_size', 'timestamp',
        'exit_premium', 'pnl', 'status', 'order_id', 'broker_status'
    ])


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_data_flow_status() -> Dict:
    """
    Get status of entire data flow from Page 9 to Page 13
    Useful for debugging and monitoring
    """
    status = {
        'page_9_shortlisted': {
            'exists': SHORTLISTED_STOCKS_CSV.exists(),
            'count': len(load_shortlisted_stocks()) if SHORTLISTED_STOCKS_CSV.exists() else 0,
            'last_update': None
        },
        'page_10_validated': {
            'exists': TRADE_ZONE_CSV.exists(),
            'count': len(load_trade_zone()) if TRADE_ZONE_CSV.exists() else 0,
            'ready_to_trade': len(get_ready_to_trade_stocks())
        },
        'page_11_signals': {
            'exists': SIGNALS_CSV.exists(),
            'total': len(load_signals()) if SIGNALS_CSV.exists() else 0,
            'open': len(get_open_signals())
        },
        'page_12_paper_trades': {
            'exists': PAPER_TRADES_CSV.exists(),
            'total': len(load_paper_trades()) if PAPER_TRADES_CSV.exists() else 0,
            'open_positions': len(get_paper_positions())
        },
        'page_13_live_cache': {
            'exists': LIVE_DATA_CACHE.exists(),
            'metadata': get_live_data_metadata()
        }
    }
    
    # Get last update times
    if REGIME_ANALYSIS_JSON.exists():
        meta = get_regime_analysis_metadata()
        status['page_9_shortlisted']['last_update'] = meta.get('timestamp')
    
    return status


def clear_all_state():
    """Clear all state files (use with caution!)"""
    import shutil
    if STATE_DIR.exists():
        shutil.rmtree(STATE_DIR)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def export_pipeline_state(output_dir: Path):
    """Export all state files to a directory for backup/analysis"""
    import shutil
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for file_path in STATE_DIR.glob("*"):
        if file_path.is_file():
            shutil.copy(file_path, output_dir / file_path.name)
    
    return output_dir


# ============================================================================
# INSTRUMENT METADATA (For Page 13 - lot sizes, etc.)
# ============================================================================

def get_lot_size(symbol: str) -> int:
    """
    Get lot size for a symbol
    This should integrate with your instruments data
    """
    # TODO: Integrate with your actual instruments database
    # For now, return common lot sizes
    lot_sizes = {
        'TCS': 150,
        'INFY': 300,
        'RELIANCE': 250,
        'HDFCBANK': 550,
        'ICICIBANK': 1375,
        'SBIN': 1500,
        'NIFTY': 25,
        'BANKNIFTY': 15,
    }
    return lot_sizes.get(symbol, 1)


def get_instrument_info(symbol: str) -> Dict:
    """
    Get comprehensive instrument information
    This should integrate with Page 1 instrument download
    """
    # TODO: Load from actual instruments database
    return {
        'symbol': symbol,
        'lot_size': get_lot_size(symbol),
        'exchange': 'NSE',
        'segment': 'NSE_EQ'
    }


if __name__ == "__main__":
    # Test/demo code
    print("Trading Bot Pro - Shared State Management")
    print("=" * 60)
    
    status = get_data_flow_status()
    
    print("\n📊 Data Flow Status:")
    print(json.dumps(status, indent=2))
    
    print("\n✅ Shared state module loaded successfully!")
    print(f"📁 State directory: {STATE_DIR}")