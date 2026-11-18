"""`scrinium check`: does this change touch a verified rule? (phase 2.2, Annex B.5)

Deterministic and standalone: it reads the rule files of the repository (`.scrinium/rules/`),
asks Git which files changed, parses only those, and looks for each anchor of those files in the
new code with the same re-anchoring as the rule watch (`reanchor`, ADR-011). No database, no
model, no network.

Each anchor of a changed file ends at one of these levels:

- **changed** (error): the same decision with other values. The check fails, unless the same
  change also updates the rule's file (`.scrinium/rules/<id>.md`): then it is **updated** (note);
- **review** (warning): the decision was edited in place, or is gone while its function remains;
- **removed** (warning): neither the decision nor its function is there;
- **moved** (note): the same decision, same values, in another file. The rule still holds; its
  file points to the old place until it is exported again.

The same decision with the same values in the same file, wherever its lines went, is nothing.
"""

import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Final

from scrinium.application.check.git import (
    COMMITTED,
    DELETED,
    LOCAL,
    STAGED,
    Reader,
    Repository,
)
from scrinium.application.check.rule_files import (
    RULES_DIR,
    VERIFIED,
    FileAnchor,
    RuleFile,
    RuleFileError,
    parse,
)
from scrinium.application.check.tickets import TicketRef, change_tickets, current_branch
from scrinium.application.knowledge.anchoring import FUZZY, OldAnchor, reanchor
from scrinium.infrastructure.code_structure import parse_symbols
from scrinium.infrastructure.decisions import (
    DECISIONS_VERSION,
    Decision,
    anchor_for,
    parse_decisions,
    supports,
)

CHANGED: Final = "changed"
UPDATED: Final = "updated"
REVIEW: Final = "review"
REMOVED: Final = "removed"
MOVED: Final = "moved"

# The levels where the rule itself changes: those should name a ticket (phase 10.5).
RULE_CHANGES: Final = frozenset({CHANGED, UPDATED, REVIEW, REMOVED})

SEVERITY: Final = {
    CHANGED: "error",
    REVIEW: "warning",
    REMOVED: "warning",
    UPDATED: "note",
    MOVED: "note",
}


@dataclass(frozen=True)
class Finding:
    rule_id: str
    title: str
    level: str
    # Where the rule's decision was (as its file says), relative to the source root.
    anchor: FileAnchor
    # Where it is now, when it was found: (path, start, end, symbol).
    now: tuple[str, int, int, str | None] | None = None
    # The tickets the change names for it: its file's commits and the branch (phase 10.5).
    tickets: tuple[TicketRef, ...] = ()

    @property
    def needs_ticket(self) -> bool:
        """A rule that changes, is edited or is retired should say why; a move need not."""
        return self.level in RULE_CHANGES

    @property
    def severity(self) -> str:
        return SEVERITY[self.level]

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.rule_id, self.anchor.path, self.anchor.start_line)

    @property
    def message(self) -> str:
        where = f"{self.anchor.path}:{self.anchor.start_line}"
        if self.level == CHANGED:
            return (
                f"Cambia una regla verificada: «{self.title}». La decisión de {where} sigue "
                "ahí con otros valores. Si el cambio es intencionado, revisa la regla en "
                "Scrinium y exporta su archivo en este mismo cambio."
            )
        if self.level == UPDATED:
            return f"La regla «{self.title}» cambia y su archivo se actualiza en este cambio."
        if self.level == REVIEW:
            return (
                f"Revisar la regla verificada «{self.title}»: la decisión de {where} se editó "
                "o ya no se encuentra, aunque su función sigue."
            )
        if self.level == REMOVED:
            return f"La regla verificada «{self.title}» se retira: su código ({where}) ya no está."
        moved = self.now[0] if self.now else "otro archivo"
        return f"La regla «{self.title}» se movió de {self.anchor.path} a {moved}, sin cambios."


