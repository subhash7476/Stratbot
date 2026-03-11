"""
Cross-Sectional Dispersion Engine
=================================
Calculates 11:00 AM dispersion metrics and generates residual-based portfolio signals.
Designed for NIFTY 50 constituents.

Constraints:
1. Beta: Uses T-20 to T-1 daily data only (no same-day leakage).
2. Entry: 11:01 AM Open (calculated from 11:00 AM snapshot).
3. Exit: 15:00 PM Close.
4. Weighting: Equal-weighted legs, dollar-neutral.
"""

import numpy as np
import pandas as pd
import duckdb
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from datetime import datetime, time

logger = logging.getLogger(__name__)

# Constants
AM_SNAPSHOT_TIME = time(11, 0)  # Use data up to 11:00 close
ENTRY_TIME = time(11, 1)       # Enter at 11:01 open
EXIT_TIME = time(15, 0)        # Exit at 15:00 close
INDEX_SYMBOL = "NSE_INDEX|Nifty 50"

class DispersionEngine:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def load_session_data(self, date_str: str, universe: List[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Loads 1m data for the specific date for Universe + Index.
        Returns: (univ_df, index_df)
        """
        day_db = self.db_path / f"{date_str}.duckdb"
        if not day_db.exists():
            return pd.DataFrame(), pd.DataFrame()

        try:
            conn = duckdb.connect(str(day_db), read_only=True)
            
            # Load Index
            index_query = f"SELECT timestamp, open, close FROM candles WHERE symbol = '{INDEX_SYMBOL}' ORDER BY timestamp"
            index_df = conn.execute(index_query).df()
            
            # Load Universe (batch query)
            if not universe:
                conn.close()
                return pd.DataFrame(), index_df
                
            placeholders = ', '.join([f"'{s}'" for s in universe])
            univ_query = f"""
                SELECT symbol, timestamp, open, close 
                FROM candles 
                WHERE symbol IN ({placeholders}) 
                ORDER BY timestamp
            """
            univ_df = conn.execute(univ_query).df()
            conn.close()
            
            # Pre-process timestamps
            if not index_df.empty:
                index_df['timestamp'] = pd.to_datetime(index_df['timestamp'])
            if not univ_df.empty:
                univ_df['timestamp'] = pd.to_datetime(univ_df['timestamp'])
                
            return univ_df, index_df
            
        except Exception as e:
            logger.error(f"Error loading data for {date_str}: {e}")
            return pd.DataFrame(), pd.DataFrame()

    def compute_rolling_beta(self, stock_returns: pd.Series, market_returns: pd.Series, window: int = 20) -> float:
        """
        Computes rolling beta using available daily return history (T-20 to T-1).
        """
        combined = pd.concat([stock_returns, market_returns], axis=1, join='inner').dropna()
        if len(combined) < window:
            return 1.0 # Fallback
            
        window_data = combined.iloc[-window:]
        cov = window_data.iloc[:, 0].cov(window_data.iloc[:, 1])
        var = window_data.iloc[:, 1].var()
        
        return cov / var if var > 0 else 1.0

    def get_snapshot_signals(self, 
                             date_str: str, 
                             universe: List[str], 
                             lookback_returns: Optional[pd.DataFrame] = None, 
                             top_n: int = 5) -> Dict:
        """
        Generates signals at 11:00 AM using data up to 11:00 Close.
        
        lookback_returns: MUST NOT include current day (T-20 to T-1).
        """
        univ_df, index_df = self.load_session_data(date_str, universe)
        
        if univ_df.empty or index_df.empty:
            return {}

        snapshot_ts = datetime.strptime(f"{date_str} {AM_SNAPSHOT_TIME}", "%Y-%m-%d %H:%M:%S")
        
        def get_ret(df, symbol=None):
            sub = df[df['symbol'] == symbol] if symbol else df
            if sub.empty: return np.nan
            open_p = sub.iloc[0]['open']
            mask = sub['timestamp'] <= snapshot_ts
            if not mask.any(): return np.nan
            return (sub[mask].iloc[-1]['close'] / open_p) - 1.0

        idx_ret = get_ret(index_df)
        if pd.isna(idx_ret): return {}

        results = []
        betas = {sym: 1.0 for sym in universe}
        if lookback_returns is not None and not lookback_returns.empty:
             mkt_rets = lookback_returns[INDEX_SYMBOL]
             for sym in universe:
                 if sym in lookback_returns.columns:
                     betas[sym] = self.compute_rolling_beta(lookback_returns[sym], mkt_rets)

        for sym in universe:
            ret = get_ret(univ_df, sym)
            if pd.isna(ret): continue
            residual = ret - (betas.get(sym, 1.0) * idx_ret)
            results.append({'symbol': sym, 'return': ret, 'residual': residual})
            
        if not results: return {}
        res_df = pd.DataFrame(results)
        
        csad = (res_df['return'] - idx_ret).abs().mean()
        cssd = res_df['return'].std()
        
        longs = res_df.nlargest(top_n, 'residual')['symbol'].tolist()
        shorts = res_df.nsmallest(top_n, 'residual')['symbol'].tolist()
        
        return {
            'timestamp': snapshot_ts,
            'longs': longs,
            'shorts': shorts,
            'metrics': {
                'CSAD': csad,
                'CSSD': cssd,
                'MarketReturn': idx_ret,
                'Breadth': (res_df['return'] > idx_ret).mean()
            },
            'details': res_df
        }

    def simulate_hold(self, date_str: str, longs: List[str], shorts: List[str]) -> float:
        """
        Calculates PnL from 11:01 Open to 15:00 Close.
        """
        univ_df, _ = self.load_session_data(date_str, longs + shorts)
        if univ_df.empty: return 0.0
        
        entry_ts = datetime.strptime(f"{date_str} {ENTRY_TIME}", "%Y-%m-%d %H:%M:%S")
        exit_ts = datetime.strptime(f"{date_str} {EXIT_TIME}", "%Y-%m-%d %H:%M:%S")
        
        pnl = 0.0
        lw = 0.5 / len(longs) if longs else 0.0
        sw = 0.5 / len(shorts) if shorts else 0.0
        
        def get_prices(df, symbol):
            sub = df[df['symbol'] == symbol]
            if sub.empty: return None, None
            en_row = sub[sub['timestamp'] == entry_ts]
            ex_row = sub[sub['timestamp'] == exit_ts]
            if en_row.empty or ex_row.empty: return None, None
            return en_row.iloc[0]['open'], ex_row.iloc[0]['close']

        for sym in longs:
            en, ex = get_prices(univ_df, sym)
            if en and ex: pnl += ((ex / en) - 1.0) * lw
                
        for sym in shorts:
            en, ex = get_prices(univ_df, sym)
            if en and ex: pnl -= ((ex / en) - 1.0) * sw
                
        return pnl
