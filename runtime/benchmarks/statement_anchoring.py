"""F2: how many reference statements have every number and `name` in their anchored code?

Phase 4.3: a proposal whose statement says a number the code does not hold never reaches the
inbox. The target is at least 95 % of the reference statements anchored. Few reference
statements carry numbers, so it also changes each number of those (adds 7) and counts how many
of the altered statements are caught.

    uv run python benchmarks/statement_anchoring.py --golden <reference.json> \\
        --repo <repo>
"""

import argparse
import json
import re
import subprocess
from pathlib import Path

from scrinium.application.analysis.rule_checks import unanchored_terms
from scrinium.infrastructure.decisions import anchor_for, parse_decisions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    golden = json.loads(args.golden.read_text())
    texts: dict[str, str] = {}
    anchored, failing = 0, []
    altered = caught = 0
    for rule in golden["rules"]:
        path = rule["path"]
        if path not in texts:
            texts[path] = subprocess.run(  # noqa: S603  git with fixed arguments
                ["git", "-C", str(args.repo.expanduser()), "show", f"{golden['commit']}:{path}"],  # noqa: S607
                capture_output=True, text=True, check=True,
            ).stdout  # fmt: skip
        decision = anchor_for(parse_decisions(path, texts[path]), rule["start"], rule["end"])
        lines = texts[path].splitlines()
        code = "\n".join(lines[rule["start"] - 1 : rule["end"]])
        missing = unanchored_terms(rule["rule"], decision.literals if decision else (), code)
        for number in re.finditer(r"(?<![\w.,])\d+(?![\w.,])", rule["rule"]):
            wrong = (
                rule["rule"][: number.start()]
                + str(int(number[0]) + 7)
                + rule["rule"][number.end() :]
            )
            altered += 1
            caught += bool(unanchored_terms(wrong, decision.literals if decision else (), code))
        if missing:
            failing.append(f"{rule['id']}: {missing} in «{rule['rule']}»")
        else:
            anchored += 1
    total = len(golden["rules"])
    report = {
        "anchored": f"{anchored}/{total}",
        "rate": round(anchored / total, 3),
        "altered_numbers_caught": f"{caught}/{altered}",
        "failing": failing,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
