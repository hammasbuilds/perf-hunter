"""Benchmarks to measure the measurer with, and a way to slow one down by a known amount.

For a false-alarm rate, the two sides must be *identical code* - then every regression found
is wrong by construction. For detection power, one side must be slower by an amount that is
known rather than estimated.

Slowing code down by exactly 5% is harder than it sounds. Adding a sleep changes the shape of
the distribution as well as its centre; adding a fixed amount of extra work is a fixed number
of nanoseconds, which is a different percentage on every machine. What works is to do a
proportion of the *same* work again: run the body, then run a measured fraction of it a
second time. The overhead is then the same kind of work, scales with the machine, and is a
known multiple.

The workloads are deliberately unalike. A tight arithmetic loop is nearly noiseless; building
dictionaries touches the allocator; sorting is memory-bound; regex work spends its time in C,
where the interpreter's own variance does not reach.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable

_WORDS = [f"word{i:04d}" for i in range(400)]
_TEXT = " ".join(random.Random(0).choices(_WORDS, k=2000))
_NUMBERS = [random.Random(1).random() for _ in range(2000)]
_PATTERN = re.compile(r"\bword0(\d{3})\b")


def arithmetic() -> int:
    """Nearly noiseless: no allocation, no syscalls, everything in registers."""
    total = 0
    for i in range(2000):
        total += (i * i) % 7
    return total


def build_dict() -> int:
    """Allocator-bound, so the garbage collector and arena growth show up here."""
    d = {}
    for i, w in enumerate(_WORDS):
        d[w] = i * 2
    return len(d)


def sort_numbers() -> float:
    """Memory-bound, and `sorted` is C - the interpreter's variance does not reach it."""
    # The suppression below is deliberate. `min()` is the faster way to get this value,
    # and that is exactly why it is wrong here: the sort IS the workload. Taking the
    # linter's advice would benchmark something else and leave the name lying.
    return sorted(_NUMBERS)[0]  # noqa: FURB192


def regex_scan() -> int:
    """Time spent inside a C extension, which `sys.settrace`-style overhead cannot perturb."""
    return len(_PATTERN.findall(_TEXT))


def string_join() -> int:
    return len("-".join(_WORDS))


WORKLOADS: dict[str, Callable[[], object]] = {
    "arithmetic": arithmetic,
    "build_dict": build_dict,
    "sort_numbers": sort_numbers,
    "regex_scan": regex_scan,
    "string_join": string_join,
}


def slowed(fn: Callable[[], object], factor: float) -> Callable[[], object]:
    """The same function, but about `factor` slower (0.05 = 5% slower).

    Implemented by repeating a fraction of the same work rather than sleeping or padding
    with a fixed constant: the overhead is then the same kind of work as the original, so it
    scales with the machine and stays a known *proportion* rather than a fixed number of
    nanoseconds that means 1% on one box and 20% on another.

    The fractional part is handled by doing the extra work on that share of calls, chosen
    deterministically rather than at random - a coin flip per call would add variance to the
    thing whose variance is being measured.
    """
    if factor <= 0:
        return fn

    whole = int(factor)
    frac = factor - whole
    period = round(1 / frac) if frac > 1e-9 else 0
    counter = [0]

    def wrapped():
        # Kept as lean as it can be: a list index beats a dict lookup, and the `whole`
        # loop is skipped entirely rather than running `range(0)`. The bookkeeping still
        # costs something, which is why the benchmark calibrates the *achieved* slowdown
        # instead of believing this number - at a 1% target the wrapper's own overhead is
        # a meaningful share of what it is trying to add.
        result = fn()
        if whole:
            for _ in range(whole):
                fn()
        if period:
            counter[0] += 1
            if counter[0] == period:
                counter[0] = 0
                fn()
        return result

    wrapped.__name__ = f"{getattr(fn, '__name__', 'fn')}_slowed_{factor}"
    return wrapped
