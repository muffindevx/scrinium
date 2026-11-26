"""Synthetic pull requests: does `scrinium check` fail only when a verified rule changes?

Phase 2.2.

The reference rules become verified rule files (`.scrinium/rules/`) in a local clone of the
reference repository, and each synthetic pull request is one commit on a branch, checked with
`scrinium check` against main:

- **unrelated** (the 2.1 and 2.2 false-positive test): one to three edits inside functions that
  hold reference rules, never on a decision's lines: a statement added, a string or a name
  changed. The largest function with rules gets its own share. Every finding is a false
  positive; the target is fewer than 5 per 100 pull requests;
- **refactor:** the behaviour-preserving refactors of `reanchor_refactors.py` on a rule's own
  decision. A failing check is a false positive;
- **threshold:** a rule's number changes. The check must fail (recall);
- **membership:** a value leaves a rule's set, tuple or list of states (`{'a', 'b'}` becomes
  `{'b'}`). The check must fail (recall).

It also times each check (target: under 5 s, without GPU or network).

    uv run python benchmarks/check_prs.py --golden <reference.json> \\
        --repo <repo> --prs 100
"""

import argparse
import json
import random
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from tree_sitter import Node

from scrinium.application.check.engine import CHANGED, check
from scrinium.application.check.git import Repository
from scrinium.application.check.rule_files import RULES_DIR, FileAnchor, RuleFile, write_all
from scrinium.core.ids import uuid7
from scrinium.infrastructure.code_structure import _parser
from scrinium.infrastructure.decisions import Decision, anchor_for, parse_decisions

sys.path.insert(0, str(Path(__file__).parent))
from reanchor_refactors import REFACTORS, change_threshold

_STRING = re.compile(r"""(['"])([A-Za-z][A-Za-z _]{2,40})\1""")
_MEMBERS = re.compile(r"""([{(\[])\s*(['"][\w ]+['"])\s*,\s*(?=['"])""")
_SIMPLE = re.compile(r"^\s+[a-z_][\w.]*\s*(?:=|\()")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603  git with fixed arguments
        ["git", "-C", str(repo), "-c", "user.name=bench", "-c", "user.email=b@example.com",  # noqa: S607
         *args],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip


def _functions(text: str) -> list[tuple[int, int]]:
    """1-based (first, last) lines of every Python function."""
    found: list[tuple[int, int]] = []
    stack: list[Node] = [_parser("python").parse(text.encode()).root_node]
    while stack:
        node = stack.pop()
        if node.type == "function_definition":
            found.append((node.start_point[0] + 1, node.end_point[0] + 1))
        stack.extend(node.children)
    return found


def _free_lines(
    text: str, decisions: list[Decision], functions: list[tuple[int, int]]
) -> list[int]:
    """Lines inside a function, outside every decision, that are plain statements."""
    taken = {n for d in decisions for n in range(d.start_line, d.end_line + 1)}
    lines = text.splitlines()
    free = []
    for first, last in functions:
        for number in range(first + 1, last + 1):
            line = lines[number - 1]
            if number in taken or not _SIMPLE.match(line) or line.rstrip().endswith((",", "(")):
                continue
            nxt = lines[number] if number < len(lines) else ""
            if nxt.strip().startswith((".", ")", "]", "}")) or line.count("(") != line.count(")"):
                continue
            free.append(number)
    return free


def _unrelated_edit(rng: random.Random, text: str, number: int) -> str:
    lines = text.splitlines()
    line = lines[number - 1]
    indent = line[: len(line) - len(line.lstrip())]
    choice = rng.random()
    string = _STRING.search(line)
    if choice < 0.4 and string:
        new = line[: string.start(2)] + string[2] + " (rev)" + line[string.end(2) :]
        lines[number - 1] = new
    elif choice < 0.7:
        lines.insert(number, f"{indent}_bench_note = 'revisado {rng.randint(1, 999)}'")
    else:
        lines.insert(number - 1, f"{indent}# nota de revisión {rng.randint(1, 999)}")
    return "\n".join(lines) + "\n"


def drop_member(text: str, anchor: Decision) -> str | None:
    """Removes the first value of the first literal collection on the decision's lines."""
    lines = text.splitlines()
    for at in range(anchor.start_line - 1, anchor.end_line):
        match = _MEMBERS.search(lines[at])
        if match:
            lines[at] = lines[at][: match.start()] + match[1] + lines[at][match.end() :]
            # A one-value tuple keeps its comma: `("cancel")` would be a string, not a tuple.
            lines[at] = re.sub(r"""\((['"][\w ]+['"])\)""", r"(\1,)", lines[at])
            return "\n".join(lines) + "\n"
    return None


