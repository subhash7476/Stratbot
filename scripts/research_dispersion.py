"""
Research Harness: Cross-Sectional Dispersion (CSAD/Residuals)
=============================================================
Walk-forward validation of 11:00 AM dispersion signals.
Tests structural edge of residual-based relative strength.

Protocol:
1. Universe: Top 50 NIFTY constituents (reusing fo_stocks).
2. Signal: 11:00 AM Residual Rank (Long Top 5 / Short Bottom 5).
3. Hold: 11:01 AM Open to 15:00 PM Close.
4. Costs: 0.04% Round-Trip.
5. Analysis: Conditioned by Regime (BullTrend / BearTrend / Choppy).
"""

import os
import sys
import logging
import pandas as pd
import duckdb
import numpy as np
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import List, Dict

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.analytics.dispersion import DispersionEngine, INDEX_SYMBOL
from core.database.manager import DatabaseManager

# Configuration
START_DATE = date(2023, 1, 1)
END_DATE = date(2026, 2, 13)
TOP_N = 5 
ROUND_TRIP_COST = 0.0004 
DB_PATH = ROOT / "data" / "market_data" / "nse" / "candles" / "1m"
REGIME_LOG = ROOT / "data" / "features" / "day_type" / "intraday_features_11am.csv"

def get_nifty_universe() -> List[str]:
    """Fetch symbols specifically from Nifty 50 CSV and resolve to keys."""
    csv_path = ROOT / "data" / "nifty-50-stock-list.csv"
    if not csv_path.exists():
        logger.error(f"Nifty 50 CSV not found at {csv_path}")
        return []
    
    try:
        df = pd.read_csv(csv_path)
        csv_symbols = [s.strip() for s in df['Symbol'].tolist()]
    except Exception as e:
        logger.error(f"Failed to read Nifty 50 CSV: {e}")
        return []
    
    db_man = DatabaseManager(ROOT / "data")
    resolved_keys = []
    try:
        with db_man.config_reader() as conn:
            # 1. Resolve via fo_stocks (primary for F&O universe)
            placeholders = ', '.join(['?'] * len(csv_symbols))
            query = f"SELECT instrument_key, trading_symbol FROM fo_stocks WHERE trading_symbol IN ({placeholders}) AND is_active=1"
            rows = conn.execute(query, csv_symbols).fetchall()
            
            fo_keys = [r[0] for r in rows]
            found_symbols = [r[1] for r in rows]
            
            # 2. Resolve missing via instrument_meta (fallback for standard equity keys)
            missing = set(csv_symbols) - set(found_symbols)
            meta_keys = []
            if missing:
                placeholders_missing = ', '.join(['?'] * len(missing))
                query_meta = f"""
                    SELECT instrument_key, trading_symbol 
                    FROM instrument_meta 
                    WHERE trading_symbol IN ({placeholders_missing}) 
                    AND exchange = 'NSE'
                    AND market_type = 'NSE_EQ'
                """
                rows_meta = conn.execute(query_meta, list(missing)).fetchall()
                meta_keys = [r[0] for r in rows_meta]
                found_meta = [r[1] for r in rows_meta]
                
                still_missing = missing - set(found_meta)
                if still_missing:
                    logger.warning(f"Could not resolve {len(still_missing)} symbols: {still_missing}")
            
            resolved_keys = list(set(fo_keys + meta_keys))
            
    except Exception as e:
        logger.error(f"Failed to resolve Nifty 50 symbols from DB: {e}")
        
    return sorted(resolved_keys)


def load_daily_returns_history(universe: List[str]) -> pd.DataFrame:
    """
    Constructs a DataFrame of Daily Returns for Beta Calculation.
    Reads close-to-close returns from daily DuckDB files.
    """
    logger.info("Building daily return history for Beta...")
    data = {}
    files = sorted(list(DB_PATH.glob("*.duckdb")))
    
    if not files:
        logger.error("No data files found in data/market_data/nse/candles/1m/")
        return pd.DataFrame()

    for f in files:
        d_str = f.stem
        try:
            conn = duckdb.connect(str(f), read_only=True)
            # Efficiently get last close for all symbols in one query
            df = conn.execute("""
                SELECT symbol, close 
                FROM candles 
                QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp DESC) = 1
            """).df()
            conn.close()
            
            if not df.empty:
                data[d_str] = df.set_index('symbol')['close'].to_dict()
        except Exception:
            continue
            
    price_df = pd.DataFrame.from_dict(data, orient='index')
    price_df.index = pd.to_datetime(price_df.index)
    price_df.sort_index(inplace=True)
    
    # Forward fill to handle occasional missing days/symbols before pct_change
    returns_df = price_df.ffill().pct_change()
    logger.info(f"Daily returns history built: {len(returns_df)} days.")
    return returns_df

def load_regime_data() -> pd.DataFrame:
    """Loads existing day-type regime labels from intraday features."""
    if not REGIME_LOG.exists():
        logger.warning(f"Regime log not found at {REGIME_LOG}")
        return pd.DataFrame()
    
    df = pd.read_csv(REGIME_LOG)
    df['date'] = pd.to_datetime(df['date']).dt.date
    
    # Check for cluster_id column
    if 'cluster_id' not in df.columns:
        logger.error(f"cluster_id not found in {REGIME_LOG}")
        return pd.DataFrame()
        
    df = df.sort_values('date')
    df = df.drop_duplicates(subset=['date'], keep='last')
    
    return df.set_index('date')

