"""Golden-output regression harness for the deterministic pipeline.

Runs `python -m run` for a fixed set of representative equipment tags and compares
the resulting UI JSON payload against a stored golden copy. This is the safety net
for refactors that must not change pipeline behavior (e.g. consolidating shared
logic, splitting bbox.py). It needs live JanusGraph + Plant360 access, so it is a
manual/CI script, NOT a unit test.

    uv run python scripts/golden_regression.py --update   # capture/refresh goldens
    uv run python scripts/golden_regression.py            # check against goldens

Exit code is non-zero if any tag drifts, so it can gate a refactor.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"

# Representative spread: both P&IDs (Dadon-2 job 2483, Aker-Clean job 2151) and
# multiple equipment classes (vessel, pump, heat exchanger).
TAGS = [
    "OGHC20-BB001",
    "0GHC30-CP004",
    "OGHC30-BR003",
    "N7",
    "P3",
]


def run_pipeline(tag: str, out_dir: Path) -> dict:
    """Run the deterministic pipeline for one tag and return its JSON payload."""
    subprocess.run(
        [sys.executable, "-m", "run", "--equipment", tag, "--quiet", "--output-dir", str(out_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads((out_dir / f"{tag}.json").read_text(encoding="utf-8"))


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


def update() -> int:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for tag in TAGS:
            payload = run_pipeline(tag, Path(tmp))
            (GOLDEN_DIR / f"{tag}.json").write_text(_canonical(payload) + "\n", encoding="utf-8")
            print(f"captured {tag}")
    print(f"\nGoldens written to {GOLDEN_DIR}")
    return 0


def check() -> int:
    drift = 0
    with tempfile.TemporaryDirectory() as tmp:
        for tag in TAGS:
            golden_path = GOLDEN_DIR / f"{tag}.json"
            if not golden_path.exists():
                print(f"MISSING golden for {tag} (run --update first)")
                drift += 1
                continue
            actual = _canonical(run_pipeline(tag, Path(tmp)))
            expected = golden_path.read_text(encoding="utf-8").rstrip("\n")
            if actual == expected:
                print(f"OK   {tag}")
            else:
                drift += 1
                a_lines = expected.splitlines()
                b_lines = actual.splitlines()
                import difflib

                delta = [
                    line
                    for line in difflib.unified_diff(a_lines, b_lines, lineterm="")
                    if line[:1] in "+-" and line[:2] not in ("++", "--")
                ]
                print(f"DRIFT {tag}: {len(delta)} changed line(s)")
                for line in delta[:40]:
                    print(f"    {line}")
    if drift:
        print(f"\n{drift} tag(s) drifted from golden.")
        return 1
    print(f"\nAll {len(TAGS)} tags match golden.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="Capture/refresh golden outputs instead of checking.")
    args = parser.parse_args()
    return update() if args.update else check()


if __name__ == "__main__":
    raise SystemExit(main())
