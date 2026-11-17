"""What a change does to the rules, between any two refs (phase 7.1, the Cambios screen).

The same engine as `scrinium check` (decision fingerprints and `reanchor`), with two
differences: the rules come from Scrinium's database, all of them that are not rejected, and the
two sides are any commit, branch or tag, or the working folder.

It goes in two steps, so a rule cited at an older revision is judged on what the change did, not
on what happened before it:

1. each rule's anchor is found in the **base** code;
2. every decision of each touched file is followed from the base to the **head**.

Rules end as `changed` (same decision, other values), `review` (edited in place, or gone while
its function remains), `removed` or `moved`. A decision in the head with no counterpart in the
base is **new**: a possible rule nobody has written yet. No model, no network.
"""

import time
from collections.abc import Hashable
from dataclasses import dataclass, field
from typing import Final

from scrinium.application.check.engine import CHANGED, MOVED, REMOVED, REVIEW, _symbol_exists
from scrinium.application.check.git import ADDED, DELETED, RENAMED, Reader, Repository
from scrinium.application.check.rule_files import FileAnchor
from scrinium.application.knowledge.anchoring import FUZZY, OldAnchor, reanchor
from scrinium.infrastructure.decisions import Decision, parse_decisions, supports

MAX_NEW: Final = 200


@dataclass(frozen=True)
class KnownRule:
    id: str
    title: str
    status: str
    anchors: tuple[FileAnchor, ...]


@dataclass(frozen=True)
class Side:
    path: str
    start_line: int
    end_line: int
    symbol: str | None
    values: tuple[str, ...]

    @classmethod
    def of(cls, path: str, decision: Decision) -> "Side":
        return cls(path, decision.start_line, decision.end_line, decision.symbol,
                   decision.literals)  # fmt: skip


@dataclass(frozen=True)
class RuleChange:
    rule_id: str
    title: str
    status: str
    level: str
    before: Side
    after: Side | None


@dataclass
class ChangesResult:
    base: str
    head: str
    changed_files: list[str] = field(default_factory=list)
    rules: list[RuleChange] = field(default_factory=list)
    new_decisions: list[tuple[str, str, Side]] = field(default_factory=list)  # (kind, path, side)
    unanchored: int = 0
    seconds: float = 0.0


def _parsed(reader: Reader, prefix: str, paths: set[str]) -> dict[str, list[Decision]]:
    found = {}
    for path in paths:
        text = reader.read(f"{prefix}{path}")
        if text is not None:
            found[path] = parse_decisions(path, text)
    return found