def run_research():
    logger.info("Initializing Dispersion Research Module...")
    
    universe = get_nifty_universe()
    if not universe:
        logger.error("Empty universe. Aborting.")
        return

    full_universe = list(set(universe + [INDEX_SYMBOL]))
    logger.info(f"Target Universe: {len(universe)} symbols.")
    
    engine = DispersionEngine(DB_PATH)
    hist_returns = load_daily_returns_history(full_universe)
    if hist_returns.empty:
        return
        
    regime_df = load_regime_data()
    
    results = []
    trading_days = [d for d in hist_returns.index.date if START_DATE <= d <= END_DATE]
    
    logger.info(f"Running walk-forward: {trading_days[0]} to {trading_days[-1]} ({len(trading_days)} days)")
    
    for i, day in enumerate(trading_days):
        day_str = str(day)
        
        # Strictly T-20 to T-1 for Beta (No leakage)
        current_idx = hist_returns.index.get_loc(pd.Timestamp(day))
        if current_idx < 20:
            continue
            
        lookback = hist_returns.iloc[current_idx-20:current_idx]
        
        # 1. Generate 11:00 AM Snapshot Signals
        signals = engine.get_snapshot_signals(
            day_str, 
            universe, 
            lookback_returns=lookback, 
            top_n=TOP_N
        )
        
        if not signals:
            continue
        
        # 2. Simulate 11:01 AM -> 15:00 PM Hold
        gross_ret = engine.simulate_hold(day_str, signals['longs'], signals['shorts'])
        net_pnl = gross_ret - ROUND_TRIP_COST
        
        # 3. Stratification Data
        regime = "Unknown"
        conf = 1.0
        if day in regime_df.index:
            row = regime_df.loc[day]
            regime_id = row['cluster_id']
            regime = {0: 'BearTrend', 1: 'BullTrend', 2: 'Choppy'}.get(regime_id, str(regime_id))

        results.append({
            'date': day,
            'pnl': net_pnl,
            'CSAD': signals['metrics']['CSAD'],
            'CSSD': signals['metrics']['CSSD'],
            'MarketReturn': signals['metrics']['MarketReturn'],
            'Breadth': signals['metrics']['Breadth'],
            'Regime': regime,
            'Conf': conf
        })
        
        if len(results) % 100 == 0:
            logger.info(f"Processed {day}: {len(results)} trades...")

    # Analysis
    res_df = pd.DataFrame(results)
    if res_df.empty:
        logger.error("No trades generated.")
        return

    # 4. Final Reporting
    print("\n" + "="*70)
    print("  CROSS-SECTIONAL DISPERSION RESEARCH REPORT (v1)")
    print("="*70)
    
    total_ret = res_df['pnl'].sum()
    win_rate = (res_df['pnl'] > 0).mean()
    avg_pnl = res_df['pnl'].mean()
    std_pnl = res_df['pnl'].std()
    sharpe = (avg_pnl / std_pnl) * (252**0.5) if std_pnl > 0 else 0
    
    print(f"  Total Trades:   {len(res_df)}")
    print(f"  Total PnL:      {total_ret*100:.2f}%")
    print(f"  Win Rate:       {win_rate:.1%}")
    print(f"  Avg PnL/Trade:  {avg_pnl*100:.4f}%")
    print(f"  Sharpe Ratio:   {sharpe:.2f}")
    
    print("\n  PERFORMANCE BY REGIME")
    print("  " + "-"*60)
    print(f"  {'Regime':<15} {'N':<5} {'WinRate':<10} {'AvgPnL':<12} {'Sharpe':<6}")
    
    for regime, grp in res_df.groupby('Regime'):
        wr = (grp['pnl'] > 0).mean()
        avg = grp['pnl'].mean()
        sh = (avg / grp['pnl'].std()) * (252**0.5) if len(grp) > 5 else 0
        print(f"  {regime:<15} {len(grp):<5} {wr:7.1%}   {avg*100:+10.4f}%   {sh:.2f}")

    # Subperiod Stability
    res_df['year'] = pd.to_datetime(res_df['date']).dt.year
    print("\n  SUBPERIOD STABILITY (YEARLY)")
    print("  " + "-"*60)
    for yr, grp in res_df.groupby('year'):
        avg = grp['pnl'].mean()
        print(f"  {yr}:  Avg PnL={avg*100:+.4f}% | WinRate={(grp['pnl']>0).mean():.1%}")

    print("\n  SIGNAL CORRELATION")
    print("  " + "-"*60)
    print(f"  Corr(CSAD, PnL):    {res_df['CSAD'].corr(res_df['pnl']):.3f}")
    print(f"  Corr(Breadth, PnL): {res_df['Breadth'].corr(res_df['pnl']):.3f}")
    
    # Save Artifacts
    out_dir = ROOT / "data" / "features"
    out_dir.mkdir(parents=True, exist_ok=True)
    res_df.to_csv(out_dir / "dispersion_results.csv", index=False)
    logger.info(f"Detailed logs saved to {out_dir / 'dispersion_results.csv'}")

if __name__ == "__main__":
    run_research()
