from strategy.options.strike_selector import (
    OptionCandidate,
    select_best_strike,
)


def test_select_best_strike_continuation_prefers_target_delta():
    chain = [
        OptionCandidate(
            strike=74500,
            option_type="CE",
            delta=0.52,
            bid=220.0,
            ask=223.0,
            open_interest=20000,
            trading_symbol="GOLDCE74500",
        ),
        OptionCandidate(
            strike=75000,
            option_type="CE",
            delta=0.34,
            bid=165.0,
            ask=167.0,
            open_interest=25000,
            trading_symbol="GOLDCE75000",
        ),
        OptionCandidate(
            strike=75500,
            option_type="CE",
            delta=0.22,
            bid=118.0,
            ask=120.0,
            open_interest=15000,
            trading_symbol="GOLDCE75500",
        ),
    ]
    out = select_best_strike(
        underlying_price=74880.0,
        option_chain_snapshot=chain,
        direction="up",
        signal_mode="continuation",
        min_oi_threshold=5000,
        max_bid_ask_spread_pct=0.03,
    )
    assert out.selected is not None
    assert out.selected.trading_symbol == "GOLDCE75000"

