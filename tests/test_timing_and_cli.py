"""Timing mechanics, schedules, and the command line.

Nothing here asserts that one piece of code is faster than another — a timing assertion on a
shared CI runner is flaky by construction, which is the exact failure this repository exists
to measure. These check the machinery: that a batch is a batch, that a schedule interleaves,
that an empty comparison is refused.
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

import pytest

from perf_hunter import schedule, timing, workloads
from perf_hunter.cli import checkout, load_benchmarks, main

BENCH_FILE = """\
def bench_cheap():
    return sum(range(50))


def bench_other():
    return len("x" * 100)


def not_a_benchmark():
    raise AssertionError("must never be called")


VALUE = 3
"""


def test_a_batch_divides_by_the_batch_size():
    calls = []
    timing.time_once(lambda: calls.append(1), inner=17)
    assert len(calls) == 17


def test_the_collector_is_restored_afterwards():
    import gc

    assert gc.isenabled()
    timing.time_once(lambda: None, inner=3)
    assert gc.isenabled()


def test_the_collector_is_restored_even_if_the_callable_raises():
    import gc

    def boom():
        raise ValueError("no")

    with pytest.raises(ValueError):
        timing.time_once(boom, inner=1)
    assert gc.isenabled()


def test_calibrate_picks_a_batch_size_of_at_least_one():
    inner = timing.calibrate(lambda: sum(range(10)), target=0.0005)
    assert inner >= 1


def test_calibrate_does_not_run_forever_on_a_slow_callable():
    """A callable already slower than the target needs a batch of exactly one."""

    def slow():
        sum(range(200_000))

    assert timing.calibrate(slow, target=1e-6) == 1


def test_measure_discards_the_warmup():
    calls = []
    timing.measure(lambda: calls.append(1), samples=4, inner=2, warmup=5)
    assert len(calls) == 5 + 4 * 2


def test_spread_is_zero_for_a_single_sample():
    s = timing.Sample("x", [0.5])
    assert s.spread == 0.0


def test_spread_survives_one_outlier():
    """Standard deviation would not. One scheduler hiccup should not condemn a benchmark
    where 99 samples of 100 agreed."""
    clean = timing.Sample("x", [1.0] * 40)
    spiked = timing.Sample("x", [1.0] * 39 + [50.0])
    assert spiked.spread == clean.spread == 0.0


def test_sequential_runs_all_of_one_side_first():
    assert schedule.order("sequential", 3) == ["a", "a", "a", "b", "b", "b"]


def test_interleaved_alternates():
    assert schedule.order("interleaved", 3) == ["a", "b", "a", "b", "a", "b"]


def test_paired_gives_each_side_one_run_per_round():
    got = schedule.order("paired", 20, seed=1)
    assert len(got) == 40
    for i in range(0, 40, 2):
        assert set(got[i : i + 2]) == {"a", "b"}


def test_paired_does_not_always_put_the_same_side_first():
    """Otherwise "going first" becomes a property of one version for the whole run."""
    got = schedule.order("paired", 40, seed=0)
    firsts = {got[i] for i in range(0, 80, 2)}
    assert firsts == {"a", "b"}


def test_an_unknown_schedule_is_an_error_not_a_default():
    with pytest.raises(ValueError):
        schedule.order("vibes", 3)


def test_run_returns_the_requested_number_of_samples():
    a, b = schedule.run(lambda: None, lambda: None, inner=1, samples=7, schedule="paired")
    assert len(a) == len(b) == 7


def test_slowed_calls_the_original_more_often():
    calls = []
    base = lambda: calls.append(1)  # noqa: E731
    slow = workloads.slowed(base, 1.0)
    slow()
    assert len(calls) == 2


def test_slowed_by_a_fraction_adds_work_periodically():
    calls = []
    base = lambda: calls.append(1)  # noqa: E731
    slow = workloads.slowed(base, 0.25)
    for _ in range(8):
        slow()
    # Eight calls, plus one extra on every fourth.
    assert len(calls) == 8 + 2


def test_slowed_by_zero_is_the_original_object():
    fn = workloads.arithmetic
    assert workloads.slowed(fn, 0) is fn


def test_every_workload_returns_something():
    for name, fn in workloads.WORKLOADS.items():
        assert fn() is not None, name


def test_load_benchmarks_finds_only_bench_functions(tmp_path):
    p = tmp_path / "b.py"
    p.write_text(BENCH_FILE, encoding="utf-8")
    fns = load_benchmarks(p, "ph_test_load")
    assert set(fns) == {"cheap", "other"}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    (root / "b.py").write_text(BENCH_FILE, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "first")
    return root


def test_checkout_reads_a_file_from_a_ref_without_touching_the_tree(repo, tmp_path):
    (repo / "b.py").write_text(BENCH_FILE + "\ndef bench_new():\n    return 1\n", encoding="utf-8")
    out = checkout(repo, "HEAD", Path("b.py"), tmp_path / "old.py")
    assert "bench_new" not in out.read_text(encoding="utf-8")
    assert "bench_new" in (repo / "b.py").read_text(encoding="utf-8")


def test_compare_against_an_unchanged_file_reports_no_regression(repo, capsys):
    code = main(["compare", str(repo / "b.py"), "--repo", str(repo), "--samples", "8", "--quiet"])
    out = capsys.readouterr().out
    assert code == 0
    assert "PERF-HUNTER" in out
    assert "cheap" in out


def test_a_new_benchmark_is_reported_as_having_nothing_to_compare(repo, capsys):
    (repo / "b.py").write_text(
        BENCH_FILE + "\ndef bench_added():\n    return 2\n", encoding="utf-8"
    )
    main(["compare", str(repo / "b.py"), "--repo", str(repo), "--samples", "8", "--quiet"])
    assert "new since HEAD" in capsys.readouterr().out


def test_a_file_with_no_benchmarks_is_refused(repo, tmp_path, capsys):
    p = repo / "empty.py"
    p.write_text("X = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "empty")
    assert main(["compare", str(p), "--repo", str(repo), "--quiet"]) == 1
    assert "no bench_* functions" in capsys.readouterr().err


def test_a_missing_file_is_refused(tmp_path, capsys):
    assert main(["compare", str(tmp_path / "nope.py")]) == 2
    assert "no such file" in capsys.readouterr().err


def test_a_bad_ref_is_refused(repo, capsys):
    assert main(["compare", str(repo / "b.py"), "--repo", str(repo), "--before", "nosuchref"]) == 2
    assert "git show" in capsys.readouterr().err


def test_run_prints_timings(repo, capsys):
    assert main(["run", str(repo / "b.py"), "--samples", "5"]) == 0
    out = capsys.readouterr().out
    assert "TIMINGS" in out and "cheap" in out


def test_paired_is_reproducible_for_a_seed():
    """Only reproducibility is asserted. "A different seed gives a different order" looks
    like a stronger test and is not one - two seeds can agree by chance, so it would be a
    test that fails at random, which is the thing this repository is about."""
    assert schedule.order("paired", 10, seed=4) == schedule.order("paired", 10, seed=4)


def test_the_rng_is_not_shared_between_schedules():
    rng = random.Random(0)
    first = list(schedule.paired(5, rng))
    rng2 = random.Random(0)
    assert first == list(schedule.paired(5, rng2))
