from strategy.regime.volatility_regime import (
    VolatilityRegime,
    classify_volatility_regime,
)


def test_high_vol_regime_classification():
    out = classify_volatility_regime(
        realized_volatility_20d=0.20,
        realized_volatility_5d=0.22,
        option_implied_volatility=0.32,  # 1.6x HV
        atr=140.0,
    )
    assert out.regime == VolatilityRegime.HIGH_VOL


def test_low_vol_regime_classification():
    out = classify_volatility_regime(
        realized_volatility_20d=0.20,
        realized_volatility_5d=0.18,
        option_implied_volatility=0.10,  # 0.5x HV
        atr=80.0,
    )
    assert out.regime == VolatilityRegime.LOW_VOL


def test_vol_expansion_classification():
    out = classify_volatility_regime(
        realized_volatility_20d=0.20,
        realized_volatility_5d=0.30,  # rapid short-term expansion
        option_implied_volatility=0.24,
        atr=180.0,
    )
    assert out.regime == VolatilityRegime.VOL_EXPANSION

