import numpy as np

from analytics.options_greeks import (
    black76_price,
    implied_volatility_black76,
    compute_greeks_with_iv,
)


def test_implied_volatility_recovers_known_sigma():
    f = np.array([75000.0, 76000.0, 74000.0])
    k = np.array([75000.0, 75500.0, 73500.0])
    t = np.array([20.0 / 365.0, 25.0 / 365.0, 15.0 / 365.0])
    r = np.array([0.06, 0.06, 0.06])
    sigma = np.array([0.22, 0.18, 0.25])
    typ = np.array(["CE", "PE", "CE"])

    price = black76_price(f, k, t, r, sigma, typ)
    iv = implied_volatility_black76(f, k, t, r, price, typ)
    assert np.allclose(iv, sigma, atol=1e-4)


def test_compute_greeks_with_iv_vectorized_outputs():
    f = np.array([75000.0, 75000.0])
    k = np.array([74500.0, 75500.0])
    t = np.array([30.0 / 365.0, 30.0 / 365.0])
    r = np.array([0.06, 0.06])
    sigma = np.array([0.20, 0.20])
    typ = np.array(["CE", "PE"])
    option_price = black76_price(f, k, t, r, sigma, typ)

    out = compute_greeks_with_iv(
        underlying_futures_price=f,
        strike=k,
        time_to_expiry=t,
        risk_free_rate=r,
        option_price=option_price,
        option_type=typ,
    )

    assert out.delta.shape == (2,)
    assert out.implied_volatility.shape == (2,)
    assert out.delta[0] > 0.0
    assert out.delta[1] < 0.0
    assert np.all(out.gamma >= 0.0)

