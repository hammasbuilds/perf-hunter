"""In what order the two versions are sampled, which decides more than the statistics do.

The obvious schedule is: time the old version thirty times, then the new one thirty times.
It is also the one that invents regressions, because everything that drifts on a machine
drifts *between* those two blocks and lands entirely on one side of the comparison.

    the CPU clocks down as it heats up          - the second block is slower
    another process starts                       - whichever block overlaps it is slower
    the allocator's arena grows                  - the second block is faster
    a background task finishes                   - the second block is faster

None of that is the code. All of it is perfectly reproducible as a "regression", and no
amount of statistical care recovers from it: the permutation test is being handed a real
difference, it just is not a difference about the software.

**Interleaving fixes it by construction.** Sample A, then B, then A, then B. Any drift slow
enough to matter now affects both sides almost equally, so it cancels in the comparison
instead of accumulating on one side.

`paired` goes further and randomises which of the two runs first within each round, so that
a systematic cost of "going first" - a cache line still warm from the previous round - does
not attach itself to one version for the whole run.

The difference this makes is measured rather than asserted: `bench.py` runs identical code
against itself under each schedule and counts how often each one reports a regression.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator


def sequential(samples: int) -> Iterator[str]:
    """All of A, then all of B. Included because it is what people do."""
    yield from ("a",) * samples
    yield from ("b",) * samples


def interleaved(samples: int) -> Iterator[str]:
    """A, B, A, B. Drift affects both sides equally."""
    for _ in range(samples):
        yield "a"
        yield "b"


def paired(samples: int, rng: random.Random) -> Iterator[str]:
    """A and B once each per round, in a random order within the round."""
    for _ in range(samples):
        pair = ["a", "b"]
        rng.shuffle(pair)
        yield from pair


SCHEDULES = ("sequential", "interleaved", "paired")


def order(name: str, samples: int, seed: int = 0) -> list[str]:
    if name == "sequential":
        return list(sequential(samples))
    if name == "interleaved":
        return list(interleaved(samples))
    if name == "paired":
        return list(paired(samples, random.Random(seed)))
    raise ValueError(f"unknown schedule: {name}")


def run(
    a: Callable[[], object],
    b: Callable[[], object],
    inner: int,
    samples: int = 30,
    schedule: str = "paired",
    seed: int = 0,
    time_fn: Callable[[Callable[[], object], int], float] | None = None,
) -> tuple[list[float], list[float]]:
    """Time both under the given schedule. Returns (a_times, b_times)."""
    from perf_hunter.timing import time_once

    measure = time_fn or time_once
    out: dict[str, list[float]] = {"a": [], "b": []}
    for which in order(schedule, samples, seed):
        out[which].append(measure(a if which == "a" else b, inner))
    return out["a"], out["b"]
