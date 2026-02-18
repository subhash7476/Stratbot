
import os
import sys
import logging
import joblib
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import classification_report, precision_score

# Ensure project root is on path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.strategies.pixityAI_batch_events import batch_generate_events
from core.events import SignalType
from core.analytics.resampler import resample_ohlcv

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("GlobalTrainer")

def simulate_trade_outcome(df, event, time_stop_bars=12):
    """
    Simulate trade execution to determine label (Win=1, Loss=0).
    Logic matches PixityAIRiskEngine:
      SL = 1.0 * ATR
      TP = 2.0 * ATR
      Time Stop = 12 bars
    """
    entry_idx = df.index.get_indexer([event.timestamp], method='nearest')[0]
    
    # We need future data
    future_data = df.iloc[entry_idx+1 : entry_idx+1+time_stop_bars]
    
    if len(future_data) == 0:
        return 0, 0.0 # No data
        
    entry_price = event.metadata['entry_price_at_event']
    atr = event.metadata['atr_at_event']
    side = event.signal_type
    
    sl_dist = 1.0 * atr
    tp_dist = 2.0 * atr
    
    if side == SignalType.BUY:
        sl_price = entry_price - sl_dist
        tp_price = entry_price + tp_dist
        
        for _, bar in future_data.iterrows():
            # Check Low for SL
            if bar['low'] <= sl_price:
                return 0, -1.0 # Loss
            # Check High for TP
            if bar['high'] >= tp_price:
                return 1, 2.0 # Win (2R)
                
        # Time Stop - exit at Close of last bar
        exit_price = future_data.iloc[-1]['close']
        pnl = exit_price - entry_price
        return 1 if pnl > 0 else 0, pnl / sl_dist # Return R-multiple
        
    else: # SELL
        sl_price = entry_price + sl_dist
        tp_price = entry_price - tp_dist
        
        for _, bar in future_data.iterrows():
            if bar['high'] >= sl_price:
                return 0, -1.0
            if bar['low'] <= tp_price:
                return 1, 2.0
                
        exit_price = future_data.iloc[-1]['close']
        pnl = entry_price - exit_price
        return 1 if pnl > 0 else 0, pnl / sl_dist

def train_global_model():
    db = DatabaseManager(Path("data"))
    query = MarketDataQuery(db)
    
    # Config
    # Note: query.get_ohlcv might return 1m data if 15m is not directly available in parquet/duckdb.
    # We should fetch 1m and resample to be safe.
    
    # Training window
    train_start = datetime(2024, 10, 1) # Start slightly earlier for warmup
    train_end = datetime(2025, 5, 31)
    
    target_timeframe = "15m"
    model_save_path = "core/models/pixityAI_global_15m.joblib"
    
    # Get Symbols
    from core.backtest.symbol_scanner import SymbolScanner
    scanner = SymbolScanner(db)
    symbols = scanner.get_all_equity_symbols()
    
    logger.info(f"Starting Global Model Training on {len(symbols)} symbols...")
    logger.info(f"Period: {train_start.date()} to {train_end.date()} | Target TF: {target_timeframe}")
    
    all_training_data = []
    
    for idx, sym_info in enumerate(symbols):
        symbol = sym_info['instrument_key']
        trading_symbol = sym_info.get('trading_symbol', symbol)
        
        # Load 1m Data (it's the most reliable base source)
        df_1m = query.get_ohlcv(symbol, start_time=train_start, end_time=train_end, timeframe="1m")
        
        if df_1m.empty or len(df_1m) < 1000:
            # logger.debug(f"Skipping {trading_symbol}: Insufficient 1m data")
            continue
            
        df_1m['timestamp'] = pd.to_datetime(df_1m['timestamp'])
        df_1m.set_index('timestamp', inplace=True)
        
        # Resample to 15m
        try:
            df = resample_ohlcv(df_1m, target_timeframe)
        except Exception as e:
            logger.warning(f"Resampling failed for {trading_symbol}: {e}")
            continue

        if df.empty or len(df) < 200:
            continue

        # Ensure timestamp index for event generation
        if 'timestamp' in df.columns:
            df.set_index('timestamp', inplace=True)

        # Generate Events
        try:
            events = batch_generate_events(
                df,
                swing_period=5,
                reversion_k=2.0,
                time_stop_bars=12,
                bar_minutes=15
            )
        except Exception as e:
            logger.warning(f"Failed to generate events for {trading_symbol}: {e}")
            continue
            
        if not events:
            continue
            
        # Label Events
        for event in events:
            # Skip events too close to end of data
            if pd.Timestamp(event.timestamp) > df.index[-13]:
                continue
                
            label, r_multiple = simulate_trade_outcome(df, event)
            
            # Extract features
            features = {
                "vwap_dist": event.metadata.get("vwap_dist", 0),
                "ema_slope": event.metadata.get("ema_slope", 0),
                "atr_pct": event.metadata.get("atr_pct", 0),
                "adx": event.metadata.get("adx", 0),
                "hour": event.metadata.get("hour", 0),
                "minute": event.metadata.get("minute", 0),
                "vol_z": event.metadata.get("vol_z", 0),
                "label": label,
                "realized_R": r_multiple,
                "symbol": trading_symbol,
                "timestamp": event.timestamp
            }
            all_training_data.append(features)
            
        if (idx + 1) % 10 == 0:
            logger.info(f"Processed {idx+1}/{len(symbols)} symbols. Collected {len(all_training_data)} samples.")

    # Create DataFrame
    full_df = pd.DataFrame(all_training_data)
    logger.info(f"Training data collection complete. Total samples: {len(full_df)}")
    
    if len(full_df) < 100:
        logger.error("Insufficient data to train model.")
        return

    # Train Model
    features = ["vwap_dist", "ema_slope", "atr_pct", "adx", "hour", "minute", "vol_z"]
    X = full_df[features]
    y = full_df['label']
    
    logger.info("Training Random Forest Classifier...")
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=10, 
        min_samples_leaf=50, # Regularization to prevent overfitting
        random_state=42,
        n_jobs=-1,
        class_weight="balanced"  # Handle class imbalance if any
    )
    
    # Time Series Split Validation
    tscv = TimeSeriesSplit(n_splits=5)
    full_df.sort_values('timestamp', inplace=True)
    
    scores = []
    support = []
    for train_index, test_index in tscv.split(X):
        X_train, X_test = X.iloc[train_index], X.iloc[test_index]
        y_train, y_test = y.iloc[train_index], y.iloc[test_index]
        
        model.fit(X_train, y_train)
        preds = model.predict(X_test)
        
        # Check precision (we care about win rate of taken trades)
        score = precision_score(y_test, preds, zero_division=0)
        scores.append(score)
        support.append(len(y_test))
        
    logger.info(f"Cross-Validation Precision Scores: {scores}")
    logger.info(f"Average CV Precision: {np.mean(scores):.4f}")
    
    # Final Fit
    model.fit(X, y)
    
    # Save
    os.makedirs(os.path.dirname(model_save_path), exist_ok=True)
    joblib.dump(model, model_save_path)
    logger.info(f"Global Model saved to: {model_save_path}")
    
    # Feature Importance
    importances = pd.Series(model.feature_importances_, index=features).sort_values(ascending=False)
    logger.info("\nFeature Importances:")
    logger.info(importances.to_string())

if __name__ == "__main__":
    train_global_model()
