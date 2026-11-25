"""`scrinium coverage`: where the code decides, and which decisions a verified rule covers.

Like `scrinium check`, it needs only the repository: the committed code files (`git ls-files`)
and the reviewed rules in `.scrinium/rules/`. No database, no model, no network. It prints one
line per folder (or file, under `--path`) with its decisions, those covered by a verified rule,
and those without one, or the same as JSON.
"""

import json
import posixpath
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from scrinium.application.check.git import GitError, Repository, git
from scrinium.application.check.rule_files import RULES_DIR, VERIFIED, RuleFileError, load
from scrinium.domain.paths import is_test_path
from scrinium.infrastructure.decisions import parse_decisions, supports


def measure(root: Path, path: str = "") -> dict[str, Any]:
    repository = Repository.at(root)
    prefix = repository.prefix
    listed = git(repository.top, "ls-files", "-z", "--", prefix or ".").split("\0")
    files = [
        name[len(prefix) :]
        for name in listed
        if name.startswith(prefix) and supports(name) and not is_test_path(name)
    ]
    anchors: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for rule in load(root / RULES_DIR):
        if rule.status == VERIFIED:
            for anchor in rule.anchors:
                anchors[anchor.path].append((anchor.start_line, anchor.end_line))
    wanted = path.strip("/")

    def empty() -> dict[str, int]:
        return {"files": 0, "decisions": 0, "verified": 0, "uncovered": 0}

    rows: dict[str, dict[str, int]] = defaultdict(empty)
    total = {"files": 0, "decisions": 0, "verified": 0, "uncovered": 0}
    for file in files:
        if wanted and not (file == wanted or file.startswith(f"{wanted}/")):
            continue
        text = (repository.top / prefix / file).read_text(encoding="utf-8", errors="replace")
        decisions = parse_decisions(file, text)
        covered = sum(
            1
            for d in decisions
            if any(start <= d.end_line and end >= d.start_line for start, end in anchors[file])
        )
        rest = file[len(wanted) + 1 :] if wanted else file
        key = posixpath.join(wanted, rest.split("/", 1)[0]) if rest else file
        for counts in (rows[key], total):
            counts["files"] += 1
            counts["decisions"] += len(decisions)
            counts["verified"] += covered
            counts["uncovered"] += len(decisions) - covered
    return {
        "path": wanted,
        "children": [{"path": key, **counts} for key, counts in sorted(rows.items())],
        "total": total,
    }


def run_coverage(root: Path, path: str, as_json: bool) -> int:
    try:
        found = measure(root.resolve(), path)
    except (GitError, RuleFileError, OSError) as error:
        print(f"scrinium coverage: {error}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return 0
    width = max([len(row["path"]) for row in found["children"]] + [10])
    print(f"{'Path':<{width}}   Decisions     Verified    No rule")
    for row in [*found["children"], {"path": "Total", **found["total"]}]:
        share = f"{row['verified'] * 100 // row['decisions']} %" if row["decisions"] else "—"
        print(f"{row['path']:<{width}}  {row['decisions']:>10}  {row['verified']:>6} {share:>4}"
              f"  {row['uncovered']:>9}")  # fmt: skip
    return 0
