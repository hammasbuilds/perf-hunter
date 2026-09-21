"""Print the verdicts, with the interval next to every one of them.

The number a reader needs is not the p-value and not the point estimate. It is the interval:
"between 3% and 7% slower" is actionable, "5% slower, p=0.003" is a number and a ritual.

Benchmarks that could not be compared are printed too. A report that silently drops the
noisy ones tells you everything went fine.
"""

from __future__ import annotations

import json
from pathlib import Path

from perf_hunter.timing import Sample
from perf_hunter.verdict import Call, Verdict

ORDER = [Call.REGRESSION, Call.NOISY, Call.FASTER, Call.SAME]
MARK = {
    Call.REGRESSION: "SLOWER",
    Call.FASTER: "faster",
    Call.SAME: "same",
    Call.NOISY: "NOISY",
}


def samples_text(samples: list[Sample]) -> str:
    out = ["=" * 72, "TIMINGS", "=" * 72]
    out.append(f"{'benchmark':<28}{'median':>14}{'samples':>9}{'batch':>8}{'spread':>9}")
    out.append("-" * 72)
    for s in sorted(samples, key=lambda x: -x.median):
        out.append(f"{s.name:<28}{_dur(s.median):>14}{len(s):>9}{s.inner:>8}{s.spread:>8.1%}")
    out.append("")
    out.append("spread is the median absolute deviation over the median. Above about 5%")
    out.append("the machine is too busy for small differences to mean anything.")
    return "\n".join(out)


def _dur(seconds: float) -> str:
    if seconds >= 1:
        return f"{seconds:.3f} s"
    if seconds >= 1e-3:
        return f"{seconds * 1e3:.3f} ms"
    if seconds >= 1e-6:
        return f"{seconds * 1e6:.3f} us"
    return f"{seconds * 1e9:.1f} ns"


def verdicts_text(
    verdicts: list[Verdict],
    before: str = "HEAD",
    added: list[str] | None = None,
    removed: list[str] | None = None,
) -> str:
    out = ["=" * 78, f"PERF-HUNTER - against {before}", "=" * 78]
    if not verdicts:
        out.append("No benchmark exists in both revisions. Nothing was compared.")
        return "\n".join(out)

    counts = {c: sum(1 for v in verdicts if v.call is c) for c in ORDER}
    out.append("  ".join(f"{counts[c]} {MARK[c].lower()}" for c in ORDER if counts[c]) or "nothing")
    out.append("")
    out.append(f"{'benchmark':<26}{'change':>10}{'interval':>22}{'':>4}{'verdict'}")
    out.append("-" * 78)

    rank = {c: i for i, c in enumerate(ORDER)}
    for v in sorted(verdicts, key=lambda x: (rank[x.call], -abs(x.comparison.percent))):
        c = v.comparison
        interval = f"{(c.low - 1) * 100:+.1f}% to {(c.high - 1) * 100:+.1f}%"
        out.append(f"{v.name:<26}{c.percent:>+9.1f}%{interval:>22}    {MARK[v.call]}")

    failing = [v for v in verdicts if v.fails_build]
    if failing:
        out.append("")
        out.append("-" * 78)
        for v in failing:
            out.append(f"  {v.name}: {v.reason}")

    noisy = [v for v in verdicts if v.call is Call.NOISY]
    if noisy:
        out.append("")
        out.append(f"{len(noisy)} benchmarks could not be read at all:")
        for v in noisy:
            out.append(f"  {v.name}: {v.reason}")
        out.append("  More samples, or a quieter machine. This is not a pass.")

    if added:
        out.append("")
        out.append(f"new since {before}, so nothing to compare against: {', '.join(added)}")
    if removed:
        out.append(f"gone since {before}: {', '.join(removed)}")
    return "\n".join(out)


def write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
