#!/usr/bin/env python3
"""Comprehensive test for portfolio functionality"""

from core.portfolio.allocator import PortfolioAllocator
from core.portfolio.engine import PortfolioEngine
from datetime import datetime

def test_portfolio_allocator():
    """Test the PortfolioAllocator functionality"""
    print("Testing PortfolioAllocator...")
    
    allocator = PortfolioAllocator(total_capital=100000, max_concurrent_positions=3)
    
    # Test equal weight allocation
    symbols = ["SYMBOL1", "SYMBOL2", "SYMBOL3", "SYMBOL4"]
    allocations = allocator.equal_weight(symbols)
    print(f"Equal weight allocations: {allocations}")
    assert len(allocations) == 3, "Should limit to max_concurrent_positions"
    assert all(abs(allocations[s] - 100000/3) < 1 for s in allocations), "Should allocate equally"
    
    # Test inverse volatility allocation
    volatilities = {"SYMBOL1": 0.2, "SYMBOL2": 0.1, "SYMBOL3": 0.15, "SYMBOL4": 0.25}
    inv_allocations = allocator.inverse_volatility(symbols, volatilities)
    print(f"Inverse volatility allocations: {inv_allocations}")
    assert len(inv_allocations) <= 3, "Should limit to max_concurrent_positions"
    
    # Test risk parity allocation
    rp_allocations = allocator.risk_parity(symbols, volatilities)
    print(f"Risk parity allocations: {rp_allocations}")
    assert len(rp_allocations) <= 3, "Should limit to max_concurrent_positions"
    
    # Test rank weighted allocation
    symbol_ranks = {"SYMBOL1": 1, "SYMBOL2": 2, "SYMBOL3": 3, "SYMBOL4": 4}
    rank_allocations = allocator.rank_weighted(symbol_ranks)
    print(f"Rank weighted allocations: {rank_allocations}")
    assert len(rank_allocations) <= 3, "Should limit to max_concurrent_positions"
    
    print("PortfolioAllocator tests passed!")


def test_portfolio_engine():
    """Test the PortfolioEngine functionality"""
    print("\nTesting PortfolioEngine...")
    
    allocations = {"SYMBOL1": 50000, "SYMBOL2": 30000, "SYMBOL3": 20000}
    engine = PortfolioEngine(
        allocations=allocations,
        max_concurrent_positions=2,
        max_correlation_allowed=0.7
    )
    
    # Test can_open_position
    assert engine.can_open_position("SYMBOL1", datetime.now()), "Should allow opening position for SYMBOL1"
    assert engine.can_open_position("SYMBOL2", datetime.now()), "Should allow opening position for SYMBOL2"
    
    # Open positions
    engine.open_position("SYMBOL1", "LONG", 100.0, 100, datetime.now(), {})
    engine.open_position("SYMBOL2", "SHORT", 200.0, 50, datetime.now(), {})
    
    # Should not allow third position due to max_concurrent constraint
    assert not engine.can_open_position("SYMBOL3", datetime.now()), "Should not allow third position"
    
    # Close a position
    pnl = engine.close_position("SYMBOL1", 105.0, datetime.now())
    print(f"Closed position for SYMBOL1, PnL: {pnl}")
    
    # Now should be able to open position for SYMBOL3
    assert engine.can_open_position("SYMBOL3", datetime.now()), "Should allow opening position after closing one"
    
    # Get portfolio metrics
    metrics = engine.get_portfolio_metrics()
    print(f"Portfolio metrics: {metrics}")
    
    print("PortfolioEngine tests passed!")


def test_imports():
    """Test that all modules can be imported correctly"""
    print("\nTesting imports...")
    
    from core.portfolio.allocator import PortfolioAllocator
    from core.portfolio.engine import PortfolioEngine
    from core.backtest.portfolio_backtest import PortfolioBacktestRunner
    
    print("All imports successful!")


if __name__ == "__main__":
    test_imports()
    test_portfolio_allocator()
    test_portfolio_engine()
    
    print("\nAll tests passed! Portfolio functionality is working correctly.")