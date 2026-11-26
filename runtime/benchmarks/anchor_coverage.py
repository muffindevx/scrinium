"""How many reference rules have a decision node to anchor them (ADR-011, phase 1.2).

No model and no database: each rule's file is read from the repository at the reference
commit, its decisions are parsed, and the rule counts as anchored when some decision overlaps
its lines. The target is 95 %.

    uv run python benchmarks/anchor_coverage.py --golden <reference.json> \\
        --repo <repo> [--show-missing]
"""

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

from scrinium.infrastructure.decisions import anchor_for, parse_decisions


def _read(repo: Path, commit: str, path: str) -> str:
    return subprocess.run(  # noqa: S603  git with fixed arguments
        ["git", "-C", str(repo), "show", f"{commit}:{path}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def coverage(golden: dict, repo: Path) -> dict:
    files: dict[str, list] = {}
    anchored, missing = 0, []
    kinds: Counter[str] = Counter()
    for rule in golden["rules"]:
        path = rule["path"]
        if path not in files:
            files[path] = parse_decisions(path, _read(repo, golden["commit"], path))
        anchor = anchor_for(files[path], rule["start"], rule["end"])
        if anchor is None:
            missing.append(rule)
        else:
            anchored += 1
            kinds[anchor.kind] += 1
    total = len(golden["rules"])
    return {
        "project": golden["project"],
        "rules": total,
        "anchored": anchored,
        "coverage": round(anchored / total, 3) if total else 0.0,
        "by_kind": dict(kinds.most_common()),
        "missing": [f"{r['id']} {r['path']}:{r['start']}-{r['end']}" for r in missing],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--show-missing", action="store_true")
    args = parser.parse_args()
    result = coverage(json.loads(args.golden.read_text()), args.repo)
    if not args.show_missing:
        result.pop("missing")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
