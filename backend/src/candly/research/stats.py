"""Hit-rate statistics: Wilson interval, cluster-robust test, Benjamini-Hochberg, Beta-binomial
shrinkage, calibration error."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps


def _z(level: float) -> float:
    return float(sps.norm.ppf(0.5 + level / 2.0))


def overlap_cluster_ids(group, start, end) -> np.ndarray:
    """Cluster id per event (unique across groups, input order).

    Within a group, events whose outcome windows (start, end] overlap in time are chained into one
    cluster: sorted by start, an event opens a new cluster only if it starts at or after the latest
    end seen so far. Windows that merely touch (the next event starts where this one ends) don't
    overlap, because their returns share no bar.
    """
    group, start, end = (np.asarray(x) for x in (group, start, end))
    if group.size == 0:
        return np.empty(0, dtype=np.int64)
    order = np.lexsort((start, group))
    g, s, e = group[order], start[order], end[order]
    first = np.r_[True, g[1:] != g[:-1]]
    run_max = pd.Series(e).groupby(g).cummax().to_numpy()
    prev_max = np.r_[run_max[:1], run_max[:-1]]
    new = first | (s >= prev_max)
    ids = np.empty(len(order), dtype=np.int64)
    ids[order] = np.cumsum(new) - 1
    return ids


def cluster_robust_z(resid, cluster) -> tuple[float, int]:
    """z = sum(resid) / sqrt(sum over clusters of (sum of resid in the cluster)^2), and the cluster count.

    `resid` is hit - base per event. z is NaN when the denominator is 0.
    """
    resid, cluster = np.asarray(resid, float), np.asarray(cluster)
    if resid.size == 0:
        return float("nan"), 0
    _, inverse = np.unique(cluster, return_inverse=True)
    sums = np.bincount(inverse, weights=resid)
    var = float((sums**2).sum())
    return (float(resid.sum() / np.sqrt(var)) if var > 0 else float("nan")), int(sums.size)


def z_pvalue(z, two_sided) -> np.ndarray:
    """Normal p-value: upper tail (H1: hit rate above base) or two-sided. NaN z -> 1."""
    z = np.asarray(z, float)
    two_sided = np.broadcast_to(np.asarray(two_sided, bool), z.shape)
    p = np.where(two_sided, 2.0 * sps.norm.sf(np.abs(z)), sps.norm.sf(z))
    return np.where(np.isnan(z), 1.0, np.clip(p, 0.0, 1.0))


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


def expected_calibration_error(p, y, bins: int, binning: str = "quantile") -> float | None:
    """sum over bins of (bin share) * |mean forecast - observed rate|.

    "quantile" bins hold equal numbers of forecasts (sorted by p); "uniform" bins split [0, 1] evenly.
    """
    p, y = np.asarray(p, float), np.asarray(y, float)
    if p.size == 0:
        return None
    if binning == "quantile":
        groups = np.array_split(np.argsort(p, kind="stable"), min(bins, p.size))
    else:
        idx = np.clip((p * bins).astype(int), 0, bins - 1)
        groups = [np.flatnonzero(idx == b) for b in range(bins)]
    return float(sum(g.size / p.size * abs(p[g].mean() - y[g].mean()) for g in groups if g.size))


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
