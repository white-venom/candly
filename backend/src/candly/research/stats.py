"""Hit-rate statistics: Wilson interval, binomial test, Benjamini-Hochberg, Beta-binomial shrinkage."""

from __future__ import annotations

import numpy as np
from scipy import stats as sps


def _z(level: float) -> float:
    return float(sps.norm.ppf(0.5 + level / 2.0))


def wilson_interval(hits, n, level: float = 0.95) -> tuple[np.ndarray, np.ndarray]:
    hits, n = np.asarray(hits, float), np.asarray(n, float)
    z = _z(level)
    with np.errstate(invalid="ignore", divide="ignore"):
        p = hits / n
        denom = 1.0 + z**2 / n
        centre = (p + z**2 / (2 * n)) / denom
        half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    low = np.where(hits <= 0, 0.0, np.clip(centre - half, 0.0, 1.0))
    high = np.where(hits >= n, 1.0, np.clip(centre + half, 0.0, 1.0))
    return low, high


def binomial_pvalue(hits: int, n: int, base_rate: float) -> float:
    """Two-sided exact binomial test of hits/n against the base rate."""
    if n <= 0:
        return 1.0
    return float(sps.binomtest(int(hits), int(n), float(np.clip(base_rate, 0.0, 1.0))).pvalue)


def benjamini_hochberg(pvalues) -> np.ndarray:
    """BH-adjusted q-values (step-up), same order as the input."""
    p = np.asarray(pvalues, float)
    m = p.size
    if m == 0:
        return p
    order = np.argsort(p)
    scaled = p[order] * m / np.arange(1, m + 1)
    q_sorted = np.minimum.accumulate(scaled[::-1])[::-1]
    q = np.empty(m)
    q[order] = np.clip(q_sorted, 0.0, 1.0)
    return q


def _beta_params(hits, n, base_rate, strength):
    hits, n, base = np.asarray(hits, float), np.asarray(n, float), np.asarray(base_rate, float)
    a = hits + strength * base
    b = (n - hits) + strength * (1.0 - base)
    return np.maximum(a, 1e-9), np.maximum(b, 1e-9)


def beta_posterior(hits, n, base_rate, strength: float):
    """Posterior mean with a Beta prior centred on the base rate, worth `strength` observations."""
    a, b = _beta_params(hits, n, base_rate, strength)
    return a / (a + b)


def beta_interval(hits, n, base_rate, strength: float, level: float = 0.95):
    a, b = _beta_params(hits, n, base_rate, strength)
    tail = (1.0 - level) / 2.0
    return sps.beta.ppf(tail, a, b), sps.beta.ppf(1.0 - tail, a, b)
