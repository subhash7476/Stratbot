"""
Black-76 implied volatility and Greeks utilities.

This module is designed for historical/batch workflows:
- fully vectorized numpy operations
- numerically stable IV solver with Newton + bisection fallback
- scalar and array input support
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Union
import math

import numpy as np

ArrayLike = Union[float, Sequence[float], np.ndarray]

_SQRT_2PI = math.sqrt(2.0 * math.pi)
_EPS = 1e-12


@dataclass(frozen=True)
class GreeksResult:
    """Vectorized output container for Black-76 Greeks and IV."""

    delta: np.ndarray
    gamma: np.ndarray
    theta: np.ndarray
    vega: np.ndarray
    implied_volatility: np.ndarray


def _to_array(value: ArrayLike) -> np.ndarray:
    arr = np.asarray(value, dtype=float)
    if arr.ndim == 0:
        arr = arr.reshape(1)
    return arr


def _broadcast(*arrays: np.ndarray) -> list[np.ndarray]:
    return [np.asarray(x, dtype=float) for x in np.broadcast_arrays(*arrays)]


def _option_sign(option_type: Union[str, Sequence[str], np.ndarray]) -> np.ndarray:
    if isinstance(option_type, str):
        values = np.array([option_type], dtype=object)
    else:
        values = np.asarray(option_type, dtype=object)
        if values.ndim == 0:
            values = values.reshape(1)
    normalized = np.char.upper(np.char.strip(values.astype(str)))
    call_mask = np.isin(normalized, ["C", "CE", "CALL"])
    put_mask = np.isin(normalized, ["P", "PE", "PUT"])
    if not np.all(call_mask | put_mask):
        bad = normalized[~(call_mask | put_mask)]
        raise ValueError(f"Unsupported option_type values: {bad.tolist()}")
    return np.where(call_mask, 1.0, -1.0)


def _norm_pdf(x: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * x * x) / _SQRT_2PI


def _norm_cdf(x: np.ndarray) -> np.ndarray:
    """
    Fast normal CDF approximation (Abramowitz-Stegun 7.1.26).

    Max absolute error is small enough for practical risk/backtesting use.
    """
    abs_x = np.abs(x)
    t = 1.0 / (1.0 + 0.2316419 * abs_x)
    poly = (
        0.319381530 * t
        - 0.356563782 * t**2
        + 1.781477937 * t**3
        - 1.821255978 * t**4
        + 1.330274429 * t**5
    )
    cdf_pos = 1.0 - _norm_pdf(abs_x) * poly
    return np.where(x >= 0.0, cdf_pos, 1.0 - cdf_pos)


def black76_price(
    futures_price: ArrayLike,
    strike: ArrayLike,
    time_to_expiry: ArrayLike,
    risk_free_rate: ArrayLike,
    volatility: ArrayLike,
    option_type: Union[str, Sequence[str], np.ndarray],
) -> np.ndarray:
    """Vectorized Black-76 premium for options on futures."""
    F = _to_array(futures_price)
    K = _to_array(strike)
    T = _to_array(time_to_expiry)
    r = _to_array(risk_free_rate)
    sigma = _to_array(volatility)
    sign = _option_sign(option_type)

    F, K, T, r, sigma, sign = _broadcast(F, K, T, r, sigma, sign)

    intrinsic = np.maximum(sign * (F - K), 0.0)
    live_mask = (T > _EPS) & (sigma > _EPS) & (F > _EPS) & (K > _EPS)
    price = np.exp(-r * np.maximum(T, 0.0)) * intrinsic
    if not np.any(live_mask):
        return price

    sqrt_t = np.sqrt(np.maximum(T[live_mask], _EPS))
    sigma_live = np.maximum(sigma[live_mask], _EPS)
    log_fk = np.log(np.maximum(F[live_mask], _EPS) / np.maximum(K[live_mask], _EPS))
    d1 = (log_fk + 0.5 * sigma_live * sigma_live * T[live_mask]) / (sigma_live * sqrt_t)
    d2 = d1 - sigma_live * sqrt_t
    disc = np.exp(-r[live_mask] * T[live_mask])
    nd1 = _norm_cdf(sign[live_mask] * d1)
    nd2 = _norm_cdf(sign[live_mask] * d2)
    price[live_mask] = disc * sign[live_mask] * (F[live_mask] * nd1 - K[live_mask] * nd2)
    return np.maximum(price, 0.0)


def implied_volatility_black76(
    futures_price: ArrayLike,
    strike: ArrayLike,
    time_to_expiry: ArrayLike,
    risk_free_rate: ArrayLike,
    option_price: ArrayLike,
    option_type: Union[str, Sequence[str], np.ndarray],
    *,
    tol: float = 1e-8,
    max_iter: int = 100,
    sigma_low: float = 1e-6,
    sigma_high: float = 5.0,
) -> np.ndarray:
    """Vectorized implied volatility via robust Newton + bracket fallback."""
    F = _to_array(futures_price)
    K = _to_array(strike)
    T = _to_array(time_to_expiry)
    r = _to_array(risk_free_rate)
    P = _to_array(option_price)
    sign = _option_sign(option_type)
    F, K, T, r, P, sign = _broadcast(F, K, T, r, P, sign)

    iv = np.full_like(P, np.nan, dtype=float)
    valid = (F > _EPS) & (K > _EPS) & (T > _EPS) & (P >= 0.0)
    if not np.any(valid):
        return iv

    Fv = F[valid]
    Kv = K[valid]
    Tv = T[valid]
    rv = r[valid]
    Pv = P[valid]
    signv = sign[valid]

    disc = np.exp(-rv * Tv)
    intrinsic = disc * np.maximum(signv * (Fv - Kv), 0.0)
    upper_bound = disc * np.where(signv > 0.0, Fv, Kv)
    feasible = (Pv >= intrinsic - 1e-10) & (Pv <= upper_bound + 1e-10)

    iv_local = np.full_like(Pv, np.nan, dtype=float)
    if not np.any(feasible):
        iv[valid] = iv_local
        return iv

    idx = np.where(feasible)[0]
    sigma = np.full(idx.shape[0], 0.25, dtype=float)
    low = np.full(idx.shape[0], sigma_low, dtype=float)
    high = np.full(idx.shape[0], sigma_high, dtype=float)

    target = Pv[idx]
    Ff = Fv[idx]
    Kf = Kv[idx]
    Tf = Tv[idx]
    rf = rv[idx]
    sf = signv[idx]

    for _ in range(max_iter):
        px = black76_price(Ff, Kf, Tf, rf, sigma, np.where(sf > 0.0, "CE", "PE"))
        sqrt_t = np.sqrt(np.maximum(Tf, _EPS))
        d1 = (np.log(Ff / Kf) + 0.5 * sigma * sigma * Tf) / (sigma * sqrt_t)
        vega = np.exp(-rf * Tf) * Ff * _norm_pdf(d1) * sqrt_t
        err = px - target
        done = np.abs(err) < tol
        if np.all(done):
            break

        # Keep bracket updated for guaranteed convergence fallback.
        low = np.where(err < 0.0, sigma, low)
        high = np.where(err > 0.0, sigma, high)

        step = np.divide(err, np.maximum(vega, 1e-10))
        candidate = sigma - step

        bad_newton = (~np.isfinite(candidate)) | (candidate <= low) | (candidate >= high) | (vega < 1e-8)
        bisect_candidate = 0.5 * (low + high)
        sigma = np.where(bad_newton, bisect_candidate, candidate)
        sigma = np.clip(sigma, sigma_low, sigma_high)

    # Final bisection polishing for any unconverged points.
    px = black76_price(Ff, Kf, Tf, rf, sigma, np.where(sf > 0.0, "CE", "PE"))
    unresolved = np.abs(px - target) >= tol
    for _ in range(40):
        if not np.any(unresolved):
            break
        sigma_mid = 0.5 * (low + high)
        px_mid = black76_price(Ff, Kf, Tf, rf, sigma_mid, np.where(sf > 0.0, "CE", "PE"))
        err_mid = px_mid - target
        low = np.where(err_mid < 0.0, sigma_mid, low)
        high = np.where(err_mid > 0.0, sigma_mid, high)
        sigma = np.where(unresolved, sigma_mid, sigma)
        unresolved = np.abs(err_mid) >= tol

    iv_local[idx] = sigma
    iv[valid] = iv_local
    return iv


def compute_greeks_with_iv(
    underlying_futures_price: ArrayLike,
    strike: ArrayLike,
    time_to_expiry: ArrayLike,
    risk_free_rate: ArrayLike,
    option_price: ArrayLike,
    option_type: Union[str, Sequence[str], np.ndarray],
) -> GreeksResult:
    """
    Compute implied volatility and Black-76 Greeks for historical arrays.

    Theta is annualized premium decay (per year, not per day).
    """
    F = _to_array(underlying_futures_price)
    K = _to_array(strike)
    T = _to_array(time_to_expiry)
    r = _to_array(risk_free_rate)
    sign = _option_sign(option_type)
    F, K, T, r, sign = _broadcast(F, K, T, r, sign)

    iv = implied_volatility_black76(F, K, T, r, option_price, np.where(sign > 0.0, "CE", "PE"))

    delta = np.zeros_like(iv)
    gamma = np.zeros_like(iv)
    theta = np.zeros_like(iv)
    vega = np.zeros_like(iv)

    live = np.isfinite(iv) & (iv > _EPS) & (T > _EPS) & (F > _EPS) & (K > _EPS)
    if np.any(live):
        Fl = F[live]
        Kl = K[live]
        Tl = T[live]
        rl = r[live]
        sigl = iv[live]
        signl = sign[live]
        sqrt_t = np.sqrt(np.maximum(Tl, _EPS))
        d1 = (np.log(Fl / Kl) + 0.5 * sigl * sigl * Tl) / (sigl * sqrt_t)
        d2 = d1 - sigl * sqrt_t
        disc = np.exp(-rl * Tl)
        pdf_d1 = _norm_pdf(d1)

        delta[live] = signl * disc * _norm_cdf(signl * d1)
        gamma[live] = disc * pdf_d1 / (Fl * sigl * sqrt_t)
        vega[live] = disc * Fl * pdf_d1 * sqrt_t
        theta[live] = (
            -disc * Fl * pdf_d1 * sigl / (2.0 * sqrt_t)
            + rl * disc * signl * (Fl * _norm_cdf(signl * d1) - Kl * _norm_cdf(signl * d2))
        )

    return GreeksResult(
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        implied_volatility=iv,
    )

