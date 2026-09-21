"""Command line: compare two revisions of a benchmark file, or measure the measurer."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from perf_hunter import bench as bench_mod
from perf_hunter import report, schedule, stats, timing, verdict


def _say(msg: str) -> None:
    print(f"  .. {msg}", file=sys.stderr, flush=True)


def load_benchmarks(path: Path, name: str = "ph_bench") -> dict[str, Callable[[], object]]:
    """Every zero-argument `bench_*` callable in a module loaded from `path`."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    out = {}
    for attr in dir(module):
        if not attr.startswith("bench_"):
            continue
        fn = getattr(module, attr)
        if callable(fn):
            out[attr[len("bench_") :]] = fn
    return out


def checkout(repo: Path, ref: str, rel: Path, into: Path) -> Path:
    """Write `rel` as it was at `ref` into `into`. Never touches the working tree."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{rel.as_posix()}"],
        capture_output=True,
        text=True,
        check=False,
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git show {ref}:{rel} failed: {proc.stderr.strip()[:200]}")
    into.write_text(proc.stdout, encoding="utf-8")
    return into


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="perf-hunter",
        description="Find performance regressions, and know how often it cries wolf.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compare", help="one benchmark file, two git revisions")
    c.add_argument("file", type=Path, help="a module with bench_* functions")
    c.add_argument("--repo", type=Path, default=Path("."))
    c.add_argument("--before", default="HEAD", help="git ref for the old side")
    c.add_argument("--samples", type=int, default=30)
    c.add_argument("--schedule", default="paired", choices=schedule.SCHEDULES)
    c.add_argument(
        "--threshold",
        type=float,
        default=verdict.DEFAULT_THRESHOLD * 100,
        help="percent below which a real difference is not worth failing a build "
        f"(default {verdict.DEFAULT_THRESHOLD * 100:g})",
    )
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--json", type=Path)
    c.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="exit 1 if any regression is called",
    )
    c.add_argument("--quiet", action="store_true")

    s = sub.add_parser("run", help="time the benchmarks once, no comparison")
    s.add_argument("file", type=Path)
    s.add_argument("--samples", type=int, default=30)
    s.add_argument("--json", type=Path)

    b = sub.add_parser(
        "self-check",
        help="false alarms on identical code, and power against known slowdowns",
    )
    b.add_argument("--repeats", type=int, default=6)
    b.add_argument("--power-repeats", type=int, default=3)
    b.add_argument("--samples", type=int, default=25)
    b.add_argument("--threshold", type=float, default=verdict.DEFAULT_THRESHOLD * 100)
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--json", type=Path)
    b.add_argument("--quiet", action="store_true")

    a = p.parse_args(argv)

    if a.cmd == "self-check":
        res = bench_mod.run(
            repeats=a.repeats,
            power_repeats=a.power_repeats,
            samples=a.samples,
            threshold=a.threshold / 100,
            seed=a.seed,
            progress=None if a.quiet else _say,
        )
        print(bench_mod.text(res))
        if a.json:
            bench_mod.write_json(res, a.json)
            print(f"\nwrote {a.json}")
        return 0

    if not a.file.exists():
        print(f"no such file: {a.file}", file=sys.stderr)
        return 2

    if a.cmd == "run":
        fns = load_benchmarks(a.file)
        if not fns:
            print(f"no bench_* functions in {a.file}", file=sys.stderr)
            return 1
        samples = [timing.measure(fn, name, samples=a.samples) for name, fn in fns.items()]
        print(report.samples_text(samples))
        if a.json:
            report.write_json({"samples": [s.summary() for s in samples]}, a.json)
        return 0

    # compare
    import tempfile

    try:
        rel = a.file.resolve().relative_to(a.repo.resolve())
    except ValueError:
        print(f"{a.file} is not inside {a.repo}", file=sys.stderr)
        return 2

    new = load_benchmarks(a.file, "ph_new")
    if not new:
        print(f"no bench_* functions in {a.file}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="ph-") as tmp:
        old_path = Path(tmp) / a.file.name
        try:
            checkout(a.repo, a.before, rel, old_path)
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 2
        old = load_benchmarks(old_path, "ph_old")

    shared = sorted(set(old) & set(new))
    if not shared:
        print(
            f"no benchmark exists in both revisions (before: {sorted(old)}, now: {sorted(new)})",
            file=sys.stderr,
        )
        return 1

    verdicts = []
    for name in shared:
        if not a.quiet:
            _say(f"{name}: {a.samples} samples per side, {a.schedule}")
        inner = timing.calibrate(new[name])
        before, after = schedule.run(
            old[name],
            new[name],
            inner=inner,
            samples=a.samples,
            schedule=a.schedule,
            seed=a.seed,
        )
        comparison = stats.compare(before, after, seed=a.seed)
        verdicts.append(verdict.judge(name, comparison, threshold=a.threshold / 100))

    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    print(report.verdicts_text(verdicts, a.before, added, removed))
    if a.json:
        report.write_json(
            {
                "before": a.before,
                "schedule": a.schedule,
                "machine": bench_mod.machine(),
                "verdicts": [v.as_row() for v in verdicts],
                "added": added,
                "removed": removed,
            },
            a.json,
        )
        print(f"\nwrote {a.json}")

    if a.fail_on_regression and any(v.fails_build for v in verdicts):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
