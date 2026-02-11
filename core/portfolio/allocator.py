from typing import Dict, List


class PortfolioAllocator:
    """Capital allocation across multiple symbols."""

    def __init__(self, total_capital: float, max_concurrent_positions: int = 5):
        self.total_capital = total_capital
        self.max_concurrent = max_concurrent_positions

    def equal_weight(self, symbols: List[str]) -> Dict[str, float]:
        """Equal capital allocation. Returns {symbol: capital_per_symbol}."""
        n = min(len(symbols), self.max_concurrent)
        if n == 0:
            return {}
        per_symbol = self.total_capital / n
        return {s: per_symbol for s in symbols[:n]}

    def inverse_volatility(self, symbols: List[str], volatilities: Dict[str, float]) -> Dict[str, float]:
        """Allocate inversely proportional to historical volatility.
        Lower vol symbols get more capital. volatilities = {symbol: vol_value}."""
        if not symbols:
            return {}

        # Limit to max_concurrent FIRST, then compute weights only for those
        selected = symbols[:self.max_concurrent]

        inv_vols = {}
        for symbol in selected:
            vol = volatilities.get(symbol, 0)
            inv_vols[symbol] = (1.0 / vol) if vol > 0 else 0.001

        total_inv_vol = sum(inv_vols.values())
        if total_inv_vol <= 0:
            return self.equal_weight(selected)

        return {s: (iv / total_inv_vol) * self.total_capital for s, iv in inv_vols.items()}

    def risk_parity(self, symbols: List[str], volatilities: Dict[str, float]) -> Dict[str, float]:
        """Each symbol contributes equal risk (vol * allocation = constant)."""
        return self.inverse_volatility(symbols, volatilities)

    def rank_weighted(self, symbol_ranks: Dict[str, int]) -> Dict[str, float]:
        """Higher-ranked symbols (lower rank number) get more capital.
        Weights: 1/rank normalized."""
        if not symbol_ranks:
            return {}

        # Sort by rank, take top max_concurrent
        sorted_symbols = sorted(symbol_ranks.keys(), key=lambda x: symbol_ranks[x])
        selected = sorted_symbols[:self.max_concurrent]

        weights = {}
        for symbol in selected:
            rank = symbol_ranks[symbol]
            weights[symbol] = (1.0 / rank) if rank > 0 else 0.001

        total_weight = sum(weights.values())
        if total_weight <= 0:
            return self.equal_weight(selected)

        return {s: (w / total_weight) * self.total_capital for s, w in weights.items()}
