"""Paired bootstrap for "is this F1 change real, or noise at this sample size?"

The gate used a fixed +2.0 pp margin. Measured at n = 189 it promoted a change
whose 95% interval included zero and rejected one with equally strong
evidence (RESULTS.md, "Is the gate's +2.0 pp margin the right test?"). A
paired bootstrap answers the question the margin was standing in for.

Paired: each resample draws the SAME filings for both systems, so filing
difficulty cancels out and only the difference between the systems varies.
"""

from __future__ import annotations

import random

Counts = dict[int, tuple[int, int, int]]   # doc id -> (tp, n_pred, n_gold)


def micro_f1(counts: Counts, ids) -> float:
    tp = sum(counts[i][0] for i in ids)
    n_pred = sum(counts[i][1] for i in ids)
    n_gold = sum(counts[i][2] for i in ids)
    return 0.0 if n_pred + n_gold == 0 else 2 * tp / (n_pred + n_gold)


def paired_bootstrap(cand: Counts, inc: Counts, resamples: int = 2000,
                     seed: int = 0) -> tuple[float, float, float]:
    """(delta, low, high) of candidate minus incumbent micro F1, in points.

    The interval is the central 95% of resampled deltas. Deterministic for a
    given seed, so a gate decision can be re-derived exactly.
    """
    ids = sorted(set(cand) & set(inc))
    if len(ids) != len(cand) or len(ids) != len(inc):
        raise ValueError("candidate and incumbent were scored on different documents")
    rng = random.Random(seed)
    deltas = []
    for _ in range(resamples):
        sample = [ids[rng.randrange(len(ids))] for _ in ids]
        deltas.append(micro_f1(cand, sample) - micro_f1(inc, sample))
    deltas.sort()
    lo, hi = deltas[int(0.025 * resamples)], deltas[int(0.975 * resamples) - 1]
    delta = micro_f1(cand, ids) - micro_f1(inc, ids)
    return delta * 100, lo * 100, hi * 100
