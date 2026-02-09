from typing import List, Dict, Optional, Tuple
import pandas as pd
import numpy as np
from core.events import SignalEvent, SignalType

class PixityAILabeler:
    """
    Triple-Barrier Labeler for PixityAI events.
    Labels events as +1 (Profit), -1 (Loss), or 0 (Neutral/Time-stop).
    Includes MAE/MFE and Realized R metrics.
    """
    
    def __init__(self, sl_mult: float = 1.0, tp_mult: float = 2.0, time_stop_bars: int = 12):
        self.sl_mult = sl_mult
        self.tp_mult = tp_mult
        self.time_stop_bars = time_stop_bars

    def label_events(self, events: List[SignalEvent], full_df: pd.DataFrame) -> pd.DataFrame:
        results = []
        full_df = full_df.sort_values('timestamp').reset_index(drop=True)
        
        for event in events:
            # Mask for current symbol
            symbol_df = full_df[full_df['symbol'] == event.symbol]
            
            # Find entry point
            if event.metadata.get("entry_price_basis") == "next_open":
                # Get the bar immediately after the event timestamp
                post_event_df = symbol_df[symbol_df['timestamp'] > event.timestamp].head(1)
                if post_event_df.empty: continue
                entry_price = post_event_df.iloc[0]['open']
                start_ts = post_event_df.iloc[0]['timestamp']
            else:
                entry_price = event.metadata.get("entry_price_at_event")
                start_ts = event.timestamp

            # Get H future bars starting AFTER start_ts
            future_df = symbol_df[symbol_df['timestamp'] > start_ts].head(self.time_stop_bars)
            if future_df.empty: continue
                
            atr = event.metadata.get("atr_at_event")
            if not entry_price or not atr: continue
                
            outcome = self._get_barrier_outcome(
                entry_price, atr, event.signal_type, future_df
            )
            
            event_data = {
                **event.metadata,
                "timestamp": event.timestamp,
                "symbol": event.symbol,
                "signal_type": event.signal_type.value,
                "entry_price": entry_price,
                **outcome
            }
            results.append(event_data)
            
        return pd.DataFrame(results)

    def _get_barrier_outcome(self, entry: float, atr: float, side: SignalType, df: pd.DataFrame) -> Dict:
        sl_dist = self.sl_mult * atr
        tp_dist = self.tp_mult * atr
        
        tp_price = entry + tp_dist if side == SignalType.BUY else entry - tp_dist
        sl_price = entry - sl_dist if side == SignalType.BUY else entry + sl_dist
        
        label = 0
        exit_price = df.iloc[-1]['close']
        exit_time = df.iloc[-1]['timestamp']
        barrier_hit = "time"
        
        # Track MAE/MFE
        max_high = df['high'].max()
        min_low = df['low'].min()
        
        if side == SignalType.BUY:
            mfe = (max_high - entry) / sl_dist if sl_dist > 0 else 0
            mae = (entry - min_low) / sl_dist if sl_dist > 0 else 0
            
            for i in range(len(df)):
                row = df.iloc[i]
                # Conservative: SL first
                if row['low'] <= sl_price:
                    label, exit_price, exit_time, barrier_hit = -1, sl_price, row['timestamp'], "sl"
                    break
                if row['high'] >= tp_price:
                    label, exit_price, exit_time, barrier_hit = 1, tp_price, row['timestamp'], "tp"
                    break
        else:
            mfe = (entry - min_low) / sl_dist if sl_dist > 0 else 0
            mae = (max_high - entry) / sl_dist if sl_dist > 0 else 0
            
            for i in range(len(df)):
                row = df.iloc[i]
                if row['high'] >= sl_price:
                    label, exit_price, exit_time, barrier_hit = -1, sl_price, row['timestamp'], "sl"
                    break
                if row['low'] <= tp_price:
                    label, exit_price, exit_time, barrier_hit = 1, tp_price, row['timestamp'], "tp"
                    break

        realized_r = (exit_price - entry) / sl_dist if side == SignalType.BUY else (entry - exit_price) / sl_dist
        
        return {
            "label": label,
            "exit_price": exit_price,
            "exit_time": exit_time,
            "barrier_hit": barrier_hit,
            "realized_R": realized_r,
            "mae": mae,
            "mfe": mfe,
            "ts_end": df.iloc[-1]['timestamp']
        }
