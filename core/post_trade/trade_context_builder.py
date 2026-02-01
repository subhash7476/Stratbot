"""
Trade Context Builder
---------------------
Assembles TradeTruth objects from historical events.
"""
from core.events import TradeEvent
from core.data.analytics_provider import AnalyticsProvider
from core.post_trade.trade_truth_model import TradeTruth

class TradeContextBuilder:
    def __init__(self, analytics: AnalyticsProvider):
        self.analytics = analytics

    def build_truth(self, trade: TradeEvent) -> TradeTruth:
        # Fetch the facts that existed at the trade timestamp
        insight = self.analytics.get_latest_snapshot(trade.symbol)
        regime = self.analytics.get_market_regime(trade.symbol)
        
        return TradeTruth(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            timestamp=trade.timestamp,
            bias_at_entry=insight.bias.value if insight else "NEUTRAL",
            confidence_at_entry=insight.confidence_score if insight else 0.0,
            indicator_facts={r.name: r.bias.value for r in insight.indicator_results} if insight else {},
            regime_at_entry=regime.get("regime") if regime else None
        )