def compare(repository: Repository, base: str, head: str, rules: list[KnownRule]) -> ChangesResult:
    started = time.perf_counter()
    base_commit = repository.resolve(base)
    changes = repository.changes(base_commit, head)
    prefix = repository.prefix
    result = ChangesResult(base=base, head=head)

    def local(path: str) -> str | None:
        return path[len(prefix) :] if path.startswith(prefix) else None

    status = {p: code for path, code in changes.status.items() if (p := local(path)) is not None}
    result.changed_files = sorted(status)
    renames = {
        old: new
        for old_path, new_path in changes.renames.items()
        if (old := local(old_path)) is not None and (new := local(new_path)) is not None
    }
    old_paths = {p for p, code in status.items() if code not in (ADDED, RENAMED)} | set(renames)
    before_paths = {p for p in old_paths if supports(p)}
    after_paths = {p for p, code in status.items() if supports(p) and code != DELETED}
    base_reader = Reader(repository, base_commit)
    head_reader = repository.reader(head)
    before = _parsed(base_reader, prefix, before_paths)
    after = _parsed(head_reader, prefix, after_paths)

    # 1. Each rule where it is in the base.
    olds = [
        OldAnchor(
            (rule.id, n),
            anchor.path,
            anchor.symbol,
            anchor.kind,
            anchor.shape,
            anchor.value,
            anchor.sketch,
            anchor.start_line,
        )
        for rule in rules
        for n, anchor in enumerate(rule.anchors)
        if anchor.path in before
    ]
    at_base = reanchor(olds, before)
    result.unanchored = len(olds) - len(at_base)

    # 2. Every base decision of the touched files, to the head.
    keyed: dict[Hashable, tuple[str, Decision]] = {
        (path, d.start_line, d.end_line, d.shape): (path, d)
        for path, decisions in before.items()
        for d in decisions
    }
    followed = reanchor(
        [
            OldAnchor(key, path, d.symbol, d.kind, d.shape, d.value, d.sketch, d.start_line)
            for key, (path, d) in keyed.items()
        ],
        after,
        renames,
    )
    by_rule = {rule.id: rule for rule in rules}
    seen: set[tuple[str, Hashable]] = set()
    for anchor_key, match in at_base.items():
        rule_id = str(anchor_key[0])  # type: ignore[index]
        key = (match.path, match.decision.start_line, match.decision.end_line, match.decision.shape)
        if (rule_id, key) in seen:
            continue
        seen.add((rule_id, key))
        rule = by_rule[rule_id]
        then = followed.get(key)
        level: str | None
        if then is not None:
            if then.decision.value == match.decision.value:
                moved = then.path != renames.get(match.path, match.path)
                level = MOVED if moved else None
            else:
                level = REVIEW if then.how == FUZZY else CHANGED
        else:
            path = renames.get(match.path, match.path)
            text = head_reader.read(f"{prefix}{path}") if path in after else None
            alive = text is not None and _symbol_exists(text, path, match.decision.symbol)
            level = REVIEW if alive else REMOVED
        if level is None:
            continue
        result.rules.append(
            RuleChange(
                rule.id,
                rule.title,
                rule.status,
                level,
                Side.of(match.path, match.decision),
                Side.of(then.path, then.decision) if then is not None else None,
            )
        )
    order = {CHANGED: 0, REVIEW: 1, REMOVED: 2, MOVED: 3}
    result.rules.sort(key=lambda c: (order[c.level], c.before.path, c.before.start_line))

    claimed = {(m.path, m.decision.start_line, m.decision.end_line) for m in followed.values()}
    for path, decisions in sorted(after.items()):
        for decision in decisions:
            unseen = (path, decision.start_line, decision.end_line) not in claimed
            if unseen and len(result.new_decisions) < MAX_NEW:
                result.new_decisions.append((decision.kind, path, Side.of(path, decision)))
    result.seconds = time.perf_counter() - started
    return result


_NOTE_LEVELS: Final = {
    CHANGED: "Cambian",
    REVIEW: "Se editaron (revisar)",
    REMOVED: "Se retiran",
}


def release_notes(result: ChangesResult) -> str:
    """Business release notes (phase 7.2): what the rules decide differently, in Markdown."""
    lines = [f"# Cambios de negocio: {result.base} → {result.head}", ""]
    for level, heading in _NOTE_LEVELS.items():
        changes = [c for c in result.rules if c.level == level]
        if not changes:
            continue
        lines += [f"## {heading}", ""]
        for change in changes:
            line = f"- **{change.title}**"
            if level == CHANGED and change.after is not None:
                before = ", ".join(change.before.values) or "—"
                after = ", ".join(change.after.values) or "—"
                line += f": antes `{before}`, ahora `{after}`"
            lines.append(f"{line} ({change.before.path}:{change.before.start_line})")
        lines.append("")
    if result.new_decisions:
        lines += [
            "## Decisiones nuevas sin regla",
            "",
            *(f"- {side.path}:{side.start_line} ({kind}"
              f"{', ' + side.symbol if side.symbol else ''})"
              for kind, _path, side in result.new_decisions),
            "",
        ]  # fmt: skip
    if len(lines) == 2:
        lines += ["Ninguna regla cambia.", ""]
    return "\n".join(lines)
