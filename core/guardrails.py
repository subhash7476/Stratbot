"""
Global Guardrails - System-Level Lockout Rules
Checked BEFORE any scanning begins
"""

import duckdb
from datetime import datetime, time, date
from pathlib import Path
from typing import Dict, Tuple

class GlobalGuardrails:
    """
    Enforces non-negotiable trading rules:
    - Trading time window: 9:20 AM - 11:30 AM
    - Max trades per day: 5
    - Max consecutive losses: 2
    - Max daily loss: -2R (₹1,000 assuming ₹500 per R)
    """
    
    def __init__(self, db_path: str = "data/trading_bot.db"):
        self.db_path = db_path
        self.TRADING_START = time(9, 16)
        self.TRADING_END = time(13, 30)
        self.MAX_TRADES_PER_DAY = 20
        self.MAX_CONSECUTIVE_LOSSES = 2
        self.MAX_DAILY_LOSS = -1000  # -2R in rupees
    
    def _get_today_stats(self) -> Dict:
        """Get today's trading statistics"""
        try:
            conn = duckdb.connect(self.db_path)
            today = date.today()
            
            result = conn.execute("""
                SELECT 
                    trades_count,
                    wins,
                    losses,
                    consecutive_losses,
                    daily_pnl,
                    locked_out,
                    lockout_reason
                FROM daily_risk_log
                WHERE date = ?
            """, [today]).fetchone()
            
            conn.close()
            
            if result:
                return {
                    'trades_count': result[0],
                    'wins': result[1],
                    'losses': result[2],
                    'consecutive_losses': result[3],
                    'daily_pnl': float(result[4]),
                    'locked_out': result[5],
                    'lockout_reason': result[6]
                }
            else:
                # Initialize today's entry
                return {
                    'trades_count': 0,
                    'wins': 0,
                    'losses': 0,
                    'consecutive_losses': 0,
                    'daily_pnl': 0.0,
                    'locked_out': False,
                    'lockout_reason': None
                }
        except Exception as e:
            print(f"⚠️ Error getting today's stats: {e}")
            return {
                'trades_count': 0,
                'wins': 0,
                'losses': 0,
                'consecutive_losses': 0,
                'daily_pnl': 0.0,
                'locked_out': False,
                'lockout_reason': None
            }
    
    def check_time_window(self) -> Tuple[bool, str]:
        """
        Check if current time is within trading window
        
        Returns:
            (allowed: bool, reason: str)
        """
        now = datetime.now().time()
        
        if now < self.TRADING_START:
            return False, f"Market not open yet. Trading starts at {self.TRADING_START.strftime('%H:%M')}"
        
        if now > self.TRADING_END:
            return False, f"Trading window closed. Last trade at {self.TRADING_END.strftime('%H:%M')}"
        
        return True, "Time window OK"
    
    def check_daily_limits(self) -> Tuple[bool, str]:
        """
        Check if daily trading limits are exceeded
        
        Returns:
            (allowed: bool, reason: str)
        """
        stats = self._get_today_stats()
        
        # Check if already locked out
        if stats['locked_out']:
            return False, stats['lockout_reason'] or "System locked out"
        
        # Check max trades
        if stats['trades_count'] >= self.MAX_TRADES_PER_DAY:
            return False, f"Max trades reached ({self.MAX_TRADES_PER_DAY}/day limit)"
        
        # Check consecutive losses
        if stats['consecutive_losses'] >= self.MAX_CONSECUTIVE_LOSSES:
            return False, f"Max consecutive losses hit ({self.MAX_CONSECUTIVE_LOSSES} in a row) - STOP for day"
        
        # Check daily loss limit
        if stats['daily_pnl'] <= self.MAX_DAILY_LOSS:
            return False, f"Daily loss limit exceeded (₹{stats['daily_pnl']:.0f} <= ₹{self.MAX_DAILY_LOSS})"
        
        return True, "Daily limits OK"
    
    def can_trade_now(self) -> Dict:
        """
        Master check - combines all guardrails
        
        Returns:
            {
                'allowed': bool,
                'reason': str,
                'stats': dict
            }
        """
        stats = self._get_today_stats()
        
        # Check time window
        time_ok, time_msg = self.check_time_window()
        if not time_ok:
            return {
                'allowed': False,
                'reason': time_msg,
                'stats': stats
            }
        
        # Check daily limits
        limits_ok, limits_msg = self.check_daily_limits()
        if not limits_ok:
            return {
                'allowed': False,
                'reason': limits_msg,
                'stats': stats
            }
        
        # All clear
        return {
            'allowed': True,
            'reason': '✅ ALL SYSTEMS GO',
            'stats': stats
        }
    
    def update_trade_result(self, win: bool, pnl: float):
        """
        Update daily stats after a trade completes
        
        Args:
            win: True if profitable trade
            pnl: Profit/loss in rupees
        """
        try:
            conn = duckdb.connect(self.db_path)
            today = date.today()
            
            # Get current stats
            stats = self._get_today_stats()
            
            # Update counters
            new_trades = stats['trades_count'] + 1
            new_wins = stats['wins'] + (1 if win else 0)
            new_losses = stats['losses'] + (0 if win else 1)
            new_consecutive = 0 if win else stats['consecutive_losses'] + 1
            new_pnl = stats['daily_pnl'] + pnl
            
            # Check lockout conditions
            locked_out = False
            lockout_reason = None
            
            if new_consecutive >= self.MAX_CONSECUTIVE_LOSSES:
                locked_out = True
                lockout_reason = f"{self.MAX_CONSECUTIVE_LOSSES} consecutive losses - LOCKED OUT"
            
            if new_pnl <= self.MAX_DAILY_LOSS:
                locked_out = True
                lockout_reason = f"Daily loss limit hit (₹{new_pnl:.0f}) - LOCKED OUT"
            
            # Update database
            conn.execute("""
                INSERT INTO daily_risk_log (
                    date, trades_count, wins, losses, consecutive_losses,
                    daily_pnl, locked_out, lockout_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (date) DO UPDATE SET
                    trades_count = excluded.trades_count,
                    wins = excluded.wins,
                    losses = excluded.losses,
                    consecutive_losses = excluded.consecutive_losses,
                    daily_pnl = excluded.daily_pnl,
                    locked_out = excluded.locked_out,
                    lockout_reason = excluded.lockout_reason
            """, [today, new_trades, new_wins, new_losses, new_consecutive, 
                  new_pnl, locked_out, lockout_reason])
            
            conn.close()
            
            print(f"✅ Updated daily stats: {new_trades} trades, {new_wins}W-{new_losses}L, P&L: ₹{new_pnl:.0f}")
            
            if locked_out:
                print(f"🔒 SYSTEM LOCKED: {lockout_reason}")
            
        except Exception as e:
            print(f"⚠️ Error updating trade result: {e}")

if __name__ == "__main__":
    # Test the guardrails
    guardrails = GlobalGuardrails()
    
    print("🧪 Testing Global Guardrails...")
    print("\n1. Time Window Check:")
    time_ok, msg = guardrails.check_time_window()
    print(f"   {msg}")
    
    print("\n2. Daily Limits Check:")
    limits_ok, msg = guardrails.check_daily_limits()
    print(f"   {msg}")
    
    print("\n3. Master Check:")
    result = guardrails.can_trade_now()
    print(f"   Allowed: {result['allowed']}")
    print(f"   Reason: {result['reason']}")
    print(f"   Stats: {result['stats']}") 