@dataclass
class CheckResult:
    base: str
    head: str
    changed_files: int
    rules: int
    anchors_checked: int
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    # Where the source sits in the repository ("" at the top): reports give paths from the top.
    prefix: str = ""
    strict: bool = False
    # Anchors from another extractor version whose decision the base commit no longer shows.
    stale: int = 0
    # Optional explanations by a model (phase 2.4), by finding key: they never change `failed`.
    explanations: dict[tuple[str, str, int], str] = field(default_factory=dict)
    # Fail when a rule changes and nothing in the change names a ticket (phase 10.5).
    require_ticket: bool = False
    branch: str | None = None

    @property
    def without_ticket(self) -> list[Finding]:
        """Rules this change alters without naming any ticket: a governance signal."""
        return [f for f in self.findings if f.needs_ticket and not f.tickets]

    @property
    def failed(self) -> bool:
        failing = {CHANGED, REVIEW, REMOVED} if self.strict else {CHANGED}
        if self.require_ticket and self.without_ticket:
            return True
        return bool(self.errors) or any(f.level in failing for f in self.findings)


def load_rules(
    paths: list[str], read: Callable[[str], str | None]
) -> tuple[list[RuleFile], list[str]]:
    """Parses the given rule files; a broken file is an error, never skipped silently."""
    rules: list[RuleFile] = []
    errors: list[str] = []
    for path in paths:
        text = read(path)
        if text is None:
            continue
        try:
            rule = parse(text, path)
        except RuleFileError as error:
            errors.append(str(error))
            continue
        if PurePosixPath(path).name != rule.file_name:
            errors.append(f"{path}:1: el archivo debe llamarse {rule.file_name}")
            continue
        rules.append(rule)
    return rules, errors


def _symbol_exists(text: str, path: str, symbol: str | None) -> bool:
    if symbol is None:
        return False
    return any(s.qualified_name == symbol for s in parse_symbols(path, text))


def _refresh(
    reader: Reader, prefix: str, anchor: FileAnchor, parsed: dict[str, list[Decision] | None]
) -> FileAnchor | None:
    """The anchor fingerprinted again from the base; `parsed` keeps each base file's decisions."""
    if anchor.path not in parsed:
        text = reader.read(f"{prefix}{anchor.path}")
        parsed[anchor.path] = None if text is None else parse_decisions(anchor.path, text)
    decisions = parsed[anchor.path]
    if decisions is None:
        return None
    found = anchor_for(decisions, anchor.start_line, anchor.end_line)
    if found is None:
        return None
    return replace(
        anchor, symbol=found.symbol, kind=found.kind, start_line=found.start_line,
        end_line=found.end_line, shape=found.shape, value=found.value, sketch=found.sketch,
        decisions=DECISIONS_VERSION,
    )  # fmt: skip


