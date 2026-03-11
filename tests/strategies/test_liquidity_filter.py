from datetime import datetime, timedelta

from strategy.filters.liquidity_filter import (
    LiquidityCheckInput,
    evaluate_liquidity,
)


def test_rejects_contract_on_multiple_liquidity_failures():
    now = datetime(2026, 3, 5, 12, 0, 0)
    payload = LiquidityCheckInput(
        bid=100.0,
        ask=106.0,  # 5.8% spread
        open_interest=1000,
        last_trade_time=now - timedelta(minutes=10),
        now=now,
        premium=3.0,
    )
    out = evaluate_liquidity(
        payload,
        max_spread_pct=0.02,
        min_open_interest=5000,
        max_last_trade_gap_seconds=120,
        min_tradable_premium=5.0,
    )
    assert not out.trade_allowed
    assert len(out.reasons) >= 3

