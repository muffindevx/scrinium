"""Synthetic refactors: does every reference rule keep its anchor? (ADR-011, phase 1.4)

For each reference rule with an anchor, one refactor at a time is applied to a copy of its file
and the anchor is looked for in the new code with `reanchor`. A rule is kept when its anchor
lands on the same decision (same first line once the edit's shift is undone). The target is
100 % for refactors that do not change behaviour. A changed threshold must be found with a
different `value` (drift), never lost.

    uv run python benchmarks/reanchor_refactors.py --golden <reference.json> \\
        --repo <repo>
"""

import argparse
import json
import re
import subprocess
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from tree_sitter import Node

from scrinium.application.knowledge.anchoring import OldAnchor, reanchor
from scrinium.infrastructure.code_structure import _parser
from scrinium.infrastructure.decisions import Decision, anchor_for, parse_decisions

# (new path, new text, function from an old line number to its new one) or None if not applicable
Edit = tuple[str, str, Callable[[int], int]] | None

_OBJECT = re.compile(r"(?<![.\w])([a-z_][a-z0-9_]*)\.")
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])")
_KEYWORDS = frozenset({"not", "and", "or", "in", "is", "if", "for", "return", "lambda", "self"})


def _function_bounds(lines: list[str], line: int) -> tuple[int, int]:
    """0-based [start, end) of the top-level-or-method `def` holding 1-based `line`."""
    index = line - 1
    start = next(
        (i for i in range(index, -1, -1) if lines[i].lstrip().startswith(("def ", "async def "))),
        0,
    )
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines) and (
        not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent
    ):
        end += 1
    return start, end


def _python_tree(text: str) -> Node:
    return _parser("python").parse(text.encode("utf-8")).root_node


def _holding_function(root: Node, line: int) -> Node | None:
    found = None
    stack = [root]
    while stack:
        node = stack.pop()
        if node.start_point[0] + 1 <= line <= node.end_point[0] + 1:
            if node.type == "function_definition":
                found = node
            stack.extend(node.children)
    return found


def _replace(text: str, spans: list[tuple[int, int, str]]) -> str:
    data = text.encode("utf-8")
    for start, end, value in sorted(spans, reverse=True):
        data = data[:start] + value.encode("utf-8") + data[end:]
    return data.decode("utf-8")


def rename_local(path: str, text: str, anchor: Decision) -> Edit:
    """Renames the first object of an attribute access in the anchor, only as an identifier."""
    root = _python_tree(text)
    function = _holding_function(root, anchor.start_line)
    if function is None:
        return None
    lines = text.splitlines()
    names = [
        n
        for line in lines[anchor.start_line - 1 : anchor.end_line]
        for n in _OBJECT.findall(line)
        if n not in _KEYWORDS
    ]
    if not names:
        return None
    name = names[0]
    spans = []
    stack = [function]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type != "identifier" or node.text != name.encode():
            continue
        parent = node.parent
        is_field = (
            parent is not None
            and parent.type == "attribute"
            and (parent.child_by_field_name("attribute") == node)
        )
        is_keyword = (
            parent is not None
            and parent.type == "keyword_argument"
            and (parent.child_by_field_name("name") == node)
        )
        if not is_field and not is_keyword:
            spans.append((node.start_byte, node.end_byte, name + "_renamed"))
    return (path, _replace(text, spans), lambda n: n) if spans else None


def reformat(path: str, text: str, anchor: Decision) -> Edit:
    lines = text.splitlines()
    at = anchor.start_line - 1
    indent = lines[at][: len(lines[at]) - len(lines[at].lstrip())]
    new = [*lines[:at], "", f"{indent}# reviewed", "", *lines[at:]]
    return path, "\n".join(new) + "\n", lambda n: n + 3 if n >= anchor.start_line else n


def swap_comparison(path: str, text: str, anchor: Decision) -> Edit:
    """`a < b` becomes `b > a` for the first two-operand ordering comparison in the anchor."""
    root = _python_tree(text)
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.children))
        if node.type != "comparison_operator":
            continue
        if not anchor.start_line <= node.start_point[0] + 1 <= anchor.end_line:
            continue
        parts = node.children
        if len(parts) != 3 or parts[1].type not in {"<", ">", "<=", ">="}:
            continue
        left, operator, right = (p.text.decode() for p in parts)
        flipped = {"<": ">", ">": "<", "<=": ">=", ">=": "<="}[operator]
        swapped = f"{right} {flipped} {left}"
        if "\n" in swapped:
            continue
        return path, _replace(text, [(node.start_byte, node.end_byte, swapped)]), lambda n: n
    return None


