"""Turning a comparison into one of four answers, only one of which should fail a build.

    REGRESSION   slower, by more than the threshold, with the interval clear of it
    FASTER       the same, in the other direction
    SAME         the interval is inside the threshold - measured, and small enough not to care
    NOISY        the interval is too wide to say anything

The rule that matters is the last one. A p-value alone cannot distinguish "no difference"
from "no information", and on a loaded machine the second is the common case. A gate that
reports SAME when it means NOISY is worse than useless: it is a green tick nobody has
earned.

## Why the threshold is on the interval, not the point estimate

A benchmark with enough samples will find that every change is statistically significant,
because no two builds of anything are exactly equal. Gating on p alone therefore fails every
build, gets muted within a week, and then catches nothing.

So a regression has to clear a **size** threshold as well: the *lower* end of the confidence
interval must be above it. That is deliberately the conservative end - it says the regression
is at least this big, not that it might be.

## And why a benchmark suite needs a correction

Run fifty benchmarks against identical code at a 5% false-alarm rate and you expect two and a
half false alarms every run. The suite-level report says how many alarms are expected by
chance, next to how many were found, because "3 regressions out of 50 benchmarks" means
nothing without it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from perf_hunter.stats import Comparison


class Call(StrEnum):
    REGRESSION = "regression"
    FASTER = "faster"
    SAME = "same"
    NOISY = "noisy"


#: Below this, a difference is real and not worth a build failure. 2% is a judgement call,
#: stated here rather than buried, and it is a flag.
DEFAULT_THRESHOLD = 0.02

#: An interval wider than this says the machine, not the code, decided the number.
DEFAULT_MAX_WIDTH = 0.10


@dataclass
class Verdict:
    name: str
    call: Call
    comparison: Comparison
    threshold: float = DEFAULT_THRESHOLD
    reason: str = ""

    @property
    def fails_build(self) -> bool:
        return self.call is Call.REGRESSION

    def as_row(self) -> dict:
        return {
            "name": self.name,
            "call": self.call.value,
            "reason": self.reason,
            **self.comparison.summary(),
        }


def judge(
    name: str,
    c: Comparison,
    threshold: float = DEFAULT_THRESHOLD,
    max_width: float = DEFAULT_MAX_WIDTH,
) -> Verdict:
    width = c.high - c.low
    if width > max_width:
        return Verdict(
            name,
            Call.NOISY,
            c,
            threshold,
            f"the interval spans {width * 100:.1f}%, wider than the {max_width * 100:.0f}% "
            f"limit - this says nothing about the code",
        )

    if c.low > 1.0 + threshold:
        return Verdict(
            name,
            Call.REGRESSION,
            c,
            threshold,
            f"at least {(c.low - 1) * 100:.1f}% slower, and the whole interval is above "
            f"the {threshold * 100:.0f}% threshold",
        )
    if c.high < 1.0 - threshold:
        return Verdict(
            name,
            Call.FASTER,
            c,
            threshold,
            f"at least {(1 - c.high) * 100:.1f}% faster",
        )
    return Verdict(
        name,
        Call.SAME,
        c,
        threshold,
        f"within {threshold * 100:.0f}%: the interval is "
        f"{(c.low - 1) * 100:+.1f}% to {(c.high - 1) * 100:+.1f}%",
    )


def expected_false_alarms(n_benchmarks: int, rate: float) -> float:
    """How many alarms a suite of this size produces on identical code.

    Printed beside the real count, because three regressions out of fifty benchmarks is not
    a finding if two and a half were coming anyway.
    """
    return n_benchmarks * rate
