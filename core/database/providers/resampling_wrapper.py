from typing import List, Optional, Dict
from datetime import datetime
import pandas as pd
import logging
from core.database.providers.base import MarketDataProvider
from core.events import OHLCVBar
from core.analytics.resampler import resample_ohlcv
from core.database.utils import MarketHours
from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery

logger = logging.getLogger(__name__)

class ResamplingMarketDataProvider(MarketDataProvider):
    """
    Wraps a base MarketDataProvider to resample 1m bars into a target timeframe (e.g., 15m) on-the-fly.
    Maintains a buffer of 1m bars and emits a high-timeframe bar only when the period is complete.
    """
    def __init__(
        self, 
        base_provider: MarketDataProvider, 
        target_tf: str = "15m", 
        db_manager: Optional[DatabaseManager] = None,
        warmup_bars: int = 100
    ):
        super().__init__(base_provider.symbols)
        self.base = base_provider
        self.target_tf = target_tf
        self.db_manager = db_manager
        self.buffers: Dict[str, List[OHLCVBar]] = {}  # symbol -> list of 1m bars
        self.last_emitted: Dict[str, datetime] = {}   # symbol -> timestamp of last emitted HTF bar
        self.resampled_buffers: Dict[str, List[OHLCVBar]] = {} # symbol -> queue of ready HTF bars
        self.last_bars: Dict[str, OHLCVBar] = {} # symbol -> last emitted bar
        
        # Parse target timeframe to minutes
        if target_tf.endswith('m'):
            self.tf_minutes = int(target_tf[:-1])
        elif target_tf.endswith('h'):
            self.tf_minutes = int(target_tf[:-1]) * 60
        else:
            raise ValueError(f"Unsupported timeframe format: {target_tf}")

        # Run warmup if database is available
        if self.db_manager:
            self._warmup(warmup_bars)

    def _warmup(self, bars_htf: int):
        """Pre-load historical bars to warm up strategy indicators."""
        db_manager = self.db_manager
        if db_manager is None:
            return
            
        logger.info(f"Warming up ResamplingMarketDataProvider with {bars_htf} {self.target_tf} bars")
        query = MarketDataQuery(db_manager)
        
        # We need roughly bars_htf * tf_minutes of 1m bars
        # Adding 20% buffer for gaps/non-market hours if any
        limit_1m = int(bars_htf * self.tf_minutes * 1.2)
        
        for symbol in self.symbols:
            try:
                df_1m = query.get_ohlcv(symbol, timeframe="1m", limit=limit_1m)
                if df_1m.empty:
                    continue
                
                # Resample historical data
                df_htf = resample_ohlcv(df_1m, self.target_tf)
                
                # Take the last 'bars_htf' bars
                df_htf = df_htf.tail(bars_htf)
                
                # Buffer them
                ready_bars = []
                for _, row in df_htf.iterrows():
                    ts = row['timestamp']
                    if not isinstance(ts, datetime):
                        ts = pd.to_datetime(ts).to_pydatetime()
                    
                    bar = OHLCVBar(
                        symbol=str(symbol),
                        timestamp=ts,
                        open=float(row['open']),
                        high=float(row['high']),
                        low=float(row['low']),
                        close=float(row['close']),
                        volume=float(row['volume'])
                    )
                    ready_bars.append(bar)
                    self.last_emitted[symbol] = bar.timestamp
                    self.last_bars[symbol] = bar
                
                if ready_bars:
                    self.resampled_buffers[symbol] = ready_bars
                    logger.info(f"Warmed up {symbol}: {len(ready_bars)} bars loaded")
            except Exception as e:
                logger.error(f"Warmup failed for {symbol}: {e}")


    def get_next_bar(self, symbol: str) -> Optional[OHLCVBar]:
        """
        Returns the next available target_tf bar for the symbol.
        If a resampled bar is ready, returns it.
        Otherwise, polls the base provider for 1m bars and accumulates them until a period is complete.
        """
        # 1. Check if we have a resampled bar ready
        if self.resampled_buffers.get(symbol):
            return self.resampled_buffers[symbol].pop(0)

        # 2. Poll base provider for 1m bars
        while True:
            bar_1m = self.base.get_next_bar(symbol)
            if not bar_1m:
                return None  # No more 1m bars available right now
            
            # Initialize buffer if needed
            if symbol not in self.buffers:
                self.buffers[symbol] = []
                
            self.buffers[symbol].append(bar_1m)

            # 3. Check if we can resample (have enough bars for complete period)
            if self._can_resample(symbol):
                bars_15m = self._resample(symbol)
                
                # Filter out already emitted bars
                new_bars = []
                last_ts = self.last_emitted.get(symbol)
                
                for bar in bars_15m:
                    if last_ts is None or bar.timestamp > last_ts:
                        new_bars.append(bar)
                        self.last_emitted[symbol] = bar.timestamp
                
                if new_bars:
                    self.resampled_buffers[symbol] = new_bars
                    return self.resampled_buffers[symbol].pop(0)
    
    def _can_resample(self, symbol: str) -> bool:
        """
        Checks if the buffer contains a complete period.
        Logic:
        - Get the latest 1m bar's timestamp.
        - Determine the expected end time of the current 15m candle.
        - If we have crossed into a NEW period, the PREVIOUS period is complete.
        
        However, simpler logic for live streaming:
        - If the latest 1m bar completes a 15m block (e.g. 9:29, 9:44), we might be ready.
        - BUT data might arrive out of order or with gaps.
        - SAFEST approach: Resample continuously, but only emit a bar if its timestamp is strictly greater than last_emitted
          AND we are sure the period is "done" (e.g. we see a bar from the NEXT period, or we rely on wall clock).
          
        For this implementation, we will use the "timestamp boundary" check:
        - If the newest 1m bar belongs to a NEW 15m bucket compared to the previous 1m bar, 
          then the previous 15m bucket is definitely closed.
        """
        if len(self.buffers[symbol]) < 2:
            return False
            
        # Get last two bars
        last_bar = self.buffers[symbol][-1]
        prev_bar = self.buffers[symbol][-2]
        
        # Check if they belong to different target_tf buckets
        last_bucket = self._get_interval_start(last_bar.timestamp)
        prev_bucket = self._get_interval_start(prev_bar.timestamp)
        
        return last_bucket > prev_bucket

    def _get_interval_start(self, ts: datetime) -> datetime:
        """Aligns timestamp to the start of the timeframe interval (e.g. 9:15, 9:30)."""
        # Market open is 9:15. We need to align relative to that or just standard hour boundaries?
        # The resampler uses offset='15min', which aligns to hour boundaries (9:00, 9:15, 9:30).
        # So standard floor division works.
        minutes_since_midnight = ts.hour * 60 + ts.minute
        open_minutes = 9 * 60 + 15
        
        # If before market open, align to standard hours
        if minutes_since_midnight < open_minutes:
             bucket_start_min = (minutes_since_midnight // self.tf_minutes) * self.tf_minutes
        else:
            # Align relative to 9:15
            # (timestamp - 9:15) // 15 * 15 + 9:15
            elapsed = minutes_since_midnight - open_minutes
            bucket_start_min = open_minutes + (elapsed // self.tf_minutes) * self.tf_minutes
            
        hour = bucket_start_min // 60
        minute = bucket_start_min % 60
        return ts.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _resample(self, symbol: str) -> List[OHLCVBar]:
        """
        Converts buffered 1m bars to target_tf bars.
        Leaves the LATEST (incomplete) bucket in the buffer for next time.
        """
        # Convert buffer to DataFrame
        bars = self.buffers[symbol]
        data = [
            {
                'timestamp': b.timestamp,
                'open': b.open,
                'high': b.high,
                'low': b.low,
                'close': b.close,
                'volume': b.volume,
                'symbol': b.symbol
            }
            for b in bars
        ]
        df_1m = pd.DataFrame(data)
        
        # Use existing robust resampler
        df_resampled = resample_ohlcv(df_1m, self.target_tf)
        
        # Convert back to OHLCVBar objects
        results = []
        for _, row in df_resampled.iterrows():
            # Robustly convert timestamp to datetime
            ts = row['timestamp']
            if not isinstance(ts, datetime):
                ts = pd.Timestamp(ts).to_pydatetime()
                
            bar = OHLCVBar(
                symbol=str(row['symbol']),
                timestamp=ts,
                open=float(row['open']),
                high=float(row['high']),
                low=float(row['low']),
                close=float(row['close']),
                volume=float(row['volume'])
            )
            results.append(bar)
            
        # Optimization: Clear old buffer data
        # We only need to keep bars that belong to the current (incomplete) bucket
        # or the very last emitted bucket if we want to be safe.
        # Check the last bar in buffer
        if bars:
            last_bar_ts = bars[-1].timestamp
            current_bucket_start = self._get_interval_start(last_bar_ts)
            
            # Filter out the incomplete latest bar from results
            results = [r for r in results if r.timestamp < current_bucket_start]
            
            # Update last_bars with the latest bar that survived filtering
            if results:
                self.last_bars[symbol] = results[-1]
            
            # Keep only bars >= current_bucket_start in buffer
            self.buffers[symbol] = [b for b in bars if b.timestamp >= current_bucket_start]
            
        return results

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        """Returns the last successfully emitted target_tf bar."""
        return self.last_bars.get(symbol)

    def is_data_available(self, symbol: str) -> bool:
        return self.base.is_data_available(symbol) or bool(self.resampled_buffers.get(symbol))

    def reset(self, symbol: str):
        if symbol in self.buffers:
            self.buffers[symbol].clear()
        if symbol in self.resampled_buffers:
            self.resampled_buffers[symbol].clear()
        if symbol in self.last_emitted:
            del self.last_emitted[symbol]
        if hasattr(self, "last_bars") and symbol in self.last_bars:
            del self.last_bars[symbol]
        self.base.reset(symbol)

    def get_progress(self, symbol: str) -> tuple:
        return self.base.get_progress(symbol)
