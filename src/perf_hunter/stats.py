"""Deciding whether two sets of timings differ, without pretending they are normal.

Benchmark timings are not normally distributed. They have a floor - the code cannot run
faster than it can run - and a long right tail of interruptions. The mean sits inside that
tail and a t-test on it assumes a shape the data does not have.

So: **the median**, compared by **permutation**, with a **bootstrap interval** on the ratio.

The permutation test asks one question with no distributional assumption in it. If the label
"before" and "after" meant nothing, how often would shuffling the labels produce a difference
at least this large? That share is the p-value, and it is computed by actually shuffling.

The bootstrap interval answers the question anybody actually has, which is not "is there a
difference" but **"how big, and how sure"**. On a fast enough machine every difference is
significant; a 0.3% regression with a confidence interval of 0.1%-0.5% is real, measurable
and not worth anybody's afternoon.

Both are seeded, so a verdict is reproducible.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass


@dataclass
class Comparison:
    ratio: float
    """Median of `after` over median of `before`. 1.05 means 5% slower."""

    low: float
    high: float
    """Bootstrap interval on that ratio."""

    p: float
    """Permutation test on the difference of medians."""

    n_before: int
    n_after: int

    @property
    def percent(self) -> float:
        return (self.ratio - 1.0) * 100.0

    def summary(self) -> dict:
        return {
            "ratio": round(self.ratio, 5),
            "percent": round(self.percent, 3),
            "ci_low_percent": round((self.low - 1.0) * 100.0, 3),
            "ci_high_percent": round((self.high - 1.0) * 100.0, 3),
            "p": round(self.p, 5),
            "n_before": self.n_before,
            "n_after": self.n_after,
        }


def permutation_p(
    before: list[float], after: list[float], rounds: int, rng: random.Random
) -> float:
    """Two-sided p for a difference in medians, by shuffling the labels.

    The `+1`s are not decoration. A p-value of exactly 0 claims the observed split is
    impossible under the null, which no finite number of shuffles can establish; the add-one
    correction reports `1/(rounds+1)` instead, which is what was actually measured.
    """
    observed = abs(statistics.median(after) - statistics.median(before))
    pool = before + after
    n = len(before)
    at_least = 0
    for _ in range(rounds):
        rng.shuffle(pool)
        diff = abs(statistics.median(pool[n:]) - statistics.median(pool[:n]))
        if diff >= observed:
            at_least += 1
    return (at_least + 1) / (rounds + 1)


def bootstrap_ratio(
    before: list[float],
    after: list[float],
    rounds: int,
    rng: random.Random,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Percentile interval on median(after) / median(before)."""
    ratios = []
    nb, na = len(before), len(after)
    for _ in range(rounds):
        b = statistics.median([before[rng.randrange(nb)] for _ in range(nb)])
        a = statistics.median([after[rng.randrange(na)] for _ in range(na)])
        ratios.append(a / b if b > 0 else 1.0)
    ratios.sort()
    lo = ratios[max(0, int(len(ratios) * alpha / 2) - 1)]
    hi = ratios[min(len(ratios) - 1, int(len(ratios) * (1 - alpha / 2)))]
    return lo, hi


def compare(
    before: list[float],
    after: list[float],
    rounds: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> Comparison:
    if not before or not after:
        return Comparison(1.0, 1.0, 1.0, 1.0, len(before), len(after))
    rng = random.Random(seed)
    mb, ma = statistics.median(before), statistics.median(after)
    ratio = ma / mb if mb > 0 else 1.0
    lo, hi = bootstrap_ratio(before, after, rounds, rng, alpha)
    # A fresh generator: the bootstrap consumed the other one, and a permutation test that
    # depends on how many bootstrap rounds ran is not reproducible in any useful sense.
    p = permutation_p(list(before), list(after), rounds, random.Random(seed + 1))
    return Comparison(ratio, lo, hi, p, len(before), len(after))
