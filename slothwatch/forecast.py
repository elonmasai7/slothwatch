"""Rung 1: expected amount + prediction interval for recurring items.
TabPFN (local, in-context, no training pipeline) when installed; robust median+MAD otherwise.
Which method ran is always recorded."""
from __future__ import annotations
import statistics
from dataclasses import dataclass
from datetime import date
from .config import Config

_REG = None


@dataclass
class Forecast:
    method: str
    expected: float
    lo: float
    hi: float
    n: int


def tabpfn_available() -> bool:
    try:
        import tabpfn  # noqa: F401
        return True
    except Exception:
        return False


def _widen(f: Forecast, cfg: Config) -> Forecast:
    half = cfg.interval_floor_pct * f.expected
    f.lo, f.hi = min(f.lo, f.expected - half), max(f.hi, f.expected + half)
    return f


def robust(amounts: list[float], cfg: Config, tag: str = "robust_mad") -> Forecast:
    w = amounts[-12:]
    med = statistics.median(w)
    mad = statistics.median([abs(x - med) for x in w])
    half = cfg.mad_k * 1.4826 * mad
    return _widen(Forecast(tag, med, med - half, med + half, len(amounts)), cfg)


def _features(dates: list[date], amounts: list[float], i: int, target: date | None = None):
    d = target or dates[i]
    prev_d = dates[i - 1]
    prev = amounts[max(0, i - 3):i]
    return [i, d.month, (d - prev_d).days, sum(prev) / len(prev)]


def forecast(dates: list[date], amounts: list[float], target: date, cfg: Config) -> Forecast:
    n = len(amounts)
    if not (cfg.use_tabpfn and n >= cfg.min_history and tabpfn_available()):
        return robust(amounts, cfg)
    try:
        global _REG
        import numpy as np
        from tabpfn import TabPFNRegressor
        if _REG is None:
            try:
                _REG = TabPFNRegressor(random_state=0)
            except TypeError:
                _REG = TabPFNRegressor()
        X = np.array([_features(dates, amounts, i) for i in range(1, n)], dtype=float)
        y = np.array(amounts[1:], dtype=float)
        xt = np.array([_features(dates, amounts, n - 1, target)], dtype=float)
        xt[0][0] = n
        _REG.fit(X, y)
        q = _REG.predict(xt, output_type="quantiles", quantiles=[cfg.interval_lo, cfg.interval_hi])
        mean = float(_REG.predict(xt)[0])
        return _widen(Forecast("tabpfn", mean, float(q[0][0]), float(q[1][0]), n), cfg)
    except Exception as e:  # never let the model break the nightly run
        return robust(amounts, cfg, tag=f"robust_mad(tabpfn_error:{type(e).__name__})")


def detect_creep(amounts: list[float], cfg: Config) -> float | None:
    """Several small rises that add up. Returns total rise % or None."""
    k = cfg.creep_len
    if len(amounts) < k:
        return None
    t = amounts[-k:]
    if all(b > a * 1.002 for a, b in zip(t, t[1:])):
        total = (t[-1] / t[0] - 1) * 100
        if total >= cfg.creep_total_pct:
            return total
    return None
