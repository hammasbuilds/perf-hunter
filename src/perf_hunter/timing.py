"""Measure how long something takes, in a way that can be compared later.

A single timing is not a measurement. It is one draw from a distribution whose spread is set
by things that have nothing to do with the code: which core the OS picked, what the cache
held, whether the CPU had clocked down, what else was running. Comparing two single timings
is comparing two coin flips.

So a sample here is a **batch**: the callable is run `inner` times and the total divided by
`inner`. Batching pulls the per-call time above clock resolution and averages away the
cheapest noise; repeating the batch `samples` times leaves a distribution to reason about.

Three things are done because leaving them out changes the answer:

**Warmup runs are discarded.** The first execution of anything pays for imports, first-touch
allocation and a cold branch predictor. Including it makes every "before" look slow.

**The garbage collector is disabled during a batch and re-enabled after.** A collection that
lands inside one batch and not the other is a step change of several percent attributed to
whichever side was unlucky.

**`perf_counter` is the clock**, not `process_time`. The question is wall-clock cost, and
`process_time` hides everything the process waits for.
"""

from __future__ import annotations

import gc
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Sample:
    """One benchmark's timings, in seconds per call."""

    name: str
    times: list[float] = field(default_factory=list)
    inner: int = 1
    warmup: int = 0

    def __len__(self) -> int:
        return len(self.times)

    @property
    def median(self) -> float:
        return statistics.median(self.times) if self.times else 0.0

    @property
    def mean(self) -> float:
        return statistics.fmean(self.times) if self.times else 0.0

    @property
    def spread(self) -> float:
        """Median absolute deviation over the median - a scale-free noise figure.

        Standard deviation is the wrong summary here: one garbage collection or one
        scheduler hiccup drags it up by a factor, which then reads as "this benchmark is
        noisy" when 99 of 100 samples agreed.
        """
        if len(self.times) < 2:
            return 0.0
        med = self.median
        if med <= 0:
            return 0.0
        mad = statistics.median([abs(t - med) for t in self.times])
        return mad / med

    def summary(self) -> dict:
        return {
            "name": self.name,
            "samples": len(self.times),
            "inner": self.inner,
            "median_s": self.median,
            "mean_s": self.mean,
            "relative_mad": round(self.spread, 5),
        }


def time_once(fn: Callable[[], object], inner: int) -> float:
    """Seconds per call, averaged over `inner` calls with the collector held off."""
    enabled = gc.isenabled()
    gc.disable()
    try:
        start = time.perf_counter()
        for _ in range(inner):
            fn()
        elapsed = time.perf_counter() - start
    finally:
        if enabled:
            gc.enable()
    return elapsed / inner


def calibrate(fn: Callable[[], object], target: float = 0.001, cap: int = 1_000_000) -> int:
    """How many calls to batch so a batch lasts about `target` seconds.

    Timing a microsecond-scale function one call at a time measures the clock, not the
    function: `perf_counter` resolution on Windows is around 100ns, so a 300ns call is
    three ticks give or take one, and "give or take one" is 33%.
    """
    inner = 1
    while inner < cap:
        elapsed = time_once(fn, inner) * inner
        if elapsed >= target:
            return inner
        # Aim straight at the target rather than doubling, but never trust a zero.
        if elapsed <= 0:
            inner *= 8
        else:
            inner = max(inner + 1, min(int(inner * target / elapsed) + 1, inner * 16))
    return cap


def measure(
    fn: Callable[[], object],
    name: str = "",
    samples: int = 30,
    inner: int = 0,
    warmup: int = 3,
    target: float = 0.001,
) -> Sample:
    for _ in range(warmup):
        fn()
    if inner <= 0:
        inner = calibrate(fn, target)
    s = Sample(name=name or getattr(fn, "__name__", "benchmark"), inner=inner, warmup=warmup)
    for _ in range(samples):
        s.times.append(time_once(fn, inner))
    return s
