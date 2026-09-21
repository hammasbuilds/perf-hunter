"""The statistics and the rule, on synthetic numbers where the answer is known.

Timing tests would be flaky here by construction, so these feed in distributions directly.
The real machine's behaviour is measured by `perf-hunter self-check` and reported in
docs/RESULTS.md, which is the right place for a number that depends on the hardware.
"""

from __future__ import annotations

import random

import pytest

from perf_hunter.stats import Comparison, bootstrap_ratio, compare, permutation_p
from perf_hunter.verdict import Call, expected_false_alarms, judge


def noisy(centre: float, n: int, spread: float, seed: int) -> list[float]:
    """A plausible timing distribution: a floor, and a long right tail."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        v = centre * (1 + abs(rng.gauss(0, spread)))
        if rng.random() < 0.05:
            v *= 1 + rng.random()  # the occasional interruption
        out.append(v)
    return out


def test_identical_distributions_are_called_same():
    a = noisy(1.0, 60, 0.01, 1)
    b = noisy(1.0, 60, 0.01, 2)
    v = judge("x", compare(a, b, rounds=500))
    assert v.call is Call.SAME


def test_a_clear_slowdown_is_called_a_regression():
    a = noisy(1.0, 60, 0.01, 1)
    b = noisy(1.20, 60, 0.01, 2)
    v = judge("x", compare(a, b, rounds=500))
    assert v.call is Call.REGRESSION
    assert 15 < v.comparison.percent < 25


def test_a_clear_speedup_is_called_faster():
    a = noisy(1.20, 60, 0.01, 1)
    b = noisy(1.0, 60, 0.01, 2)
    v = judge("x", compare(a, b, rounds=500))
    assert v.call is Call.FASTER


def test_a_real_but_tiny_difference_does_not_fail_a_build():
    """With enough samples every difference is significant. A 0.5% regression is real,
    measurable, and not worth anybody's afternoon."""
    a = noisy(1.000, 200, 0.002, 1)
    b = noisy(1.005, 200, 0.002, 2)
    v = judge("x", compare(a, b, rounds=500), threshold=0.02)
    assert v.call is Call.SAME
    assert not v.fails_build


def test_a_wide_interval_is_noisy_not_same():
    """The distinction that matters. "No difference" and "no information" look identical
    in a p-value, and on a loaded machine the second is the common case."""
    a = noisy(1.0, 12, 0.30, 1)
    b = noisy(1.0, 12, 0.30, 2)
    v = judge("x", compare(a, b, rounds=500))
    assert v.call is Call.NOISY
    assert "says nothing about the code" in v.reason


def test_noisy_is_reported_before_a_threshold_is_applied():
    """A huge but unreadable difference must not be called a regression."""
    a = noisy(1.0, 10, 0.5, 3)
    b = noisy(1.3, 10, 0.5, 4)
    v = judge("x", compare(a, b, rounds=500))
    assert v.call is Call.NOISY


def test_the_threshold_is_applied_to_the_interval_not_the_point():
    """The point estimate is over the threshold; the interval is not clear of it, so the
    evidence does not support failing a build."""
    a = noisy(1.000, 40, 0.03, 5)
    b = noisy(1.025, 40, 0.03, 6)
    c = compare(a, b, rounds=800)
    v = judge("x", c, threshold=0.02)
    if c.low <= 1.02:
        assert v.call is not Call.REGRESSION


def test_permutation_p_is_never_zero():
    """A p of exactly 0 claims the split is impossible, which no finite shuffling shows.

    The two sides are spread out rather than two constants. A pool of 30 identical `1.0`s
    and 30 identical `5.0`s makes the median a step function - any split other than exactly
    15/15 puts both halves at opposite extremes and reports the full difference - so a
    two-valued fixture measures that artefact instead of the property.
    """
    a = [1.0 + i * 0.001 for i in range(30)]
    b = [5.0 + i * 0.001 for i in range(30)]
    p = permutation_p(a, b, 200, random.Random(0))
    assert p > 0
    assert p == pytest.approx(1 / 201)


def test_permutation_p_is_high_when_there_is_nothing_there():
    a = noisy(1.0, 40, 0.01, 7)
    b = noisy(1.0, 40, 0.01, 8)
    assert permutation_p(a, b, 400, random.Random(0)) > 0.05


def test_the_bootstrap_interval_brackets_the_ratio():
    a = noisy(1.0, 50, 0.02, 9)
    b = noisy(1.1, 50, 0.02, 10)
    lo, hi = bootstrap_ratio(a, b, 600, random.Random(0))
    assert lo < 1.1 < hi


def test_a_narrower_interval_comes_from_more_samples():
    small = compare(noisy(1.0, 10, 0.05, 1), noisy(1.0, 10, 0.05, 2), rounds=400)
    large = compare(noisy(1.0, 200, 0.05, 1), noisy(1.0, 200, 0.05, 2), rounds=400)
    assert (large.high - large.low) < (small.high - small.low)


def test_the_same_seed_gives_the_same_verdict():
    a, b = noisy(1.0, 40, 0.02, 1), noisy(1.03, 40, 0.02, 2)
    assert compare(a, b, rounds=300, seed=7) == compare(a, b, rounds=300, seed=7)


def test_an_empty_side_is_not_a_result():
    c = compare([], [1.0, 2.0])
    assert isinstance(c, Comparison)
    assert c.ratio == 1.0


def test_expected_false_alarms_scales_with_suite_size():
    """Three regressions out of fifty benchmarks is not a finding if two were coming anyway."""
    assert expected_false_alarms(50, 0.05) == pytest.approx(2.5)
    assert expected_false_alarms(1, 0.05) == pytest.approx(0.05)
