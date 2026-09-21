"""Measure the measurer: how often it cries wolf, and how small a change it can see.

Two numbers, and a performance gate needs both before anybody should let it fail a build:

    false alarms   run identical code against itself. Every regression found is wrong.
    power          inject a slowdown of known size. Every one missed is a regression shipped.

A gate tuned to never cry wolf detects nothing; one tuned to catch 1% regressions fails every
build by Thursday. The pair is the whole answer, and either alone is marketing.

The false-alarm trial is the more important of the two, because it needs no ground truth at
all - the ground truth is that there is nothing to find. `a` and `b` are the *same function
object*. Any REGRESSION or FASTER verdict is a false alarm by construction, with no
modelling assumption behind the claim.

The same trial also settles the schedule question, which is otherwise a matter of opinion:
run it under `sequential`, `interleaved` and `paired` and compare the rates.
"""

from __future__ import annotations

import json
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path

from perf_hunter import schedule as sched_mod
from perf_hunter import stats, timing, verdict, workloads


@dataclass
class Trial:
    workload: str
    schedule: str
    call: str
    percent: float
    ci_low: float
    ci_high: float
    p: float


@dataclass
class FalseAlarms:
    trials: list[Trial] = field(default_factory=list)

    def by_schedule(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for s in sched_mod.SCHEDULES:
            rows = [t for t in self.trials if t.schedule == s]
            if not rows:
                continue
            alarms = [t for t in rows if t.call in ("regression", "faster")]
            noisy = [t for t in rows if t.call == "noisy"]
            out[s] = {
                "trials": len(rows),
                "false_alarms": len(alarms),
                "rate": round(len(alarms) / len(rows), 4),
                "noisy": len(noisy),
                "worst_percent": round(max((abs(t.percent) for t in rows), default=0.0), 3),
            }
        return out


def false_alarm_trials(
    repeats: int = 6,
    samples: int = 25,
    schedules: tuple[str, ...] = sched_mod.SCHEDULES,
    threshold: float = verdict.DEFAULT_THRESHOLD,
    seed: int = 0,
    progress=None,
) -> FalseAlarms:
    """Compare each workload against itself, under each schedule, `repeats` times."""
    say = progress or (lambda *_: None)
    out = FalseAlarms()
    for name, fn in workloads.WORKLOADS.items():
        inner = timing.calibrate(fn)
        for s in schedules:
            for r in range(repeats):
                # The same object on both sides. There is nothing here to find.
                a_times, b_times = sched_mod.run(
                    fn, fn, inner=inner, samples=samples, schedule=s, seed=seed + r
                )
                c = stats.compare(a_times, b_times, seed=seed + r)
                v = verdict.judge(name, c, threshold=threshold)
                out.trials.append(
                    Trial(
                        name,
                        s,
                        v.call.value,
                        c.percent,
                        (c.low - 1) * 100,
                        (c.high - 1) * 100,
                        c.p,
                    )
                )
            say(f"false alarms: {name} / {s} done")
    return out


@dataclass
class Power:
    rows: list[dict] = field(default_factory=list)

    def by_effect(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for effect in sorted({r["injected_percent"] for r in self.rows}):
            rows = [r for r in self.rows if r["injected_percent"] == effect]
            caught = [r for r in rows if r["call"] == "regression"]
            missed = [r for r in rows if r["call"] in ("same", "faster")]
            achieved = sorted(r.get("achieved_percent", r["percent"]) for r in rows)
            out[f"{effect:g}%"] = {
                "trials": len(rows),
                "detected": len(caught),
                "power": round(len(caught) / len(rows), 4),
                "missed": len(missed),
                "noisy": len(rows) - len(caught) - len(missed),
                # The x-axis that means something. `injected` is what was asked for; this
                # is what the wrapper actually cost, measured separately and at greater
                # length. At a 1% target the two are not close.
                "median_achieved_percent": round(achieved[len(achieved) // 2], 2),
                "median_measured_percent": round(
                    sorted(r["percent"] for r in rows)[len(rows) // 2], 2
                ),
            }
        return out


def power_trials(
    effects=(0.01, 0.02, 0.05, 0.10, 0.25),
    repeats: int = 3,
    samples: int = 25,
    schedule: str = "paired",
    threshold: float = verdict.DEFAULT_THRESHOLD,
    seed: int = 0,
    progress=None,
) -> Power:
    say = progress or (lambda *_: None)
    p = Power()
    for name, fn in workloads.WORKLOADS.items():
        inner = timing.calibrate(fn)
        for effect in effects:
            slow = workloads.slowed(fn, effect)
            # What was actually injected, not what was asked for. The wrapper has its own
            # cost, and at a 1% target that cost is a large share of the target - so the
            # nominal figure would mislabel the whole power curve. Measured once, with
            # more samples than a trial uses, under the same schedule.
            cal_a, cal_b = sched_mod.run(
                fn,
                slow,
                inner=inner,
                samples=max(60, samples * 2),
                schedule=schedule,
                seed=seed + 991,
            )
            achieved = stats.compare(cal_a, cal_b, seed=seed + 991).percent
            for r in range(repeats):
                a_times, b_times = sched_mod.run(
                    fn,
                    slow,
                    inner=inner,
                    samples=samples,
                    schedule=schedule,
                    seed=seed + r,
                )
                c = stats.compare(a_times, b_times, seed=seed + r)
                v = verdict.judge(name, c, threshold=threshold)
                p.rows.append(
                    {
                        "workload": name,
                        "injected_percent": effect * 100,
                        "achieved_percent": achieved,
                        "call": v.call.value,
                        "percent": c.percent,
                        "ci_low": (c.low - 1) * 100,
                        "ci_high": (c.high - 1) * 100,
                    }
                )
            say(f"power: {name} at {effect * 100:g}% done")
    return p


def machine() -> dict:
    """Recorded with every result, because a timing number without it is not reproducible."""
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or "unknown",
        "cpu_count": __import__("os").cpu_count(),
    }


def run(
    repeats: int = 6,
    power_repeats: int = 3,
    samples: int = 25,
    threshold: float = verdict.DEFAULT_THRESHOLD,
    seed: int = 0,
    progress=None,
) -> dict:
    started = time.time()
    fa = false_alarm_trials(
        repeats=repeats,
        samples=samples,
        threshold=threshold,
        seed=seed,
        progress=progress,
    )
    pw = power_trials(
        repeats=power_repeats,
        samples=samples,
        threshold=threshold,
        seed=seed,
        progress=progress,
    )
    best = min(fa.by_schedule().items(), key=lambda kv: kv[1]["rate"], default=("", {}))
    return {
        "seconds": round(time.time() - started, 1),
        "machine": machine(),
        "settings": {
            "samples_per_side": samples,
            "threshold_percent": threshold * 100,
            "false_alarm_repeats": repeats,
            "power_repeats": power_repeats,
            "workloads": list(workloads.WORKLOADS),
        },
        "false_alarms": fa.by_schedule(),
        "best_schedule": best[0],
        "power": pw.by_effect(),
        "false_alarm_detail": [t.__dict__ for t in fa.trials],
        "power_detail": pw.rows,
    }


def text(res: dict) -> str:
    out = [
        "=" * 78,
        "PERF-HUNTER - how often it cries wolf, and what it can see",
        "=" * 78,
    ]
    m = res["machine"]
    out.append(f"{m['platform']}, {m['cpu_count']} cpus, python {m['python']}")
    s = res["settings"]
    out.append(
        f"{s['samples_per_side']} samples per side, {s['threshold_percent']:g}% threshold, "
        f"{len(s['workloads'])} workloads, {res['seconds']}s"
    )
    out.append("")
    out.append("-" * 78)
    out.append("FALSE ALARMS - identical code on both sides, so every alarm is wrong")
    out.append("-" * 78)
    out.append(
        f"{'schedule':<14}{'trials':>8}{'false alarms':>15}{'rate':>9}{'noisy':>8}{'worst':>9}"
    )
    for name, row in res["false_alarms"].items():
        out.append(
            f"{name:<14}{row['trials']:>8}{row['false_alarms']:>15}"
            f"{row['rate']:>8.0%}{row['noisy']:>8}{row['worst_percent']:>8.1f}%"
        )
    out.append("")
    out.append(f"  lowest false-alarm rate: {res['best_schedule']}")
    out.append("")
    out.append("-" * 78)
    out.append("POWER - a slowdown of known size, injected on one side")
    out.append("-" * 78)
    out.append(
        f"{'asked':<9}{'achieved':>10}{'trials':>8}{'detected':>10}{'power':>9}{'missed':>8}"
    )
    for eff, row in res["power"].items():
        out.append(
            f"{eff:<9}{row['median_achieved_percent']:>9.1f}%{row['trials']:>8}"
            f"{row['detected']:>10}{row['power']:>8.0%}{row['missed']:>8}"
        )
    out.append("")
    out.append("  'asked' is the nominal factor; 'achieved' is what the injection actually")
    out.append("  cost, measured separately. The second is the one that means anything.")
    return "\n".join(out)


def write_json(res: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, indent=2), encoding="utf-8")