def check(
    repository: Repository,
    base: str,
    *,
    include_local: bool = False,
    staged: bool = False,
    strict: bool = False,
    require_ticket: bool = False,
    branch: str | None = None,
) -> CheckResult:
    started = time.perf_counter()
    since = repository.merge_base(base)
    head = STAGED if staged else LOCAL if include_local else COMMITTED
    changes = repository.changes(since, head)
    reader = repository.reader(head)
    prefix = repository.prefix
    rules_dir = f"{prefix}{RULES_DIR.as_posix()}"
    rule_paths = [
        f"{rules_dir}/{name}" for name in reader.list_dir(rules_dir) if name.endswith(".md")
    ]
    rules, errors = load_rules(rule_paths, reader.read)
    verified = [rule for rule in rules if rule.status == VERIFIED]
    result = CheckResult(
        base=base, head=head,
        changed_files=len(changes.status), rules=len(verified), anchors_checked=0,
        errors=errors, prefix=prefix, strict=strict, require_ticket=require_ticket,
        branch=branch if branch is not None else current_branch(repository),
    )  # fmt: skip

    def local(path: str) -> str | None:
        """A repository path as the source sees it; None outside the source."""
        return path[len(prefix) :] if path.startswith(prefix) else None

    touched = {p for p in map(local, changes.touched) if p is not None}
    acknowledged = {
        PurePosixPath(path).stem
        for path, code in changes.status.items()
        if path.startswith(f"{rules_dir}/") and code != DELETED
    }
    old: list[OldAnchor] = []
    owners: dict[Hashable, tuple[RuleFile, FileAnchor]] = {}
    base_reader = Reader(repository, since)
    base_decisions: dict[str, list[Decision] | None] = {}
    for rule in verified:
        for number, anchor in enumerate(rule.anchors):
            if anchor.path not in touched:
                continue
            if anchor.decisions != DECISIONS_VERSION:
                # Fingerprinted by another version of the decision extractor: computed again
                # from the base commit, where the rule file was written against that code.
                refreshed = _refresh(base_reader, prefix, anchor, base_decisions)
                if refreshed is None:
                    result.stale += 1
                    continue
                anchor = refreshed
            key = (rule.id, number)
            owners[key] = (rule, anchor)
            old.append(
                OldAnchor(
                    key=key, path=anchor.path, symbol=anchor.symbol, kind=anchor.kind,
                    shape=anchor.shape, value=anchor.value, sketch=anchor.sketch,
                    start_line=anchor.start_line,
                )
            )  # fmt: skip
    result.anchors_checked = len(old)
    if not old:
        result.seconds = time.perf_counter() - started
        return result

    renames: dict[str, str] = {}
    for old_path, new_path in changes.renames.items():
        old_local, new_local = local(old_path), local(new_path)
        if old_local is not None and new_local is not None:
            renames[old_local] = new_local
    texts: dict[str, str] = {}
    decisions: dict[str, list[Decision]] = {}
    for path, code in changes.status.items():
        source_path = local(path)
        if source_path is None or code == DELETED or not supports(source_path):
            continue
        text = reader.read(path)
        if text is None:
            continue
        texts[source_path] = text
        decisions[source_path] = parse_decisions(source_path, text)

    matches = reanchor(old, decisions, renames)
    for anchor_key, (rule, anchor) in owners.items():
        match = matches.get(anchor_key)
        level: str | None
        now = None
        if match is not None:
            found = match.decision
            now = (match.path, found.start_line, found.end_line, found.symbol)
            if found.value == anchor.value:
                level = MOVED if match.path != renames.get(anchor.path, anchor.path) else None
            elif match.how == FUZZY:
                level = REVIEW
            else:
                level = UPDATED if rule.id in acknowledged else CHANGED
        else:
            current = texts.get(renames.get(anchor.path, anchor.path))
            alive = current is not None and _symbol_exists(
                current, renames.get(anchor.path, anchor.path), anchor.symbol
            )
            level = REVIEW if alive else REMOVED
        if level is not None:
            result.findings.append(Finding(rule.id, rule.title or "", level, anchor, now))
    _attach_tickets(repository, since, head, result)
    order = {CHANGED: 0, REVIEW: 1, REMOVED: 2, UPDATED: 3, MOVED: 4}
    result.findings.sort(key=lambda f: (order[f.level], f.anchor.path, f.anchor.start_line))
    result.seconds = time.perf_counter() - started
    return result


def _attach_tickets(repository: Repository, since: str, head: str, result: CheckResult) -> None:
    """Each finding's tickets, read from Git only (no database, no network)."""
    if not result.findings:
        return
    prefix = result.prefix
    paths: set[str] = set()
    for finding in result.findings:
        paths.add(f"{prefix}{finding.anchor.path}")
        if finding.now is not None:
            paths.add(f"{prefix}{finding.now[0]}")
    found = change_tickets(repository, since, head, paths, result.branch)
    result.findings = [
        replace(
            finding,
            tickets=tuple(
                found.for_paths(
                    f"{prefix}{finding.anchor.path}",
                    *((f"{prefix}{finding.now[0]}",) if finding.now is not None else ()),
                )
            ),
        )
        for finding in result.findings
    ]
