
import os
import sys
import logging
import pandas as pd
import json
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.database.manager import DatabaseManager
from core.strategies.regime.observer import RegimeObserver
from core.strategies.regime.classifier import HMMRegimeClassifier

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RegimeMapper")

def generate_regime_map():
    db_manager = DatabaseManager(Path("data"))
    
    # 1. Fetch Intermarket Data
    logger.info("Fetching daily intermarket data...")
    # Using existing query logic from scripts/fetch_intermarket_data.py implicitly (assuming data exists)
    # We need to construct the DataFrame expected by RegimeObserver
    
    # Observer expects a dataframe with daily closes for Nifty, BankNifty, VIX
    # Let's verify data exists first
    from core.database.queries import MarketDataQuery
    query = MarketDataQuery(db_manager)
    
    # Fetch 1d data for features
    start_date = datetime(2023, 1, 1)
    end_date = datetime(2026, 2, 1) # Get everything
    
    nifty = query.get_candles("NSE_INDEX|Nifty 50", "nse", "1d", start=start_date, end=end_date)
    banknifty = query.get_candles("NSE_INDEX|Nifty Bank", "nse", "1d", start=start_date, end=end_date)
    vix = query.get_candles("NSE_INDEX|India VIX", "nse", "1d", start=start_date, end=end_date)
    
    if nifty.empty or banknifty.empty or vix.empty:
        logger.error("Missing intermarket data. Please run scripts/fetch_intermarket_data.py first.")
        return

    # Prepare individual DFs
    # Ensure we keep all OHLC columns as Observer might need them (e.g. for Gap calculation)
    def prepare_df(df):
        df = df.set_index('timestamp')
        df = df[['open', 'high', 'low', 'close', 'volume']]
        df.index = pd.to_datetime(df.index)
        df.sort_index(inplace=True)
        return df

    nifty = prepare_df(nifty)
    banknifty = prepare_df(banknifty)
    vix = prepare_df(vix)

    # 2. Compute Features
    logger.info("Computing regime features...")
    observer = RegimeObserver()
    
    # observer.compute_features expects (nifty_df, banknifty_df, vix_df)
    # Each must have a 'close' column and DatetimeIndex
    features = observer.compute_features(nifty, banknifty, vix)
    
    # 3. Train HMM (Rolling Window approach to match backtest report)
    # Train on data UP TO Validation Period start (Jun 2025)
    train_end_date = datetime(2025, 5, 31).date()
    
    # Ensure features index is comparable (convert to date if it's datetime)
    if isinstance(features.index[0], datetime):
         features.index = features.index.date
         
    train_features = features[features.index < train_end_date]
    
    logger.info(f"Training HMM on {len(train_features)} days (Up to {train_end_date})...")
    
    classifier = HMMRegimeClassifier(n_states=3)
    classifier.fit(train_features)
    
    # 4. Predict Regime for Validation Period
    # We predict on the FULL dataset to get states for the test period
    # Note: In a true walk-forward, we'd retrain. Here we use a fixed model trained on history.
    
    # Use classify_all(features) which returns DF with 'regime' column
    states_df = classifier.classify_all(features)
    
    # Create Map: Date -> Regime Label
    regime_map = {}
    validation_start = datetime(2025, 6, 1)
    validation_end = datetime(2025, 12, 31)
    
    expansion_days = 0
    total_days = 0
    
    for dt, row in states_df.iterrows():
        # Ensure we have a datetime object for comparison
        if isinstance(dt, pd.Timestamp):
            current_dt = dt.to_pydatetime()
        elif hasattr(dt, 'date'): # datetime
             current_dt = dt
        else: # date
             from datetime import time
             current_dt = datetime.combine(dt, time.min)
        
        if validation_start <= current_dt <= validation_end:
            regime_label = row['regime'] 
            
            regime_map[current_dt.strftime('%Y-%m-%d')] = regime_label
            total_days += 1
            if regime_label == "EXPANSION":
                expansion_days += 1
                
    logger.info(f"Generated Regime Map for {total_days} days.")
    logger.info(f"Expansion Days: {expansion_days} ({expansion_days/total_days*100:.1f}%)")
    
    # Save to JSON
    output_path = "core/models/regime_map_validation.json"
    with open(output_path, "w") as f:
        json.dump(regime_map, f, indent=2)
        
    logger.info(f"Saved to {output_path}")

if __name__ == "__main__":
    generate_regime_map()
