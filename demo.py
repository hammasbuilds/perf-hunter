"""Show what perf-hunter does, in one command, with nothing to set up.

    python demo.py

This runs the tool's own self-check: it injects a known slowdown into a
function, measures what the injection actually cost, and reports whether the
detector found it. That is the honest way to demo a performance detector -
pointing it at a real repository shows output, but nothing tells you whether
the numbers are right, because there is no ground truth to compare against.

Here there is: the slowdown is planted, so "asked" (the factor requested) can
be compared against "achieved" (what the injection really cost, measured
separately). Those two disagree, and the second is the one that means anything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    print("perf-hunter self-check: can it find a slowdown it planted itself?\n", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "perf_hunter.cli", "self-check"],
        cwd=ROOT,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
    )
    if result.returncode != 0:
        print("\nself-check failed - that is the demo working, not a broken demo.", flush=True)
        return result.returncode
    print("\nPoint it at your own code with:", flush=True)
    print("    perf-hunter compare <baseline-ref> <candidate-ref>", flush=True)
    print("    perf-hunter run <path>", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
