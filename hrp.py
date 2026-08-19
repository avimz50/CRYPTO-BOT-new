#!/usr/bin/env python3
"""hrp.py - Hierarchical Risk Parity, pure Python. Lopez de Prado (2016).
An ALLOCATION rule, not a forecast: given assets you already hold, it decides
how much of each. Cluster by correlation, reorder, split recursively."""
from __future__ import annotations
import math, random


def cov_corr(returns):
    """returns[i] = series of asset i. Gives (covariance, correlation).
    A zero-variance asset gets zero correlation, not a divide-by-zero."""
    n, m = len(returns), len(returns[0])
    if m < 2:
        raise ValueError("need at least two observations")
    mean = [sum(r) / m for r in returns]
    dev = [[x - mean[i] for x in returns[i]] for i in range(n)]
    cov = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            c = sum(dev[i][k] * dev[j][k] for k in range(m)) / (m - 1)
            cov[i][j] = cov[j][i] = c
    sd = [math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0 for i in range(n)]
    corr = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if sd[i] > 0 and sd[j] > 0:
                corr[i][j] = max(-1.0, min(1.0, cov[i][j] / (sd[i] * sd[j])))
            else:
                corr[i][j] = 1.0 if i == j else 0.0
    return cov, corr


def corr_distance(corr):
    """d(i,j) = sqrt(0.5*(1-rho)) - a proper metric on correlations."""
    n = len(corr)
    return [[math.sqrt(max(0.0, 0.5 * (1.0 - corr[i][j]))) for j in range(n)]
            for i in range(n)]


def quasi_diag_order(dist):
    """Single-linkage agglomeration. Each merge concatenates member lists, so
    the final list is the dendrogram leaf order: similar assets sit adjacent."""
    n = len(dist)
    if n <= 1:
        return list(range(n))
    clusters = {i: [i] for i in range(n)}

    def link(a, b):
        return min(dist[i][j] for i in a for j in b)

    while len(clusters) > 1:
        keys = sorted(clusters)
        best, bd = None, float('inf')
        for x in range(len(keys)):
            for y in range(x + 1, len(keys)):
                d = link(clusters[keys[x]], clusters[keys[y]])
                if d < bd:
                    bd, best = d, (keys[x], keys[y])
        a, b = best
        clusters[a] = clusters[a] + clusters[b]
        del clusters[b]
    return next(iter(clusters.values()))


def _inv_var_weights(cov, idx):
    iv = [1.0 / cov[i][i] if cov[i][i] > 0 else 0.0 for i in idx]
    s = sum(iv)
    return [x / s for x in iv] if s > 0 else [1.0 / len(idx)] * len(idx)


def _cluster_var(cov, idx):
    """Variance of the inverse-variance portfolio built from this cluster."""
    w = _inv_var_weights(cov, idx)
    return sum(w[a] * w[b] * cov[i][j]
               for a, i in enumerate(idx) for b, j in enumerate(idx))


def hrp_weights(cov, order):
    """Split the ordered list in half repeatedly; each half keeps a share of
    its parent inversely proportional to its own variance."""
    w = {i: 1.0 for i in order}
    groups = [order]
    while groups:
        nxt = []
        for g in groups:
            if len(g) < 2:
                continue
            mid = len(g) // 2
            left, right = g[:mid], g[mid:]
            vl, vr = _cluster_var(cov, left), _cluster_var(cov, right)
            tot = vl + vr
            alpha = 0.5 if tot <= 0 else 1.0 - vl / tot
            for i in left:
                w[i] *= alpha
            for i in right:
                w[i] *= (1.0 - alpha)
            nxt += [left, right]
        groups = nxt
    s = sum(w.values())
    return {i: v / s for i, v in w.items()} if s > 0 else \
           {i: 1.0 / len(order) for i in order}


def hrp(returns):
    """Full pipeline. Weights in the ORIGINAL asset order."""
    n = len(returns)
    if n == 1:
        return [1.0]
    cov, corr = cov_corr(returns)
    order = quasi_diag_order(corr_distance(corr))
    wd = hrp_weights(cov, order)
    return [wd.get(i, 0.0) for i in range(n)]


def inverse_variance(returns):
    """Risk weighting WITHOUT the clustering. Any HRP gain over this is what
    the tree actually contributes - the comparison the article never makes."""
    cov, _ = cov_corr(returns)
    return _inv_var_weights(cov, list(range(len(returns))))


def _selftest():
    """Plant three correlated blocks and check the ordering recovers them.
    A method that cannot find structure that is definitely there proves
    nothing when it reports structure on real data."""
    rnd = random.Random(1)
    n_obs, blocks = 400, [(0, 4), (4, 8), (8, 12)]
    factors = [[rnd.gauss(0, 1) for _ in range(n_obs)] for _ in blocks]
    rets = []
    for a in range(12):
        b = next(k for k, (lo, hi) in enumerate(blocks) if lo <= a < hi)
        vol = 0.5 + a * 0.15
        rets.append([0.9 * factors[b][t] + vol * rnd.gauss(0, 1)
                     for t in range(n_obs)])
    cov, corr = cov_corr(rets)
    order = quasi_diag_order(corr_distance(corr))
    print("planted blocks : [0-3] [4-7] [8-11]")
    print("recovered order:", order)
    contiguous = all(
        max(pos) - min(pos) == len(pos) - 1
        for pos in ([order.index(a) for a in range(lo, hi)]
                    for lo, hi in blocks))
    print("blocks contiguous in the ordering:", contiguous)
    w = hrp(rets)
    print("\nweights sum to 1:", abs(sum(w) - 1) < 1e-9)
    print("all weights non-negative:", all(x >= 0 for x in w))
    lo_vol, hi_vol = sum(w[:4]) / 4, sum(w[8:]) / 4
    print(f"avg weight, low-vol block  {lo_vol:.4f}")
    print(f"avg weight, high-vol block {hi_vol:.4f}")
    print("risk-based tilt away from the volatile block:", lo_vol > hi_vol)


if __name__ == '__main__':
    _selftest()