def evaluate(golden: dict, source: Path, prs: int, seed: int) -> dict:
    rng = random.Random(seed)  # noqa: S311  reproducible synthetic edits, not secrets
    with tempfile.TemporaryDirectory(prefix="scrinium-check-") as tmp:
        repo = Path(tmp) / "repo"
        subprocess.run(  # noqa: S603  local clone, no network
            ["git", "clone", "-q", "--no-hardlinks", str(source), str(repo)],  # noqa: S607
            check=True,
        )
        _git(repo, "checkout", "-q", "-B", "main", golden["commit"])
        texts: dict[str, str] = {}
        decisions: dict[str, list[Decision]] = {}
        rules: list[RuleFile] = []
        anchored: list[tuple[dict, Decision]] = []
        for rule in golden["rules"]:
            path = rule["path"]
            if path not in texts:
                texts[path] = (repo / path).read_text()
                decisions[path] = parse_decisions(path, texts[path])
            anchor = anchor_for(decisions[path], rule["start"], rule["end"])
            if anchor is None:
                continue
            anchored.append((rule, anchor))
            rules.append(
                RuleFile(
                    str(uuid7()), rule["id"], rule["rule"],
                    anchors=(FileAnchor(path, anchor.symbol, anchor.kind, anchor.start_line,
                                        anchor.end_line, anchor.shape, anchor.value,
                                        anchor.sketch),),
                )
            )  # fmt: skip
        write_all(repo / RULES_DIR, rules)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "reglas verificadas")
        main_commit = _git(repo, "rev-parse", "HEAD").strip()

        # Functions that hold a reference rule, with their free lines.
        holders: list[tuple[str, int, int, list[int]]] = []
        for path, text in texts.items():
            free = _free_lines(text, decisions[path], _functions(text))
            for first, last in _functions(text):
                spans = [(r, a) for r, a in anchored if r["path"] == path]
                if any(first <= a.start_line <= last for _, a in spans):
                    inside = [n for n in free if first < n <= last]
                    if inside:
                        holders.append((path, first, last, inside))
        largest = max(holders, key=lambda h: h[2] - h[1])

        repository = Repository.at(repo)
        timings: list[float] = []
        outcome: Counter[str] = Counter()
        problems: list[str] = []

        def run(label: str, files: dict[str, str], removed: tuple[str, ...] = ()) -> list[str]:
            _git(repo, "checkout", "-q", "-B", "pr", main_commit)
            for path in removed:
                (repo / path).unlink()
            for path, text in files.items():
                (repo / path).parent.mkdir(parents=True, exist_ok=True)
                (repo / path).write_text(text)
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "--allow-empty", "-m", label)
            result = check(repository, "main")
            timings.append(result.seconds)
            if result.errors:
                problems.append(f"{label}: {result.errors}")
            return [f.level for f in result.findings]

        # Unrelated pull requests: a quarter of them in the largest function.
        for number in range(prs):
            files: dict[str, str] = {}
            for _ in range(rng.randint(1, 3)):
                path, _, _, free = largest if number % 4 == 0 else rng.choice(holders)
                text = files.get(path, texts[path])
                if text != texts[path]:
                    # Lines moved with the earlier edit: pick again on the edited text.
                    free = _free_lines(text, parse_decisions(path, text), _functions(text))
                files[path] = _unrelated_edit(rng, text, rng.choice(free))
            levels = run(f"unrelated {number}", files)
            outcome["unrelated_prs"] += 1
            outcome["unrelated_findings"] += len(levels)
            outcome["unrelated_failed"] += CHANGED in levels
            if levels:
                problems.append(f"unrelated {number}: {levels} in {sorted(files)}")

        # Behaviour-preserving refactors and changed thresholds of each rule's decision.
        for rule, anchor in anchored:
            path = rule["path"]
            for name, refactor in REFACTORS.items():
                edit = refactor(path, texts[path], anchor)
                if edit is None:
                    continue
                new_path, new_text, _ = edit
                removed = (path,) if new_path != path else ()
                levels = run(f"{name} {rule['id']}", {new_path: new_text}, removed)
                outcome["refactor_prs"] += 1
                outcome["refactor_failed"] += CHANGED in levels
                outcome["refactor_warned"] += bool(levels) and CHANGED not in levels
                if levels:
                    problems.append(f"{name} {rule['id']}: {levels}")
            dropped = drop_member(texts[path], anchor)
            if dropped is not None:
                levels = run(f"membership {rule['id']}", {path: dropped})
                outcome["membership_prs"] += 1
                outcome["membership_failed"] += CHANGED in levels
                if CHANGED not in levels:
                    problems.append(f"membership {rule['id']}: {levels}")
            edit = change_threshold(path, texts[path], anchor)
            if edit is not None:
                levels = run(f"threshold {rule['id']}", {path: edit[1]})
                outcome["threshold_prs"] += 1
                outcome["threshold_failed"] += CHANGED in levels
                if CHANGED not in levels:
                    problems.append(f"threshold {rule['id']}: {levels}")

    return {
        "project": golden["project"],
        "rules": len(rules),
        "largest_function": f"{largest[0]}:{largest[1]}-{largest[2]}",
        "unrelated_false_positives_per_100_prs": round(
            100 * outcome["unrelated_findings"] / max(outcome["unrelated_prs"], 1), 2
        ),
        "refactor_false_failures": f"{outcome['refactor_failed']}/{outcome['refactor_prs']}",
        "threshold_recall": f"{outcome['threshold_failed']}/{outcome['threshold_prs']}",
        "membership_recall": f"{outcome['membership_failed']}/{outcome['membership_prs']}",
        "seconds": {
            "median": round(statistics.median(timings), 3),
            "max": round(max(timings), 3),
        },
        "counts": dict(outcome),
        "problems": problems[:40],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--prs", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20261116)
    args = parser.parse_args()
    golden = json.loads(args.golden.read_text())
    print(json.dumps(evaluate(golden, args.repo.expanduser(), args.prs, args.seed), indent=2))


if __name__ == "__main__":
    main()
