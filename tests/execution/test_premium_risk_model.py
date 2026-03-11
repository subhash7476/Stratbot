from risk.premium_risk_model import map_underlying_stop_to_premium_risk


def test_premium_risk_mapping_contract_cap():
    out = map_underlying_stop_to_premium_risk(
        account_equity=1_000_000.0,
        risk_pct=0.005,
        underlying_atr=120.0,
        atr_multiplier=1.5,
        option_delta=0.30,
        lot_size=100,
        option_gamma=0.0002,
    )
    assert out.expected_underlying_move == 180.0
    assert out.risk_budget == 5000.0
    assert out.risk_per_contract > 0.0
    assert out.max_contracts >= 0

