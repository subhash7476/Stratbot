"""
Signal Generator - Converts Filter Results to Actionable Trade Signals
Outputs clear entry, SL, targets following your rule engine
"""

from datetime import datetime
from typing import Dict
import uuid

class SignalGenerator:
    """
    Generates "₹500 PROBABLE - EXECUTE" signals
    """
    
    def __init__(self):
        # Exit rules from your screenshots
        self.sl_pct = 0.04          # -4% stop loss
        self.target1_pct = 0.05     # +5% book 50%
        self.target2_pct = 0.08     # +8% full exit
        self.time_sl_seconds = 90   # 90 seconds without movement
    
    def generate(self, symbol: str, stock_data: Dict, 
                 option_data: Dict, filter_metrics: Dict) -> Dict:
        """
        Generate complete trade signal
        
        Args:
            symbol: Stock symbol
            stock_data: Stock market data
            option_data: Option chain data
            filter_metrics: Metrics from all 5 filters
        
        Returns:
            Complete signal with all trade parameters
        """
        
        # Get current prices
        spot_price = stock_data['spot_price']
        option_premium = option_data['ltp']
        lot_size = stock_data['lot_size']
        strike = option_data.get('strike', self._calc_atm_strike(spot_price))
        
        # Determine option type (CE for bullish, PE for bearish)
        # For now, assume CE based on impulse direction
        option_type = 'CE'
        
        # Entry parameters
        entry_premium = option_premium
        capital_required = entry_premium * lot_size
        
        # Stop loss
        sl_premium = entry_premium * (1 - self.sl_pct)
        sl_loss = (entry_premium - sl_premium) * lot_size
        
        # Targets
        target1_premium = entry_premium * (1 + self.target1_pct)
        target2_premium = entry_premium * (1 + self.target2_pct)
        
        target1_profit = (target1_premium - entry_premium) * lot_size * 0.5  # 50% position
        target2_profit = (target2_premium - entry_premium) * lot_size * 0.5  # Remaining 50%
        total_target_profit = target1_profit + target2_profit
        
        # Risk:Reward
        risk = sl_loss
        reward = total_target_profit
        rr_ratio = reward / risk if risk > 0 else 0
        
        # Create signal
        signal = {
            # Identity
            'signal_id': str(uuid.uuid4()),
            'symbol': symbol,
            'timestamp': datetime.now().isoformat(),
            
            # Entry
            'entry_type': 'MARKET',
            'entry_instruction': 'Entry ONLY on candle close',
            'strike': strike,
            'option_type': option_type,
            'entry_premium': round(entry_premium, 2),
            'lot_size': lot_size,
            'capital_required': round(capital_required, 0),
            
            # Stop Loss
            'sl_premium': round(sl_premium, 2),
            'sl_pct': self.sl_pct * 100,
            'sl_loss': round(sl_loss, 0),
            'time_sl_seconds': self.time_sl_seconds,
            
            # Targets
            'target1_premium': round(target1_premium, 2),
            'target1_pct': self.target1_pct * 100,
            'target1_profit': round(target1_profit, 0),
            'target1_action': 'Book 50%',
            
            'target2_premium': round(target2_premium, 2),
            'target2_pct': self.target2_pct * 100,
            'target2_profit': round(target2_profit, 0),
            'target2_action': 'Exit Fully',
            
            # Risk:Reward
            'rr_ratio': round(rr_ratio, 2),
            'total_expected_profit': round(total_target_profit, 0),
            
            # Supporting data
            'spot_price': round(spot_price, 2),
            'underlying_atr': round(stock_data.get('atr', 0), 2),
            'option_atr': round(option_data.get('atr', 0), 3),
            
            # Filter metrics (for transparency)
            'filter_metrics': filter_metrics,
            
            # Status
            'status': 'PENDING',
            'executed': False
        }
        
        return signal
    
    def _calc_atm_strike(self, spot_price: float) -> int:
        """Calculate ATM strike from spot price"""
        # Round to nearest 10 for most stocks
        if spot_price < 500:
            step = 10
        elif spot_price < 1500:
            step = 50
        else:
            step = 100
        
        return int(round(spot_price / step) * step)
    
    def format_signal_for_display(self, signal: Dict) -> str:
        """
        Format signal as readable text for UI display
        
        Returns:
            Formatted signal text
        """
        text = f"""
🚀 ₹500 PROBABLE - EXECUTE

Stock: {signal['symbol']}
Strike: {signal['strike']} {signal['option_type']}
Entry: ₹{signal['entry_premium']} (Market order on candle close)
Lot Size: {signal['lot_size']:,}
Capital: ₹{signal['capital_required']:,}

📉 Stop Loss:
  Premium: ₹{signal['sl_premium']} (-{signal['sl_pct']}%)
  Max Loss: ₹{signal['sl_loss']:,}
  Time SL: {signal['time_sl_seconds']} seconds without movement

🎯 Targets:
  Target 1: ₹{signal['target1_premium']} (+{signal['target1_pct']}%) → {signal['target1_action']} → ₹{signal['target1_profit']:,}
  Target 2: ₹{signal['target2_premium']} (+{signal['target2_pct']}%) → {signal['target2_action']} → ₹{signal['target2_profit']:,}

💰 Expected Profit: ₹{signal['total_expected_profit']:,}
⚖️ Risk:Reward: 1:{signal['rr_ratio']}

Spot: ₹{signal['spot_price']}
ATR: ₹{signal['underlying_atr']} (stock) | ₹{signal['option_atr']} (option)
"""
        return text.strip()

if __name__ == "__main__":
    # Test signal generation
    print("🧪 Testing Signal Generator...")
    
    generator = SignalGenerator()
    
    # Mock data
    signal = generator.generate(
        symbol="PNB",
        stock_data={
            'spot_price': 120.37,
            'atr': 0.50,
            'lot_size': 5000
        },
        option_data={
            'ltp': 0.56,
            'strike': 121,
            'atr': 0.08
        },
        filter_metrics={
            'impulse': 'VWAP reclaim',
            'option_response': 'LTP +3.5%',
            'feasibility': '₹500 easy'
        }
    )
    
    print("\nGenerated Signal:")
    print(generator.format_signal_for_display(signal))
    
    print(f"\nSignal ID: {signal['signal_id']}")
    print(f"Capital Required: ₹{signal['capital_required']:,}")
    print(f"R:R: 1:{signal['rr_ratio']}") 
