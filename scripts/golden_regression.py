"""Golden-output regression harness for the deterministic pipeline.

Runs `python -m run` for a fixed set of representative equipment tags and compares
the resulting UI JSON payload against a stored golden copy. This is the safety net
for refactors that must not change pipeline behavior (e.g. consolidating shared
logic, splitting bbox.py). It needs live JanusGraph + Plant360 access, so it is a
manual/CI script, NOT a unit test.

    uv run python scripts/golden_regression.py --update   # capture/refresh goldens
    uv run python scripts/golden_regression.py            # check against goldens
    uv run python scripts/golden_regression.py --jobs 1   # serial (debugging)

Tags run concurrently (threads around subprocess, which is I/O-bound), so wall
clock is roughly the slowest single tag rather than the sum. Output stays ordered
by TAGS regardless of completion order, so runs are diffable against each other.

A tag whose pipeline run fails is reported as FAIL and counted as drift; it does
not abort the other tags. Exit code is non-zero if any tag drifts or fails, so it
can gate a refactor.
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLDEN_DIR = REPO_ROOT / "tests" / "golden"

# Representative spread: both P&IDs (Dadon-2 job 2483, Aker-Clean job 2151) and
# multiple equipment classes (vessel, pump, heat exchanger).
TAGS = [
    "OGHC20-BB001",
    "0GHC30-CP004",
    "OGHC30-BR003",
    "N7",
    "P3",
]

DEFAULT_JOBS = 5


def run_pipeline(tag: str, out_dir: Path) -> dict:
    """Run the deterministic pipeline for one tag and return its JSON payload.

    Each tag gets its own output directory so concurrent runs cannot interact
    through the P&ID image files the pipeline also writes there.
    """
    tag_dir = out_dir / tag
    tag_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "run", "--equipment", tag, "--quiet", "--output-dir", str(tag_dir)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        raise RuntimeError("\n".join(tail[-5:]) or f"exit {result.returncode}")
    return json.loads((tag_dir / f"{tag}.json").read_text(encoding="utf-8"))


def _strip_volatile(obj):
    """Drop fields that embed the run output directory (e.g. debug.pid_image_path).
    They are run-location noise, not pipeline behavior."""
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k != "pid_image_path"}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def _canonical(payload: dict) -> str:
    return json.dumps(_strip_volatile(payload), indent=1, sort_keys=True)


def _run_all(tmp: Path, jobs: int) -> dict[str, object]:
    """Run every tag concurrently. Returns tag -> payload dict or the Exception."""

    def one(tag: str):
        try:
            return run_pipeline(tag, tmp)
        except Exception as exc:  # reported per-tag; never aborts the sweep
            return exc

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return dict(zip(TAGS, pool.map(one, TAGS)))


def update(jobs: int, golden_dir: Path) -> int:
    golden_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        results = _run_all(Path(tmp), jobs)
    for tag in TAGS:
        payload = results[tag]
        if isinstance(payload, Exception):
            failed += 1
            print(f"FAIL {tag}: {payload}")
            continue
        (golden_dir / f"{tag}.json").write_text(_canonical(payload) + "\n", encoding="utf-8")
        print(f"captured {tag}")
    if failed:
        print(f"\n{failed} tag(s) failed to run; their goldens were NOT written.")
        return 1
    print(f"\nGoldens written to {golden_dir}")
    return 0


def check(jobs: int, golden_dir: Path) -> int:
    drift = 0
    with tempfile.TemporaryDirectory() as tmp:
        results = _run_all(Path(tmp), jobs)

    for tag in TAGS:
        golden_path = golden_dir / f"{tag}.json"
        if not golden_path.exists():
            print(f"MISSING golden for {tag} (run --update first)")
            drift += 1
            continue

        payload = results[tag]
        if isinstance(payload, Exception):
            drift += 1
            print(f"FAIL {tag}: {payload}")
            continue

        actual = _canonical(payload)
        expected = golden_path.read_text(encoding="utf-8").rstrip("\n")
        if actual == expected:
            print(f"OK   {tag}")
            continue

        drift += 1
        delta = [
            line
            for line in difflib.unified_diff(expected.splitlines(), actual.splitlines(), lineterm="")
            if line[:1] in "+-" and line[:2] not in ("++", "--")
        ]
        print(f"DRIFT {tag}: {len(delta)} changed line(s)")
        for line in delta[:40]:
            print(f"    {line}")
        if len(delta) > 40:
            print(f"    ... {len(delta) - 40} more")

    if drift:
        print(f"\n{drift} tag(s) drifted from golden.")
        return 1
    print(f"\nAll {len(TAGS)} tags match golden.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="Capture/refresh golden outputs instead of checking.")
    parser.add_argument(
        "--golden-dir",
        type=Path,
        default=DEFAULT_GOLDEN_DIR,
        help="Directory holding golden payloads (default tests/golden). Point this at an\n"
             "out-of-tree baseline to verify a refactor without staging files into the repo.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"Concurrent pipeline runs (default {DEFAULT_JOBS}; use 1 to serialize when debugging).",
    )
    args = parser.parse_args()
    golden_dir = args.golden_dir.expanduser().resolve()
    print(f"golden dir: {golden_dir}\n")
    return update(args.jobs, golden_dir) if args.update else check(args.jobs, golden_dir)


if __name__ == "__main__":
    raise SystemExit(main())
