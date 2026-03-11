from analytics.structured_metrics import build_strategy_metrics_snapshot


def test_build_strategy_metrics_snapshot_fields():
    snapshot = build_strategy_metrics_snapshot(
        current_iv=0.22,
        iv_history=[0.15, 0.18, 0.20, 0.25, 0.30],
        realized_volatility=0.19,
        spread_pcts=[0.01, 0.015, 0.02],
        liquidity_rejections=5,
        liquidity_checks=20,
        slippage_bps_samples=[3.0, 4.0, 5.0],
    )
    payload = snapshot.to_dict()
    assert 0.0 <= payload["iv_rank"] <= 1.0
    assert payload["realized_volatility"] == 0.19
    assert payload["option_spread_stats"]["p90_spread_pct"] >= payload["option_spread_stats"]["mean_spread_pct"]
    assert payload["liquidity_rejection_rate"] == 0.25

