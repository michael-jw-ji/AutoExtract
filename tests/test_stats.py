"""Paired bootstrap used by the promotion gate."""

from __future__ import annotations

import pytest

from evalgate.stats import micro_f1, paired_bootstrap


def docs(tp_pred_gold, n=100):
    return {i: tp_pred_gold for i in range(n)}


def test_identical_systems_have_an_interval_around_zero():
    a = {i: (i % 7, 7, 7) for i in range(150)}
    delta, lo, hi = paired_bootstrap(a, dict(a))
    assert delta == 0 and lo <= 0 <= hi


def test_a_clear_improvement_excludes_zero():
    inc = {i: (5, 10, 10) for i in range(150)}
    cand = {i: (8, 10, 10) for i in range(150)}
    delta, lo, hi = paired_bootstrap(cand, inc)
    assert delta == pytest.approx(30.0) and lo > 0


def test_a_small_noisy_gain_does_not_exclude_zero():
    # Mostly equal; the candidate wins a little on some documents, loses on others.
    inc = {i: (5, 10, 10) for i in range(150)}
    cand = {i: (6 if i % 3 == 0 else 4 if i % 3 == 1 else 5, 10, 10) for i in range(150)}
    cand[0] = (7, 10, 10)
    _, lo, _ = paired_bootstrap(cand, inc)
    assert lo <= 0


def test_deterministic_for_a_seed():
    inc = {i: (i % 5, 6, 6) for i in range(80)}
    cand = {i: ((i + 1) % 5, 6, 6) for i in range(80)}
    assert paired_bootstrap(cand, inc, seed=3) == paired_bootstrap(cand, inc, seed=3)


def test_refuses_different_document_sets():
    with pytest.raises(ValueError):
        paired_bootstrap({1: (1, 1, 1)}, {2: (1, 1, 1)})


def test_micro_f1():
    assert micro_f1({1: (3, 4, 6)}, [1]) == pytest.approx(0.6)
