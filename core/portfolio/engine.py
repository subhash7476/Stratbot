from typing import Dict, List, Optional, Any
import pandas as pd
from datetime import datetime
from dataclasses import dataclass


@dataclass
class Position:
    symbol: str
    direction: str  # 'LONG' or 'SHORT'
    entry_price: float
    quantity: int
    entry_timestamp: datetime
    exit_price: Optional[float] = None
    exit_timestamp: Optional[datetime] = None
    pnl: float = 0.0
    metadata: Optional[dict] = None


class PortfolioEngine:
    """Portfolio-level risk management for multi-symbol backtesting."""

    def __init__(
        self,
        allocations: Dict[str, float],       # {symbol: capital}
        max_concurrent_positions: int = 5,
        max_correlation_allowed: float = 0.7, # Skip new position if avg corr > this
        correlation_matrix: Optional[pd.DataFrame] = None,
    ):
        self.allocations = allocations
        self.max_concurrent = max_concurrent_positions
        self.max_corr = max_correlation_allowed
        self.corr_matrix = correlation_matrix
        self.open_positions: Dict[str, Position] = {}  # symbol -> position info
        self.closed_positions: List[Position] = []      # List of closed positions
        self.equity_curve: List[Dict] = []              # [{timestamp, equity, symbol_equities}]
        self.total_pnl = 0.0
        self.peak_equity = sum(allocations.values())
        self.current_equity = self.peak_equity
        self.max_drawdown = 0.0
        self.trades_history: List[Dict] = []            # History of all trades

    def can_open_position(self, symbol: str, timestamp: datetime) -> bool:
        """Check if a new position is allowed given portfolio constraints."""
        # 1. Check max concurrent positions
        if len(self.open_positions) >= self.max_concurrent:
            return False

        # 2. Check symbol has remaining allocation
        if symbol not in self.allocations:
            return False

        # 3. Check correlation with existing open positions (if corr_matrix provided)
        if self.corr_matrix is not None and self.open_positions:
            for open_symbol in self.open_positions:
                if open_symbol in self.corr_matrix.index and symbol in self.corr_matrix.columns:
                    correlation = self.corr_matrix.loc[open_symbol, symbol]
                    if abs(correlation) > self.max_corr:
                        return False
                elif open_symbol in self.corr_matrix.columns and symbol in self.corr_matrix.index:
                    # Handle transposed case
                    correlation = self.corr_matrix.loc[symbol, open_symbol]
                    if abs(correlation) > self.max_corr:
                        return False

        return True

    def open_position(self, symbol: str, direction: str, entry_price: float, quantity: int, timestamp: datetime, metadata: dict):
        """Record a new open position."""
        position = Position(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            quantity=quantity,
            entry_timestamp=timestamp,
            metadata=metadata
        )
        self.open_positions[symbol] = position

    def close_position(self, symbol: str, exit_price: float, timestamp: datetime) -> float:
        """Close a position and return PnL. Updates equity curve and drawdown."""
        if symbol not in self.open_positions:
            return 0.0

        position = self.open_positions.pop(symbol)
        position.exit_price = exit_price
        position.exit_timestamp = timestamp
        
        # Calculate PnL based on direction
        if position.direction.upper() == 'LONG':
            pnl = (exit_price - position.entry_price) * position.quantity
        else:  # SHORT
            pnl = (position.entry_price - exit_price) * position.quantity
            
        position.pnl = pnl
        self.total_pnl += pnl
        self.current_equity += pnl
        
        # Add to closed positions
        self.closed_positions.append(position)
        
        # Record trade in history
        self.trades_history.append({
            'symbol': position.symbol,
            'entry_timestamp': position.entry_timestamp,
            'exit_timestamp': position.exit_timestamp,
            'entry_price': position.entry_price,
            'exit_price': position.exit_price,
            'quantity': position.quantity,
            'direction': position.direction,
            'pnl': position.pnl,
            'metadata': position.metadata
        })
        
        # Update peak equity and max drawdown
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity
        current_drawdown = (self.peak_equity - self.current_equity) / self.peak_equity if self.peak_equity > 0 else 0
        if current_drawdown > self.max_drawdown:
            self.max_drawdown = current_drawdown
            
        return pnl

    def get_portfolio_metrics(self) -> Dict[str, Any]:
        """Calculate portfolio-level metrics from the equity curve."""
        if not self.trades_history:
            return {
                'total_pnl': 0.0,
                'max_drawdown': 0.0,
                'sharpe_ratio': 0.0,
                'sortino_ratio': 0.0,
                'calmar_ratio': 0.0,
                'total_trades': 0,
                'win_rate': 0.0,
                'avg_trade_pnl': 0.0,
                'per_symbol_breakdown': {}
            }

        # Calculate basic metrics
        total_trades = len(self.trades_history)
        winning_trades = [t for t in self.trades_history if t['pnl'] > 0]
        losing_trades = [t for t in self.trades_history if t['pnl'] <= 0]
        
        win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0.0
        avg_trade_pnl = sum(t['pnl'] for t in self.trades_history) / total_trades if total_trades > 0 else 0.0
        
        # Per symbol breakdown
        symbol_breakdown = {}
        for trade in self.trades_history:
            symbol = trade['symbol']
            if symbol not in symbol_breakdown:
                symbol_breakdown[symbol] = {
                    'total_pnl': 0.0,
                    'total_trades': 0,
                    'winning_trades': 0,
                    'losing_trades': 0,
                    'avg_win': 0.0,
                    'avg_loss': 0.0
                }
            
            sb = symbol_breakdown[symbol]
            sb['total_pnl'] += trade['pnl']
            sb['total_trades'] += 1
            
            if trade['pnl'] > 0:
                sb['winning_trades'] += 1
            else:
                sb['losing_trades'] += 1
        
        for symbol, stats in symbol_breakdown.items():
            wins = [t['pnl'] for t in self.trades_history if t['symbol'] == symbol and t['pnl'] > 0]
            losses = [t['pnl'] for t in self.trades_history if t['symbol'] == symbol and t['pnl'] <= 0]
            
            stats['avg_win'] = sum(wins) / len(wins) if wins else 0.0
            stats['avg_loss'] = sum(losses) / len(losses) if losses else 0.0
        
        # Calculate risk ratios (simplified)
        # Note: These are simplified calculations; in practice, you'd need more detailed equity curve data
        returns = [t['pnl'] for t in self.trades_history]
        avg_return = sum(returns) / len(returns) if returns else 0.0
        std_returns = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5 if returns else 0.0
        
        # Sharpe ratio (assuming risk-free rate of 0 for simplicity)
        sharpe_ratio = (avg_return / std_returns) * (252 ** 0.5) if std_returns != 0 else 0.0  # Annualized
        
        # Sortino ratio (using downside deviation)
        downside_returns = [r for r in returns if r < avg_return]
        if downside_returns:
            downside_deviation = (sum((r - avg_return) ** 2 for r in downside_returns) / len(downside_returns)) ** 0.5
            sortino_ratio = (avg_return / downside_deviation) * (252 ** 0.5) if downside_deviation != 0 else 0.0
        else:
            sortino_ratio = sharpe_ratio  # If no downside deviation, use sharpe ratio
            
        # Calmar ratio (return over max drawdown)
        calmar_ratio = (avg_return * 252) / abs(self.max_drawdown) if self.max_drawdown != 0 else 0.0  # Annualized
        
        return {
            'total_pnl': self.total_pnl,
            'max_drawdown': self.max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'sortino_ratio': sortino_ratio,
            'calmar_ratio': calmar_ratio,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'avg_trade_pnl': avg_trade_pnl,
            'per_symbol_breakdown': symbol_breakdown
        }

    def update_equity_snapshot(self, timestamp: datetime, market_prices: Dict[str, float]):
        """Snapshot equity at a point in time (for equity curve with mark-to-market)."""
        # Calculate current value of open positions
        current_value = self.total_pnl  # Start with realized PnL
        
        for symbol, position in self.open_positions.items():
            if symbol in market_prices:
                if position.direction.upper() == 'LONG':
                    unrealized_pnl = (market_prices[symbol] - position.entry_price) * position.quantity
                else:  # SHORT
                    unrealized_pnl = (position.entry_price - market_prices[symbol]) * position.quantity
                current_value += unrealized_pnl
            else:
                # If we don't have a market price, use the entry price (no PnL)
                current_value += 0
        
        # Add back the initial capital to get total equity
        current_equity = self.peak_equity - sum(self.allocations.values()) + current_value
        
        # Update current equity
        self.current_equity = current_equity
        
        # Update peak equity and max drawdown
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity
        current_drawdown = (self.peak_equity - self.current_equity) / self.peak_equity if self.peak_equity > 0 else 0
        if current_drawdown > self.max_drawdown:
            self.max_drawdown = current_drawdown
        
        # Record snapshot
        snapshot = {
            'timestamp': timestamp,
            'total_equity': current_equity,
            'realized_pnl': self.total_pnl,
            'unrealized_pnl': current_value - self.total_pnl,
            'symbol_equities': {}
        }
        
        # Add per-symbol equity information
        for symbol, position in self.open_positions.items():
            if symbol in market_prices:
                if position.direction.upper() == 'LONG':
                    symbol_unrealized = (market_prices[symbol] - position.entry_price) * position.quantity
                else:  # SHORT
                    symbol_unrealized = (position.entry_price - market_prices[symbol]) * position.quantity
                snapshot['symbol_equities'][symbol] = {
                    'entry_price': position.entry_price,
                    'current_price': market_prices[symbol],
                    'unrealized_pnl': symbol_unrealized,
                    'quantity': position.quantity
                }
        
        self.equity_curve.append(snapshot)