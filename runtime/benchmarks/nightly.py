"""Nightly regression run (phase 9.7): anchoring, drift and statement checks, with no model.

Runs the benchmarks that need no GPU and no network on a reference set and compares each
metric with a baseline file. Both live outside the repository: the analyzed projects' rules
never go in it. A metric that falls below its baseline is a
regression: the run prints it and exits with 1, so a scheduler (cron, a systemd timer, CI)
can raise the alarm. `--update` writes the current numbers as the new baseline, after a
person decided a change is right.

What it does not measure yet: extraction recall, which needs a model's answers. A replay of
recorded answers needs a recording run with the owner's provider first (see HANDOFF).

    uv run python benchmarks/nightly.py --repo <repo> --golden <reference.json> \\
        --baseline <baseline.json>
    # crontab: 0 3 * * * cd /path/to/scrinium/runtime && uv run python benchmarks/nightly.py ...
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent


def _run(script: str, golden: Path, repo: Path, *extra: str) -> dict[str, Any]:
    out = subprocess.run(  # noqa: S603  this same Python, fixed scripts
        [sys.executable, str(HERE / script), "--golden", str(golden), "--repo", str(repo), *extra],
        capture_output=True, text=True, check=True, timeout=1800,
    ).stdout  # fmt: skip
    result: dict[str, Any] = json.loads(out)
    return result


def _ratio(text: str) -> float:
    found, total = text.split("/")
    return int(found) / int(total) if int(total) else 1.0


def measure(golden: Path, repo: Path) -> dict[str, float]:
    """Each metric as a number where higher is better."""
    started = time.perf_counter()
    coverage = _run("anchor_coverage.py", golden, repo)
    refactors = _run("reanchor_refactors.py", golden, repo)
    statements = _run("statement_anchoring.py", golden, repo)
    prs = _run("check_prs.py", golden, repo, "--prs", "100")["counts"]
    return {
        "anchor_coverage": float(coverage["coverage"]),
        "refactors_kept": float(refactors["rate"]),
        "statements_anchored": float(statements["rate"]),
        "altered_numbers_caught": _ratio(statements["altered_numbers_caught"]),
        "unrelated_prs_clean": 1 - prs["unrelated_findings"] / max(prs["unrelated_prs"], 1),
        "refactor_prs_pass": 1 - prs["refactor_failed"] / max(prs["refactor_prs"], 1),
        "membership_caught": prs["membership_failed"] / max(prs["membership_prs"], 1),
        "thresholds_caught": prs["threshold_failed"] / max(prs["threshold_prs"], 1),
        "seconds": round(time.perf_counter() - started, 1),
    }


def compare(current: dict[str, float], baseline: dict[str, float]) -> list[str]:
    regressions = []
    for name, expected in baseline.items():
        if name == "seconds":
            continue
        got = current.get(name)
        if got is None or got + 1e-9 < expected:
            regressions.append(f"{name}: {got} < {expected}")
    return regressions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True, help="The reference rules.")
    parser.add_argument("--baseline", type=Path, required=True, help="The numbers to beat.")
    parser.add_argument("--update", action="store_true", help="Write the current numbers.")
    args = parser.parse_args()
    current = measure(args.golden.expanduser(), args.repo.expanduser())
    if args.update or not args.baseline.exists():
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(current, indent=2) + "\n")
        print(json.dumps({"baseline_written": str(args.baseline), **current}, indent=2))
        return 0
    regressions = compare(current, json.loads(args.baseline.read_text()))
    print(json.dumps({"current": current, "regressions": regressions}, indent=2))
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