def extend_condition(path: str, text: str, anchor: Decision) -> Edit:
    """A real edit: one more condition on a guard (`if a:` -> `if a and not ctx.get('x'):`)."""
    if anchor.kind != "guard":
        return None
    lines = text.splitlines()
    at = anchor.start_line - 1
    if not lines[at].rstrip().endswith(":") or not lines[at].lstrip().startswith(("if ", "elif ")):
        return None
    head = lines[at].rstrip()[:-1]
    lines[at] = f"{head} and not self.env.context.get('skip_check'):"
    return path, "\n".join(lines) + "\n", lambda n: n


def move_function(path: str, text: str, anchor: Decision) -> Edit:
    lines = text.splitlines()
    start, end = _function_bounds(lines, anchor.start_line)
    if start == 0 or end >= len(lines):
        return None
    block = lines[start:end]
    rest = lines[:start] + lines[end:]
    new = [*rest, "", *block]
    offset = len(rest) + 1 - start

    def where(n: int) -> int:
        return n + offset if start < n <= end else n

    return path, "\n".join(new) + "\n", where


def move_file(path: str, text: str, anchor: Decision) -> Edit:
    return "moved/" + path, text, lambda n: n


def change_threshold(path: str, text: str, anchor: Decision) -> Edit:
    lines = text.splitlines()
    for at in range(anchor.start_line - 1, anchor.end_line):
        match = _NUMBER.search(lines[at])
        if match:
            lines[at] = lines[at][: match.start()] + "7777" + lines[at][match.end() :]
            return path, "\n".join(lines) + "\n", lambda n: n
    return None


REFACTORS: dict[str, Callable[[str, str, Decision], Edit]] = {
    "rename_local": rename_local,
    "reformat": reformat,
    "swap_comparison": swap_comparison,
    "move_function": move_function,
    "move_file": move_file,
}


def _read(repo: Path, commit: str, path: str) -> str:
    return subprocess.run(  # noqa: S603  git with fixed arguments
        ["git", "-C", str(repo), "show", f"{commit}:{path}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def evaluate(golden: dict, repo: Path) -> dict:
    texts: dict[str, str] = {}
    decisions: dict[str, list[Decision]] = {}
    for rule in golden["rules"]:
        path = rule["path"]
        if path not in texts:
            texts[path] = _read(repo, golden["commit"], path)
            decisions[path] = parse_decisions(path, texts[path])
    results: dict[str, Counter[str]] = {
        name: Counter() for name in [*REFACTORS, "edited", "threshold"]
    }
    lost: list[str] = []
    for rule in golden["rules"]:
        path = rule["path"]
        anchor = anchor_for(decisions[path], rule["start"], rule["end"])
        if anchor is None:
            continue
        old = OldAnchor(
            rule["id"], path, anchor.symbol, anchor.kind, anchor.shape, anchor.value,
            anchor.sketch, anchor.start_line,
        )  # fmt: skip
        for name, refactor in [
            *REFACTORS.items(),
            ("edited", extend_condition),
            ("threshold", change_threshold),
        ]:
            edit = refactor(path, texts[path], anchor)
            if edit is None:
                results[name]["not_applicable"] += 1
                continue
            new_path, new_text, where = edit
            new_by_path = {p: d for p, d in decisions.items() if p != path}
            new_by_path[new_path] = parse_decisions(new_path, new_text)
            renames = {path: new_path} if new_path != path else None
            match = reanchor([old], new_by_path, renames).get(rule["id"])
            if match is None:
                results[name]["lost"] += 1
                lost.append(f"{name} {rule['id']}")
                continue
            same_place = match.path == new_path and match.decision.start_line == where(
                anchor.start_line
            )
            if name == "threshold":
                outcome = "drift" if match.value_changed(old) else "value_unchanged"
            elif name == "edited":
                outcome = "found" if same_place else "wrong"
            else:
                outcome = "kept" if same_place and not match.value_changed(old) else "wrong"
            results[name][outcome] += 1
            results[name][match.how] += 1
            if outcome in {"wrong", "value_unchanged"}:
                lost.append(f"{name} {rule['id']} ({match.how}, line {match.decision.start_line})")
    applied = sum(c["kept"] + c["lost"] + c["wrong"] for n, c in results.items() if n in REFACTORS)
    kept = sum(c["kept"] for n, c in results.items() if n in REFACTORS)
    return {
        "project": golden["project"],
        "kept": f"{kept}/{applied}",
        "rate": round(kept / applied, 3) if applied else 0.0,
        "by_refactor": {name: dict(counter) for name, counter in results.items()},
        "problems": lost,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(json.loads(args.golden.read_text()), args.repo), indent=2))


if __name__ == "__main__":
    main()
