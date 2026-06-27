"""
utils/options_math.py
---------------------
Black-Scholes option pricing and Greeks.

Implements:
  - price()   → theoretical option price
  - delta()   → rate of change of price w.r.t. underlying
  - gamma()   → rate of change of delta w.r.t. underlying
  - theta()   → time decay per day
  - vega()    → sensitivity to IV (per 1% move in IV)
  - iv_approx() → rough IV estimate from market price (Newton-Raphson)

All functions are pure — no side effects, no I/O.

Reference: https://en.wikipedia.org/wiki/Black%E2%80%93Scholes_model
"""

import math
from scipy.stats import norm
from typing import Literal


OptionSide = Literal["CE", "PE"]


def _d1(S: float, K: float, r: float, sigma: float, T: float) -> float:
    """d1 parameter of Black-Scholes formula."""
    return (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))


def _d2(S: float, K: float, r: float, sigma: float, T: float) -> float:
    """d2 = d1 - sigma * sqrt(T)"""
    return _d1(S, K, r, sigma, T) - sigma * math.sqrt(T)


def price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionSide,
) -> float:
    """
    Black-Scholes theoretical option price.

    Parameters
    ----------
    S    : Spot price of the underlying
    K    : Strike price
    T    : Time to expiry in years (e.g. 7/365 for 7 days)
    r    : Risk-free rate (e.g. 0.065 for 6.5%)
    sigma: Implied volatility (e.g. 0.18 for 18%)
    option_type: "CE" or "PE"

    Returns
    -------
    Theoretical option price (float)
    """
    if T <= 0:
        # At expiry — intrinsic value only
        if option_type == "CE":
            return max(S - K, 0.0)
        else:
            return max(K - S, 0.0)

    d1 = _d1(S, K, r, sigma, T)
    d2 = _d2(S, K, r, sigma, T)

    if option_type == "CE":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def delta(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionSide,
) -> float:
    """
    Delta: ∂V/∂S — change in option price per ₹1 move in underlying.
    CE delta ∈ [0, 1], PE delta ∈ [-1, 0].
    ATM ≈ 0.5 (CE) or -0.5 (PE).
    """
    if T <= 0:
        if option_type == "CE":
            return 1.0 if S > K else 0.0
        else:
            return -1.0 if S < K else 0.0

    d1 = _d1(S, K, r, sigma, T)
    if option_type == "CE":
        return norm.cdf(d1)
    else:
        return norm.cdf(d1) - 1.0


def gamma(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
) -> float:
    """
    Gamma: ∂²V/∂S² — rate of change of delta.
    Same for CE and PE. High near ATM, especially near expiry.
    """
    if T <= 0:
        return 0.0
    d1 = _d1(S, K, r, sigma, T)
    return norm.pdf(d1) / (S * sigma * math.sqrt(T))


def theta(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionSide,
    days_in_year: int = 365,
) -> float:
    """
    Theta: ∂V/∂T — option price decay per calendar day (negative for long).
    Returned as ₹ per day (divided by days_in_year for daily decay).
    """
    if T <= 0:
        return 0.0

    d1 = _d1(S, K, r, sigma, T)
    d2 = _d2(S, K, r, sigma, T)

    term1 = -(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))

    if option_type == "CE":
        term2 = -r * K * math.exp(-r * T) * norm.cdf(d2)
    else:
        term2 = r * K * math.exp(-r * T) * norm.cdf(-d2)

    return (term1 + term2) / days_in_year


def vega(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
) -> float:
    """
    Vega: ∂V/∂σ — change in option price per 1% change in IV.
    Same for CE and PE.
    """
    if T <= 0:
        return 0.0
    d1 = _d1(S, K, r, sigma, T)
    return S * norm.pdf(d1) * math.sqrt(T) * 0.01  # per 1% IV move


def iv_approx(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: OptionSide,
    iterations: int = 100,
    tolerance: float = 1e-5,
) -> float:
    """
    Estimate Implied Volatility from market price using Newton-Raphson.
    Returns IV as a decimal (e.g. 0.18 for 18%).
    Returns NaN if it doesn't converge.
    """
    if T <= 0:
        return float("nan")

    sigma = 0.3  # initial guess: 30% IV
    for _ in range(iterations):
        p = price(S, K, T, r, sigma, option_type)
        v = vega(S, K, T, r, sigma)
        if abs(v) < 1e-10:
            break
        diff = p - market_price
        sigma -= diff / (v / 0.01)  # vega is per 1% so adjust
        sigma = max(0.001, min(sigma, 5.0))  # clamp to [0.1%, 500%]
        if abs(diff) < tolerance:
            return sigma

    return float("nan")


def greeks_summary(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionSide,
) -> dict:
    """Convenience: returns all Greeks as a dict."""
    return {
        "price": round(price(S, K, T, r, sigma, option_type), 2),
        "delta": round(delta(S, K, T, r, sigma, option_type), 4),
        "gamma": round(gamma(S, K, T, r, sigma), 6),
        "theta": round(theta(S, K, T, r, sigma, option_type), 4),
        "vega": round(vega(S, K, T, r, sigma), 4),
    }